//! STFT / ISTFT（rustfft + 周期 Hann 窗 + OLA 归一化重建）。

use num_complex::Complex;
use rustfft::FftPlanner;

/// 频谱：帧 × 频 bin（只保留 `n_fft/2+1` 个共轭对称 bin）。
pub type Spec = Vec<Vec<Complex<f32>>>;

pub const N_FFT: usize = 2048;
pub const HOP: usize = 512;

/// 周期 Hann 窗（OLA 归一化友好）。
pub fn hann(n: usize) -> Vec<f32> {
    (0..n)
        .map(|i| 0.5 * (1.0 - (2.0 * std::f32::consts::PI * i as f32 / n as f32).cos()))
        .collect()
}

/// 信号 → 复数频谱（帧 × bin）。短信号自动补零到一帧。
pub fn stft(x: &[f32], n_fft: usize, hop: usize) -> Spec {
    let win = hann(n_fft);
    let mut planner = FftPlanner::new();
    let fft = planner.plan_fft_forward(n_fft);
    let n_bins = n_fft / 2 + 1;
    if x.is_empty() {
        return vec![vec![Complex::default(); n_bins]];
    }
    let n_frames = if x.len() <= n_fft {
        1
    } else {
        1 + (x.len() - n_fft) / hop
    };
    let mut out = Vec::with_capacity(n_frames);
    for i in 0..n_frames {
        let start = i * hop;
        let mut buf = vec![Complex::<f32>::default(); n_fft];
        for j in 0..n_fft {
            let s = x.get(start + j).copied().unwrap_or(0.0);
            buf[j].re = s * win[j];
        }
        fft.process(&mut buf);
        out.push(buf[..n_bins].to_vec());
    }
    out
}

/// 频谱 → 信号（共轭对称补全 + IFFT + 加权 OLA 归一化）。
/// `out_len` 为期望输出长度（通常等于原信号长度）。
pub fn istft(spec: &Spec, n_fft: usize, hop: usize, out_len: usize) -> Vec<f32> {
    let win = hann(n_fft);
    let mut planner = FftPlanner::new();
    let ifft = planner.plan_fft_inverse(n_fft);
    let mut y = vec![0f32; out_len];
    let mut w = vec![0f32; out_len];
    let scale = 1.0 / n_fft as f32;
    for (i, frame) in spec.iter().enumerate() {
        let mut buf = vec![Complex::<f32>::default(); n_fft];
        for (b, v) in frame.iter().enumerate() {
            buf[b] = *v;
            if b > 0 && b < n_fft - b {
                buf[n_fft - b] = v.conj();
            }
        }
        ifft.process(&mut buf);
        let start = i * hop;
        for j in 0..n_fft {
            let idx = start + j;
            if idx >= out_len {
                break;
            }
            y[idx] += buf[j].re * scale * win[j];
            w[idx] += win[j] * win[j];
        }
    }
    y.iter()
        .zip(w.iter())
        .map(|(&y, &w)| if w > 1e-8 { y / w } else { 0.0 })
        .collect()
}

/// 复数频谱 → 幅度谱（帧 × bin）。
pub fn magnitude(spec: &Spec) -> Vec<Vec<f32>> {
    spec.iter()
        .map(|f| f.iter().map(|c| c.norm()).collect())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// STFT→ISTFT 重建能量基本守恒（窗归一化 OLA）。
    #[test]
    fn stft_istft_roundtrip() {
        let sr = 22050u32;
        let x: Vec<f32> = (0..sr as usize) // 1s
            .map(|i| {
                let t = i as f32 / sr as f32;
                (2.0 * std::f32::consts::PI * 220.0 * t).sin() * 0.5
            })
            .collect();
        let spec = stft(&x, N_FFT, HOP);
        let y = istft(&spec, N_FFT, HOP, x.len());
        assert_eq!(y.len(), x.len());
        // 跳过首尾边界帧（窗未完全覆盖）
        let margin = N_FFT;
        let mut max_err = 0f32;
        for i in margin..(x.len() - margin) {
            max_err = max_err.max((x[i] - y[i]).abs());
        }
        assert!(max_err < 1e-3, "max err {max_err}");
    }

    #[test]
    fn short_signal_single_frame() {
        let x = vec![0.1f32; 100];
        let spec = stft(&x, N_FFT, HOP);
        assert_eq!(spec.len(), 1);
        assert_eq!(spec[0].len(), N_FFT / 2 + 1);
    }
}

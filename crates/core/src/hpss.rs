//! HPSS：谐波/打击乐分离（时间轴/频率轴中值滤波软掩码）+ 低频带切分。

use crate::stft::Spec;

/// 中值滤波核大小（帧/频 bin）。
const KERNEL: usize = 17;

fn median(v: &mut [f32]) -> f32 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

/// 反射边界索引。
fn reflect(i: i64, n: i64) -> usize {
    if i < 0 {
        (-i - 1) as usize
    } else if i >= n {
        (2 * n - i - 1) as usize
    } else {
        i as usize
    }
}

/// 沿时间轴中值（谐波成分在时间上稳定 → 保留）。
pub fn median_filter_time(mag: &[Vec<f32>], k: usize) -> Vec<Vec<f32>> {
    let n_frames = mag.len();
    let bins = mag.first().map_or(0, |f| f.len());
    let half = k as i64 / 2;
    let mut out = vec![vec![0f32; bins]; n_frames];
    let mut buf = Vec::with_capacity(k);
    for b in 0..bins {
        for t in 0..n_frames as i64 {
            buf.clear();
            for i in (t - half)..=(t + half) {
                buf.push(mag[reflect(i, n_frames as i64)][b]);
            }
            out[t as usize][b] = median(&mut buf);
        }
    }
    out
}

/// 沿频率轴中值（瞬态在频率上宽带 → 保留）。
pub fn median_filter_freq(mag: &[Vec<f32>], k: usize) -> Vec<Vec<f32>> {
    let n_frames = mag.len();
    let bins = mag.first().map_or(0, |f| f.len());
    if bins == 0 {
        return mag.to_vec();
    }
    let half = k as i64 / 2;
    let mut out = vec![vec![0f32; bins]; n_frames];
    let mut buf = Vec::with_capacity(k);
    for t in 0..n_frames {
        for b in 0..bins as i64 {
            buf.clear();
            for i in (b - half)..=(b + half) {
                buf.push(mag[t][reflect(i, bins as i64)]);
            }
            out[t][b as usize] = median(&mut buf);
        }
    }
    out
}

/// HPSS 软掩码（功率 2 软分配）。返回 (harmonic_mask, percussive_mask)。
pub fn hpss_masks(mag: &[Vec<f32>]) -> (Vec<Vec<f32>>, Vec<Vec<f32>>) {
    let h = median_filter_time(mag, KERNEL);
    let p = median_filter_freq(mag, KERNEL);
    let mut mh = vec![vec![0f32; p[0].len()]; mag.len()];
    for (t, (hrow, prow)) in h.iter().zip(p.iter()).enumerate() {
        for (m, (hv, pv)) in mh[t].iter_mut().zip(hrow.iter().zip(prow.iter())) {
            let h2 = hv * hv;
            let p2 = pv * pv;
            *m = h2 / (h2 + p2 + 1e-12);
        }
    }
    let mp = mh
        .iter()
        .map(|row| row.iter().map(|m| 1.0 - m).collect())
        .collect();
    (mh, mp)
}

/// 低频带软掩码（f<lo 全保留，f>hi 全去除，中间平滑过渡）。用于 bass 切分。
pub fn lowband_mask(n_bins: usize, n_fft: usize, sr: u32, lo_hz: f32, hi_hz: f32) -> Vec<f32> {
    let bin_hz = sr as f32 / n_fft as f32;
    (0..n_bins)
        .map(|b| {
            let f = b as f32 * bin_hz;
            if f <= lo_hz {
                1.0
            } else if f >= hi_hz {
                0.0
            } else {
                // smoothstep
                let x = (f - lo_hz) / (hi_hz - lo_hz);
                1.0 - (3.0 * x * x - 2.0 * x * x * x)
            }
        })
        .collect()
}

/// 掩码逐 bin 乘到复数频谱。
pub fn apply_mask(spec: &Spec, mask: &[Vec<f32>]) -> Spec {
    spec.iter()
        .zip(mask.iter())
        .map(|(f, m)| f.iter().zip(m.iter()).map(|(c, w)| c * w).collect())
        .collect()
}

/// 对每个 bin 应用行向量掩码（低频带切分）。
pub fn apply_bin_mask(spec: &Spec, mask: &[f32]) -> Spec {
    spec.iter()
        .map(|f| f.iter().zip(mask.iter()).map(|(c, w)| c * w).collect())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hpss_separates_click_vs_tone() {
        // 构造幅度谱：稳定正弦（时间上恒定）vs 宽带瞬态（仅 1 帧）
        let bins = 64;
        let frames = 40;
        let mut mag = vec![vec![0.01f32; bins]; frames];
        for f in &mut mag {
            f[10] = 1.0; // 稳定谱线 → 谐波
        }
        mag[20][..].copy_from_slice(&vec![1.0f32; bins]); // 一帧宽带 → 打击乐
        let (mh, mp) = hpss_masks(&mag);
        // 稳定帧：谐波掩码高
        assert!(mh[5][10] > 0.9, "harm mask {}", mh[5][10]);
        // 瞬态帧：打击乐掩码在大多数 bin 上占优
        let perc_dominant = (0..bins).filter(|&b| mp[20][b] > 0.5).count();
        assert!(perc_dominant > bins / 2, "{perc_dominant}/{bins}");
    }

    #[test]
    fn lowband_mask_shape() {
        let m = lowband_mask(1025, 2048, 22050, 140.0, 260.0);
        assert!((m[0] - 1.0).abs() < 1e-6);
        assert!(m.last().unwrap() < &0.001);
    }

    #[test]
    fn median_filters() {
        let mag = vec![
            vec![1.0f32, 5.0, 1.0],
            vec![2.0, 5.0, 2.0],
            vec![9.0, 5.0, 3.0],
        ];
        let t = median_filter_time(&mag, 3);
        assert_eq!(t[1][0], 2.0); // [1,2,9] → 2
        let f = median_filter_freq(&mag, 3);
        assert_eq!(f[0][1], 1.0); // [1,5,1] → 1（脉冲被压掉）
    }
}

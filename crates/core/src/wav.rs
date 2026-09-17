//! WAV 读写。支持 16/24/32 位整数与 32 位浮点，统一转换为 f32。

use std::path::Path;

use crate::error::{EngineError, Result};

/// 解码后的音频：每声道一个采样序列。
#[derive(Debug, Clone)]
pub struct WavData {
    pub sample_rate: u32,
    pub channels: Vec<Vec<f32>>,
}

impl WavData {
    pub fn channels(&self) -> usize {
        self.channels.len()
    }

    pub fn len_samples(&self) -> usize {
        self.channels.first().map_or(0, |c| c.len())
    }

    /// 所有声道求平均 → 单声道（分析用）。
    pub fn mono(&self) -> Vec<f32> {
        if self.channels.is_empty() {
            return Vec::new();
        }
        let n = self.len_samples();
        let mut out = vec![0f32; n];
        for ch in &self.channels {
            for (o, s) in out.iter_mut().zip(ch.iter()) {
                *o += s;
            }
        }
        let inv = 1.0 / self.channels.len() as f32;
        for o in &mut out {
            *o *= inv;
        }
        out
    }
}

/// 读取 WAV 文件（任意支持的位深），归一化为 f32。
pub fn load(path: impl AsRef<Path>) -> Result<WavData> {
    let mut reader = hound::WavReader::open(path.as_ref())?;
    let spec = reader.spec();
    if spec.channels == 0 || spec.channels > 8 {
        return Err(EngineError::Wav(format!("不支持 {} 声道", spec.channels)));
    }
    let n = reader.duration() as usize; // 每声道采样数
    let nch = spec.channels as usize;
    let mut channels = vec![Vec::with_capacity(n); nch];
    let mut idx = 0usize;
    let mut push = |channels: &mut [Vec<f32>], sample: f32| {
        channels[idx % nch].push(sample);
        idx += 1;
    };
    match (spec.sample_format, spec.bits_per_sample) {
        (hound::SampleFormat::Float, 32) => {
            for s in reader.samples::<f32>() {
                let s = s.map_err(|e| EngineError::Wav(e.to_string()))?;
                push(&mut channels, s);
            }
        }
        (hound::SampleFormat::Int, 16) => {
            for s in reader.samples::<i16>() {
                let s = s.map_err(|e| EngineError::Wav(e.to_string()))?;
                push(&mut channels, s as f32 / i16::MAX as f32);
            }
        }
        (hound::SampleFormat::Int, 24) => {
            for s in reader.samples::<i32>() {
                let s = s.map_err(|e| EngineError::Wav(e.to_string()))?;
                push(&mut channels, s as f32 / 8_388_607.0);
            }
        }
        (hound::SampleFormat::Int, 32) => {
            for s in reader.samples::<i32>() {
                let s = s.map_err(|e| EngineError::Wav(e.to_string()))?;
                push(&mut channels, s as f32 / i32::MAX as f32);
            }
        }
        (fmt, bits) => {
            return Err(EngineError::Wav(format!(
                "不支持的 WAV 格式：{bits}bit {fmt:?}"
            )));
        }
    }
    Ok(WavData {
        sample_rate: spec.sample_rate,
        channels,
    })
}

/// 写 16 位 PCM WAV（输出分轨用，兼容性最好）。
pub fn save(path: impl AsRef<Path>, data: &WavData) -> Result<()> {
    let spec = hound::WavSpec {
        channels: data.channels.len() as u16,
        sample_rate: data.sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(path.as_ref(), spec)?;
    let n = data.len_samples();
    for i in 0..n {
        for ch in &data.channels {
            let s = ch.get(i).copied().unwrap_or(0.0).clamp(-1.0, 1.0);
            writer.write_sample((s * i16::MAX as f32) as i16)?;
        }
    }
    writer.finalize()?;
    Ok(())
}

/// 生成合成正弦（测试夹具用）。
pub fn sine(freq: f32, dur_sec: f32, sr: u32, amp: f32) -> Vec<f32> {
    let n = (dur_sec * sr as f32) as usize;
    (0..n)
        .map(|i| {
            let t = i as f32 / sr as f32;
            amp * (2.0 * std::f32::consts::PI * freq * t).sin()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wav_roundtrip() {
        let dir = std::env::temp_dir().join("mlcore-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("t.wav");
        let data = WavData {
            sample_rate: 22050,
            channels: vec![sine(220.0, 0.1, 22050, 0.5)],
        };
        save(&p, &data).unwrap();
        let back = load(&p).unwrap();
        assert_eq!(back.sample_rate, 22050);
        assert_eq!(back.channels(), 1);
        assert_eq!(back.len_samples(), data.len_samples());
        for (a, b) in back.channels[0].iter().zip(data.channels[0].iter()) {
            assert!((a - b).abs() < 1e-3, "{a} vs {b}");
        }
    }

    #[test]
    fn mono_mixed() {
        let data = WavData {
            sample_rate: 8000,
            channels: vec![vec![0.2; 100], vec![0.4; 100]],
        };
        let m = data.mono();
        assert_eq!(m.len(), 100);
        assert!((m[0] - 0.3).abs() < 1e-6);
    }
}

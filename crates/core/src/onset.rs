//! 频谱通量 onset 检测 + 鼓件频带分类（kick/snare/hihat → GM）。

use crate::engine::Note;

/// GM 鼓组音高：36 kick / 38 snare / 42 closed hihat。
pub const GM_KICK: u8 = 36;
pub const GM_SNARE: u8 = 38;
pub const GM_HIHAT: u8 = 42;

/// 频带边界（Hz）。
const BAND_LOW: f32 = 150.0;
const BAND_MID: f32 = 5000.0;
/// onset 后音符固定时长（秒）。
const DRUM_NOTE_DUR: f32 = 0.05;

/// 频谱通量（半波整流的帧间幅度增量总和）。
pub fn spectral_flux(mag: &[Vec<f32>]) -> Vec<f32> {
    let mut flux = vec![0f32; mag.len()];
    for t in 1..mag.len() {
        let mut s = 0f32;
        for (a, b) in mag[t].iter().zip(mag[t - 1].iter()) {
            let d = a - b;
            if d > 0.0 {
                s += d;
            }
        }
        flux[t] = s;
    }
    flux
}

/// 峰点检测：局部最大 + 高于（均值 + delta×标准差）。
pub fn pick_peaks(flux: &[f32], delta_sigma: f32, wait: usize) -> Vec<usize> {
    if flux.len() < 5 {
        return Vec::new();
    }
    let mean = flux.iter().sum::<f32>() / flux.len() as f32;
    let var = flux.iter().map(|f| (f - mean) * (f - mean)).sum::<f32>() / flux.len() as f32;
    let thresh = mean + delta_sigma * var.sqrt();
    let mut peaks = Vec::new();
    let mut last = 0usize;
    for t in 2..flux.len() - 2 {
        if flux[t] < thresh {
            continue;
        }
        let is_max = (flux[t - 2..=t + 2]).iter().all(|f| *f <= flux[t]);
        if is_max && (peaks.is_empty() || t - last >= wait) {
            peaks.push(t);
            last = t;
        }
    }
    peaks
}

/// 某帧的频带能量占比 → 鼓件判定。
fn classify_band(mag_frame: &[f32], n_fft: usize, sr: u32) -> Option<u8> {
    let bin_hz = sr as f32 / n_fft as f32;
    let (mut e_low, mut e_mid, mut e_high) = (0f32, 0f32, 0f32);
    for (b, m) in mag_frame.iter().enumerate() {
        let f = b as f32 * bin_hz;
        let e = m * m;
        if f < BAND_LOW {
            e_low += e;
        } else if f < BAND_MID {
            e_mid += e;
        } else {
            e_high += e;
        }
    }
    let total = e_low + e_mid + e_high;
    if total < 1e-9 {
        return None;
    }
    let note = if e_low >= e_mid && e_low >= e_high {
        GM_KICK
    } else if e_mid >= e_high {
        GM_SNARE
    } else {
        GM_HIHAT
    };
    Some(note)
}

/// 打击乐分轨（幅度谱）→ 鼓组音符。
pub fn drum_notes(perc_mag: &[Vec<f32>], n_fft: usize, hop: usize, sr: u32) -> Vec<Note> {
    let flux = spectral_flux(perc_mag);
    let peaks = pick_peaks(&flux, 1.5, 8);
    let hop_sec = hop as f32 / sr as f32;
    let mut notes = Vec::with_capacity(peaks.len());
    for &t in &peaks {
        let Some(midi) = classify_band(&perc_mag[t], n_fft, sr) else {
            continue;
        };
        // 强度 → 力度
        let rel = flux[t] / (flux.iter().cloned().fold(0f32, f32::max) + 1e-12);
        notes.push(Note {
            onset: t as f32 * hop_sec,
            dur: DRUM_NOTE_DUR,
            midi,
            vel: (70.0 + 50.0 * rel.min(1.0)) as u8,
        });
    }
    notes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn peaks_find_impulses() {
        let mut flux = vec![0.1f32; 100];
        flux[20] = 5.0;
        flux[50] = 3.0;
        flux[52] = 4.0; // 距 50 仅 2 帧 < wait 8 → 抑制
        let peaks = pick_peaks(&flux, 1.0, 8);
        assert_eq!(peaks, vec![20, 52]);
    }

    #[test]
    fn band_classify() {
        let n_fft = 2048;
        let sr = 22050;
        let bins = n_fft / 2 + 1;
        let mut frame = vec![0f32; bins];
        frame[3] = 1.0; // ~32Hz → kick
        assert_eq!(classify_band(&frame, n_fft, sr), Some(GM_KICK));
        let mut frame = vec![0f32; bins];
        frame[300] = 1.0; // ~3200Hz → snare 区
        assert_eq!(classify_band(&frame, n_fft, sr), Some(GM_SNARE));
        let mut frame = vec![0f32; bins];
        frame[800] = 1.0; // ~8.6kHz → hihat
        assert_eq!(classify_band(&frame, n_fft, sr), Some(GM_HIHAT));
    }
}

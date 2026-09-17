//! YIN 基频追踪 + 帧级音高 → 音符分组。

use crate::engine::Note;

const YIN_THRESH: f32 = 0.15;
pub const MIN_NOTE_SEC: f32 = 0.06;

/// 频率 → MIDI 音高（可含分数）。
pub fn hz_to_midi(f: f32) -> f32 {
    69.0 + 12.0 * (f / 440.0).log2()
}

/// 对单个分析帧做 YIN（帧长 ≥ 2×(sr/fmin) 最佳）。
/// 返回 None 表示该帧无周期性（未发声/噪声）。
pub fn yin_frame(x: &[f32], sr: u32, fmin: f32, fmax: f32) -> Option<f32> {
    let w = x.len();
    let tau_max = ((sr as f32 / fmin) as usize).min(w / 2);
    let tau_min = ((sr as f32 / fmax) as usize).max(2);
    if tau_max <= tau_min + 2 {
        return None;
    }
    // 差分函数
    let mut d = vec![0f32; tau_max + 1];
    for tau in 1..=tau_max {
        let mut s = 0f32;
        for j in 0..(w - tau_max) {
            let diff = x[j] - x[j + tau];
            s += diff * diff;
        }
        d[tau] = s;
    }
    // 累积均值归一化
    let mut cmnd = vec![0f32; tau_max + 1];
    let mut cum = 0f32;
    for tau in 1..=tau_max {
        cum += d[tau];
        cmnd[tau] = if cum > 0.0 {
            d[tau] * tau as f32 / cum
        } else {
            1.0
        };
    }
    // 阈值搜索：第一个低于阈值处，再走到局部最小
    let mut tau = tau_min;
    let mut found = false;
    while tau < tau_max {
        if cmnd[tau] < YIN_THRESH {
            while tau + 1 < tau_max && cmnd[tau + 1] < cmnd[tau] {
                tau += 1;
            }
            found = true;
            break;
        }
        tau += 1;
    }
    if !found {
        // 容错：全局最小足够低也算
        let (mut best, mut best_v) = (tau_min, f32::MAX);
        for (t, &c) in cmnd.iter().enumerate().take(tau_max).skip(tau_min) {
            if c < best_v {
                best_v = c;
                best = t;
            }
        }
        if best_v > 0.45 {
            return None;
        }
        tau = best;
    }
    // 抛物线插值
    let (x0, x1, x2) = (cmnd[tau - 1], cmnd[tau], cmnd[(tau + 1).min(tau_max)]);
    let denom = x0 - 2.0 * x1 + x2;
    let shift = if denom.abs() > 1e-12 {
        0.5 * (x0 - x2) / denom
    } else {
        0.0
    };
    let tau_f = tau as f32 + shift.clamp(-1.0, 1.0);
    if tau_f <= 0.0 {
        return None;
    }
    let f0 = sr as f32 / tau_f;
    (fmin..=fmax).contains(&f0).then_some(f0)
}

/// 整条信号逐帧 YIN。帧长 2048、hop 同 STFT。
pub fn yin_track(
    x: &[f32],
    sr: u32,
    frame: usize,
    hop: usize,
    fmin: f32,
    fmax: f32,
) -> Vec<Option<f32>> {
    if x.len() < frame {
        return vec![yin_frame(x, sr, fmin, fmax)];
    }
    (0..=(x.len() - frame))
        .step_by(hop)
        .map(|i| yin_frame(&x[i..i + frame], sr, fmin, fmax))
        .collect()
}

/// 帧级 f0 → 音符：连续同音高（±0.5 semitone 内取整一致）聚组。
pub fn f0_to_notes(f0s: &[Option<f32>], hop_sec: f32) -> Vec<Note> {
    #[derive(Clone, Copy)]
    struct Run {
        start: usize,
        end: usize, // exclusive
        midi: u8,
    }
    let mut runs: Vec<Run> = Vec::new();
    let mut cur: Option<Run> = None;
    let mut unvoiced_gap = 0usize;
    for (i, f0) in f0s.iter().enumerate() {
        match f0 {
            Some(f) => {
                let midi = hz_to_midi(*f).round().clamp(0.0, 127.0) as u8;
                unvoiced_gap = 0;
                match &mut cur {
                    Some(r) if r.midi == midi => r.end = i + 1,
                    Some(_) | None => {
                        if let Some(r) = cur.take() {
                            runs.push(r);
                        }
                        cur = Some(Run {
                            start: i,
                            end: i + 1,
                            midi,
                        });
                    }
                }
            }
            None => {
                // 允许 1 帧的空洞延续当前音符
                unvoiced_gap += 1;
                if unvoiced_gap > 1 {
                    if let Some(r) = cur.take() {
                        runs.push(r);
                    }
                } else if let Some(r) = &mut cur {
                    r.end = i + 1;
                }
            }
        }
    }
    if let Some(r) = cur.take() {
        runs.push(r);
    }
    runs.into_iter()
        .filter(|r| (r.end - r.start) as f32 * hop_sec >= MIN_NOTE_SEC)
        .map(|r| Note {
            onset: r.start as f32 * hop_sec,
            dur: (r.end - r.start) as f32 * hop_sec,
            midi: r.midi,
            vel: 100,
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wav::sine;

    const SR: u32 = 22050;

    #[test]
    fn yin_detects_220hz() {
        let x = sine(220.0, 0.2, SR, 0.6);
        let f0 = yin_frame(&x[..2048], SR, 60.0, 1200.0);
        let f0 = f0.expect("应有 f0");
        assert!((f0 - 220.0).abs() < 2.0, "f0={f0}");
    }

    #[test]
    fn yin_silence_none() {
        let x = vec![0f32; 2048];
        assert!(yin_frame(&x, SR, 60.0, 1200.0).is_none());
    }

    #[test]
    fn notes_two_pitches() {
        // 110Hz 0.2s + 220Hz 0.2s，帧长 2048/hop 512
        let mut x = sine(110.0, 0.2, SR, 0.6);
        x.extend(sine(220.0, 0.2, SR, 0.6));
        let f0s = yin_track(&x, SR, 2048, 512, 60.0, 1200.0);
        let notes = f0_to_notes(&f0s, 512.0 / SR as f32);
        assert_eq!(notes.len(), 2, "{notes:?}");
        assert_eq!(notes[0].midi, 45); // A2 = 110Hz
        assert_eq!(notes[1].midi, 57); // A3 = 220Hz
    }
}

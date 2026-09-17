//! 管线编排：加载 → HPSS 分离 + 频带切分 → 逐轨转录 → 产物落盘。

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::{EngineError, Result};
use crate::hpss;
use crate::midi::write_midi;
use crate::onset::drum_notes;
use crate::pitch::{f0_to_notes, yin_track};
use crate::stft::{self, Spec};
use crate::wav::{self, WavData};

/// 一条音符（秒制时间轴）。
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Note {
    pub onset: f32,
    pub dur: f32,
    pub midi: u8,
    pub vel: u8,
}

/// 阶段报告。
#[derive(Debug, Clone, Serialize)]
pub struct StageReport {
    pub name: String,
    pub backend: String,
    pub seconds: f32,
    pub outputs: Vec<String>,
}

/// 管线执行报告（JSON 可序列化，直接作为任务 result）。
#[derive(Debug, Clone, Serialize)]
pub struct PipelineReport {
    pub input: String,
    pub output_dir: String,
    pub schema: Vec<String>,
    pub stem_files: Vec<String>,
    pub midi_files: BTreeMap<String, String>,
    pub notes_count: BTreeMap<String, usize>,
    pub stages: Vec<StageReport>,
    pub total_seconds: f32,
    pub warnings: Vec<String>,
}

/// 分轨预设。
pub fn schema_for(key: &str) -> Option<&'static [&'static str]> {
    match key {
        "2" => Some(&["harmonic", "percussive"]),
        "3" => Some(&["bass", "harmonic", "drums"]),
        _ => None,
    }
}

pub fn schemas() -> Vec<&'static str> {
    vec!["2", "3"]
}

/// 单音轨声部转录配置。
fn stem_pitch_range(stem: &str) -> Option<(f32, f32)> {
    match stem {
        "bass" => Some((35.0, 500.0)),
        "harmonic" | "melody" | "vocals" | "other" => Some((65.0, 1100.0)),
        _ => None,
    }
}

fn is_drum(stem: &str) -> bool {
    stem == "drums" || stem == "percussive"
}

/// 阶段事件（JSON）。phase ∈ start/end；skipped 表示该轨被跳过。
#[allow(clippy::too_many_arguments)]
fn emit(
    on_event: Option<&dyn Fn(serde_json::Value)>,
    phase: &str,
    index: usize,
    total: usize,
    stage: &str,
    extra: serde_json::Value,
) {
    if let Some(cb) = on_event {
        cb(serde_json::json!({
            "kind": "stage", "phase": phase, "stage": stage,
            "index": index, "total": total, "extra": extra,
        }));
    }
}

fn check_cancel(should_cancel: Option<&dyn Fn() -> bool>) -> Result<()> {
    if let Some(f) = should_cancel {
        if f() {
            return Err(EngineError::Cancelled);
        }
    }
    Ok(())
}

/// 分离结果：分轨名 → 音频。
pub struct Separation {
    pub stems: Vec<(String, WavData)>,
    /// 打击乐幅度谱（供 onset 检测复用）。
    pub perc_mag: Vec<Vec<f32>>,
    pub n_fft: usize,
    pub hop: usize,
    pub backend: &'static str,
}

/// HPSS 分离 + bass 频带切分。
pub fn separate(data: &WavData, schema: &[&str]) -> Separation {
    let n_fft = stft::N_FFT;
    let hop = stft::HOP;
    let need_bass = schema.contains(&"bass");
    let need_harmonic = schema.contains(&"harmonic");

    // 1) 各声道 STFT
    let specs: Vec<Spec> = data
        .channels
        .iter()
        .map(|c| stft::stft(c, n_fft, hop))
        .collect();
    // 2) 平均幅度谱 → HPSS 掩码
    let n_bins = n_fft / 2 + 1;
    let mut mean_mag = vec![vec![0f32; n_bins]; specs[0].len()];
    for spec in &specs {
        for (t, frame) in stft::magnitude(spec).iter().enumerate() {
            for (b, m) in frame.iter().enumerate() {
                mean_mag[t][b] += m;
            }
        }
    }
    let inv = 1.0 / specs.len() as f32;
    for frame in &mut mean_mag {
        for m in frame {
            *m *= inv;
        }
    }
    let (mh, mp) = hpss::hpss_masks(&mean_mag);
    let low = if need_bass {
        hpss::lowband_mask(n_bins, n_fft, data.sample_rate, 140.0, 260.0)
    } else {
        Vec::new()
    };

    // 掩码组合 → 各声道 ISTFT
    let build = |mask: &[Vec<f32>], bin_mask: &[f32]| -> WavData {
        let channels = specs
            .iter()
            .map(|spec| {
                let s = hpss::apply_mask(spec, mask);
                if bin_mask.is_empty() {
                    stft::istft(&s, n_fft, hop, data.len_samples())
                } else {
                    stft::istft(
                        &hpss::apply_bin_mask(&s, bin_mask),
                        n_fft,
                        hop,
                        data.len_samples(),
                    )
                }
            })
            .collect();
        WavData {
            sample_rate: data.sample_rate,
            channels,
        }
    };

    let mut stems = Vec::new();
    if need_bass {
        stems.push(("bass".to_string(), build(&mh, &low)));
    }
    if need_harmonic {
        let bin_mask: Vec<f32> = if need_bass {
            low.iter().map(|m| 1.0 - m).collect()
        } else {
            Vec::new()
        };
        stems.push(("harmonic".to_string(), build(&mh, &bin_mask)));
    }
    let mut perc_mag = Vec::new();
    if let Some(drum_name) = schema.iter().find(|s| is_drum(s)) {
        perc_mag = hpss::apply_mask(&specs[0], &mp)
            .iter()
            .map(|f| f.iter().map(|c| c.norm()).collect())
            .collect();
        stems.push((drum_name.to_string(), build(&mp, &[])));
    }
    Separation {
        stems,
        perc_mag,
        n_fft,
        hop,
        backend: "hpss-dsp",
    }
}

/// 单个分轨转录。
pub fn transcribe_stem(
    name: &str,
    data: &WavData,
    perc_mag: Option<&[Vec<f32>]>,
    n_fft: usize,
    hop: usize,
) -> (Vec<Note>, bool) {
    let mono = data.mono();
    let sr = data.sample_rate;
    if is_drum(name) {
        let mag = perc_mag.map_or_else(
            || stft::magnitude(&stft::stft(&mono, n_fft, hop)),
            |m| m.to_vec(),
        );
        (drum_notes(&mag, n_fft, hop, sr), true)
    } else if let Some((fmin, fmax)) = stem_pitch_range(name) {
        let f0s = yin_track(&mono, sr, 2048, hop, fmin, fmax);
        (f0_to_notes(&f0s, hop as f32 / sr as f32), false)
    } else {
        (Vec::new(), false)
    }
}

/// 端到端管线。
pub fn run_pipeline(
    input: impl AsRef<Path>,
    out_dir: impl AsRef<Path>,
    stems_key: &str,
    tempo_bpm: f32,
    on_event: Option<&dyn Fn(serde_json::Value)>,
    should_cancel: Option<&dyn Fn() -> bool>,
) -> Result<PipelineReport> {
    let t0 = std::time::Instant::now();
    let out_dir = out_dir.as_ref();
    std::fs::create_dir_all(out_dir)?;
    let schema = schema_for(stems_key)
        .ok_or_else(|| EngineError::InvalidParams(format!("无效分轨预设 {stems_key:?}")))?;
    let total = 2 + schema.len();

    let mut report = PipelineReport {
        input: input.as_ref().display().to_string(),
        output_dir: out_dir.display().to_string(),
        schema: schema.iter().map(|s| s.to_string()).collect(),
        stem_files: Vec::new(),
        midi_files: BTreeMap::new(),
        notes_count: BTreeMap::new(),
        stages: Vec::new(),
        total_seconds: 0.0,
        warnings: Vec::new(),
    };

    // ---- 1. 加载 ----
    check_cancel(should_cancel)?;
    emit(on_event, "start", 1, total, "load", serde_json::json!({}));
    let s = std::time::Instant::now();
    let data = wav::load(&input)?;
    report.stages.push(StageReport {
        name: "load".into(),
        backend: "hound".into(),
        seconds: s.elapsed().as_secs_f32(),
        outputs: vec![report.input.clone()],
    });
    emit(on_event, "end", 1, total, "load", serde_json::json!({}));

    // ---- 2. 分离 ----
    check_cancel(should_cancel)?;
    emit(
        on_event,
        "start",
        2,
        total,
        "separate",
        serde_json::json!({}),
    );
    let s = std::time::Instant::now();
    let sep = separate(&data, schema);
    report.stages.push(StageReport {
        name: "separate".into(),
        backend: sep.backend.into(),
        seconds: s.elapsed().as_secs_f32(),
        outputs: sep
            .stems
            .iter()
            .map(|(name, _)| out_dir.join(format!("{name}.wav")).display().to_string())
            .collect(),
    });
    emit(
        on_event,
        "end",
        2,
        total,
        "separate",
        serde_json::json!({"backend": sep.backend}),
    );

    // ---- 3. 写分轨 + 逐轨转录 ----
    for (i, (stem_name, stem_data)) in sep.stems.iter().enumerate() {
        check_cancel(should_cancel)?;
        let index = 3 + i;
        let stage = format!("transcribe:{stem_name}");
        emit(
            on_event,
            "start",
            index,
            total,
            &stage,
            serde_json::json!({}),
        );
        let s = std::time::Instant::now();

        let stem_path = out_dir.join(format!("{stem_name}.wav"));
        wav::save(&stem_path, stem_data)?;
        report.stem_files.push(stem_path.display().to_string());

        let (notes, is_drum_track) = transcribe_stem(
            stem_name,
            stem_data,
            Some(&sep.perc_mag),
            sep.n_fft,
            sep.hop,
        );
        let midi_path: PathBuf = out_dir.join(format!("{stem_name}.mid"));
        write_midi(&midi_path, &notes, tempo_bpm, is_drum_track)?;
        report
            .midi_files
            .insert(stem_name.clone(), midi_path.display().to_string());
        report.notes_count.insert(stem_name.clone(), notes.len());

        report.stages.push(StageReport {
            name: stage.clone(),
            backend: if is_drum_track { "onset-flux" } else { "yin" }.into(),
            seconds: s.elapsed().as_secs_f32(),
            outputs: vec![midi_path.display().to_string()],
        });
        emit(
            on_event,
            "end",
            index,
            total,
            &stage,
            serde_json::json!({"notes": notes.len()}),
        );
    }

    report.total_seconds = t0.elapsed().as_secs_f32();
    Ok(report)
}

//! 集成冒烟：合成音频 → 端到端管线 → 校验产物。

use std::collections::HashSet;

use musiclab_core::engine::{run_pipeline, schemas};
use musiclab_core::tasks::{TaskManager, TaskParams, TaskStatus};
use musiclab_core::wav;

fn tempdir(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("musiclab-test-{name}"));
    std::fs::create_dir_all(&d).unwrap();
    d
}

/// 合成测试音频：0.5s 低频正弦（bass）+ 0.5s 中频正弦（旋律）+ 脉冲噪声（鼓）。
fn make_input(path: &std::path::Path) {
    let sr = 44_100u32;
    let mut samples: Vec<f32> = Vec::new();
    // 低频正弦 110Hz
    samples.extend(wav::sine(110.0, 0.5, sr, 0.6));
    // 中频正弦 440Hz
    samples.extend(wav::sine(440.0, 0.5, sr, 0.6));
    // 打击脉冲：短促噪声突发
    let mut t = 0.0f32;
    while t < 0.5 {
        let phase = t % 0.25;
        let amp = if phase < 0.02 {
            (rand01() - 0.5) * 0.9
        } else {
            0.0
        };
        samples.push(amp);
        t += 1.0 / sr as f32;
    }
    let data = wav::WavData {
        sample_rate: sr,
        channels: vec![samples],
    };
    wav::save(path, &data).unwrap();
}

/// 确定性伪随机（避免测试依赖）。
fn rand01() -> f32 {
    use std::cell::Cell;
    thread_local! {
        static SEED: Cell<u64> = const { Cell::new(0x1234_5678_9abc_def0) };
    }
    SEED.with(|s| {
        let mut x = s.get();
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        s.set(x);
        (x >> 11) as f32 / (1u64 << 53) as f32
    })
}

#[test]
fn pipeline_3stems_produces_stems_and_midi() {
    let dir = tempdir("pipeline3");
    let input = dir.join("in.wav");
    let out = dir.join("out");
    make_input(&input);

    let events = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let ev2 = events.clone();
    let report = run_pipeline(
        &input,
        &out,
        "3",
        120.0,
        Some(&move |ev| ev2.lock().unwrap().push(ev)),
        None,
    )
    .unwrap();

    // 分轨与 MIDI 均落盘
    for stem in ["bass", "harmonic", "drums"] {
        assert!(out.join(format!("{stem}.wav")).exists(), "缺 {stem}.wav");
        assert!(out.join(format!("{stem}.mid")).exists(), "缺 {stem}.mid");
        assert!(report.notes_count.contains_key(stem));
    }
    // 3 轨 + load + separate = 5 个阶段
    assert_eq!(report.stages.len(), 5);
    // 进度事件按阶段成对出现
    let pairs = events.lock().unwrap().len();
    assert_eq!(pairs, 10, "start/end 事件应成对");

    // MIDI 头校验
    let bytes = std::fs::read(out.join("bass.mid")).unwrap();
    assert_eq!(&bytes[0..4], b"MThd");
    assert_eq!(&bytes[14..18], b"MTrk");
}

#[test]
fn pipeline_rejects_bad_schema() {
    let dir = tempdir("badschema");
    let input = dir.join("in.wav");
    make_input(&input);
    let err = run_pipeline(&input, dir.join("o"), "9", 120.0, None, None).unwrap_err();
    assert!(err.to_string().contains("无效"));
}

#[test]
fn schemas_are_known() {
    assert_eq!(schemas().len(), 2);
}

#[test]
fn task_manager_full_lifecycle() {
    let dir = tempdir("tasks");
    let input = dir.join("in.wav");
    make_input(&input);

    let mgr = TaskManager::new();
    let id = mgr.submit(TaskParams {
        input: input.clone(),
        out_dir: Some(dir.join("taskout")),
        stems: "2".into(),
        tempo_bpm: 90.0,
    });

    let info = mgr.wait(&id).unwrap();
    assert_eq!(info.status, TaskStatus::Done);
    let report = info.result.unwrap();
    assert_eq!(report.schema.len(), 2);
    assert!(report.total_seconds > 0.0);

    // 事件可增量读取，最后一个是 done
    let (mut evs, latest) = mgr.events_since(&id, 0).unwrap();
    assert!(!evs.is_empty());
    evs.clear();
    let _ = latest;
    let (evs, _) = mgr.events_since(&id, 0).unwrap();
    let last = evs.last().unwrap();
    assert_eq!(last["kind"], "done");

    // 已完成任务不可再取消
    assert!(!mgr.cancel(&id));
}

#[test]
fn task_manager_unknown_id() {
    let mgr = TaskManager::new();
    assert!(mgr.get("nope").is_none());
    assert!(!mgr.cancel("nope"));
}

#[test]
fn output_files_are_deterministic_set() {
    let dir = tempdir("determinism");
    let input = dir.join("in.wav");
    make_input(&input);
    let out = dir.join("out");
    run_pipeline(&input, &out, "2", 120.0, None, None).unwrap();
    let names: HashSet<String> = std::fs::read_dir(&out)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    for f in [
        "harmonic.wav",
        "harmonic.mid",
        "percussive.wav",
        "percussive.mid",
    ] {
        assert!(names.contains(f), "缺文件 {f}");
    }
}

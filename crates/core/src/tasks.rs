//! 任务状态机 + 事件总线（HTTP server 与 Tauri 共用）。
//!
//! 设计：每个任务一个后台线程执行 [`crate::engine::run_pipeline`]，
//! 进度事件按序号追加到任务的事件日志；调用方（HTTP 轮询 / Tauri 前端）
//! 用 `events_since(id, seq)` 增量拉取。取消为协作式：管线在阶段边界
//! 检查 `AtomicBool`。

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::engine::{run_pipeline, PipelineReport};
use crate::error::EngineError;

/// 任务参数（POST /api/tasks 的 body）。
#[derive(Debug, Clone, Deserialize)]
pub struct TaskParams {
    pub input: PathBuf,
    #[serde(default)]
    pub out_dir: Option<PathBuf>,
    /// 分轨预设："2" | "3"
    #[serde(default = "default_stems")]
    pub stems: String,
    /// 转谱使用的全局速度（仅影响 MIDI 时间轴换算）
    #[serde(default = "default_tempo")]
    pub tempo_bpm: f32,
}

fn default_stems() -> String {
    "3".into()
}

fn default_tempo() -> f32 {
    120.0
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Running,
    Done,
    Error,
    Cancelled,
}

/// 任务快照（GET /api/tasks/{id} 的主体）。
#[derive(Debug, Clone, Serialize)]
pub struct TaskInfo {
    pub id: String,
    pub status: TaskStatus,
    pub params: TaskParamsSerde,
    pub created_at: f64,
    pub events_count: usize,
    pub result: Option<PipelineReport>,
    pub error: Option<String>,
}

/// 参数的序列化视图（PathBuf 直接按字符串给出）。
#[derive(Debug, Clone, Serialize)]
pub struct TaskParamsSerde {
    pub input: String,
    pub out_dir: String,
    pub stems: String,
    pub tempo_bpm: f32,
}

struct Task {
    info: TaskInfo,
    events: Vec<serde_json::Value>,
    cancel: Arc<AtomicBool>,
}

/// 共享任务管理器。
#[derive(Clone, Default)]
pub struct TaskManager {
    tasks: Arc<Mutex<HashMap<String, Task>>>,
}

impl TaskManager {
    pub fn new() -> Self {
        Self::default()
    }

    /// 提交并立刻在后台线程执行。返回任务 id。
    pub fn submit(&self, mut params: TaskParams) -> String {
        let id = new_id();
        if let Some(dir) = &params.out_dir {
            // 输出目录按任务隔离，避免多任务互相覆盖
            params.out_dir = Some(dir.join(format!("task-{}", &id[..8])));
        }
        let out_dir: PathBuf = params
            .out_dir
            .clone()
            .unwrap_or_else(|| default_out_dir(&id));

        let cancel = Arc::new(AtomicBool::new(false));
        let info = TaskInfo {
            id: id.clone(),
            status: TaskStatus::Running,
            params: TaskParamsSerde {
                input: params.input.display().to_string(),
                out_dir: out_dir.display().to_string(),
                stems: params.stems.clone(),
                tempo_bpm: params.tempo_bpm,
            },
            created_at: now_sec(),
            events_count: 0,
            result: None,
            error: None,
        };

        self.tasks.lock().unwrap().insert(
            id.clone(),
            Task {
                info,
                events: Vec::new(),
                cancel: cancel.clone(),
            },
        );

        let mgr = self.clone();
        let tid = id.clone();
        std::thread::Builder::new()
            .name(format!("task-{tid}"))
            .spawn(move || mgr.run(tid, params, out_dir, cancel))
            .expect("spawn task thread");
        id
    }

    fn run(&self, id: String, params: TaskParams, out_dir: PathBuf, cancel: Arc<AtomicBool>) {
        let on_event = |ev: serde_json::Value| self.push_event(&id, ev);
        let should_cancel = || cancel.load(Ordering::Relaxed);
        let res = run_pipeline(
            &params.input,
            &out_dir,
            &params.stems,
            params.tempo_bpm,
            Some(&on_event),
            Some(&should_cancel),
        );
        match res {
            Ok(report) => {
                self.push_event(
                    &id,
                    serde_json::json!({"kind": "done", "total_seconds": report.total_seconds}),
                );
                self.finish(&id, |info| {
                    info.status = TaskStatus::Done;
                    info.result = Some(report);
                });
            }
            Err(EngineError::Cancelled) => {
                self.push_event(&id, serde_json::json!({"kind": "cancelled"}));
                self.finish(&id, |info| {
                    info.status = TaskStatus::Cancelled;
                });
            }
            Err(e) => {
                self.push_event(
                    &id,
                    serde_json::json!({"kind": "error", "message": e.to_string()}),
                );
                self.finish(&id, |info| {
                    info.status = TaskStatus::Error;
                    info.error = Some(e.to_string());
                });
            }
        }
    }

    fn push_event(&self, id: &str, mut ev: serde_json::Value) {
        let mut tasks = self.tasks.lock().unwrap();
        if let Some(t) = tasks.get_mut(id) {
            if let Some(obj) = ev.as_object_mut() {
                obj.insert("seq".into(), t.events.len().into());
            }
            t.events.push(ev);
            t.info.events_count = t.events.len();
        }
    }

    fn finish(&self, id: &str, f: impl FnOnce(&mut TaskInfo)) {
        let mut tasks = self.tasks.lock().unwrap();
        if let Some(t) = tasks.get_mut(id) {
            f(&mut t.info);
        }
    }

    /// 任务快照。
    pub fn get(&self, id: &str) -> Option<TaskInfo> {
        self.tasks.lock().unwrap().get(id).map(|t| t.info.clone())
    }

    /// 全部任务快照（创建时间倒序）。
    pub fn list(&self) -> Vec<TaskInfo> {
        let mut v: Vec<TaskInfo> = self
            .tasks
            .lock()
            .unwrap()
            .values()
            .map(|t| t.info.clone())
            .collect();
        v.sort_by(|a, b| b.created_at.total_cmp(&a.created_at));
        v
    }

    /// 增量拉取事件：返回 (seq 之后的新事件, 最新 seq)。
    pub fn events_since(&self, id: &str, seq: usize) -> Option<(Vec<serde_json::Value>, usize)> {
        let tasks = self.tasks.lock().unwrap();
        let t = tasks.get(id)?;
        let evs = t.events.iter().skip(seq + 1).cloned().collect();
        Some((evs, t.events.len().saturating_sub(1)))
    }

    /// 请求取消。返回是否存在且仍在运行。
    pub fn cancel(&self, id: &str) -> bool {
        let tasks = self.tasks.lock().unwrap();
        match tasks.get(id) {
            Some(t) if t.info.status == TaskStatus::Running => {
                t.cancel.store(true, Ordering::Relaxed);
                true
            }
            _ => false,
        }
    }

    /// 阻塞等待任务终态（测试用）。
    pub fn wait(&self, id: &str) -> Option<TaskInfo> {
        loop {
            std::thread::sleep(std::time::Duration::from_millis(20));
            let info = self.get(id)?;
            if info.status != TaskStatus::Running {
                return Some(info);
            }
        }
    }
}

fn new_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let mut seed = nanos as u64 ^ (std::process::id() as u64) << 32;
    let mut s = String::with_capacity(16);
    for _ in 0..16 {
        // xorshift64 → 十六进制
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        s.push(char::from_digit((seed & 0xf) as u32, 16).unwrap_or('0'));
    }
    s
}

fn now_sec() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn default_out_dir(id: &str) -> PathBuf {
    std::env::temp_dir().join(format!("musiclab-{id}"))
}

//! MusicLab 原生引擎（纯 Rust）。
//!
//! 模块分层与 Python 版 musiclab 对齐，但全部在 Rust 内实现：
//! - [`wav`]    WAV 读写（hound）
//! - [`stft`]   STFT/ISTFT（rustfft，OLA 重建）
//! - [`hpss`]   谐波/打击乐分离（中值滤波软掩码）+ 频带切分
//! - [`pitch`]  YIN 基频追踪 + 音符分组
//! - [`onset`]  频谱通量 onset 检测 + 鼓件频带分类
//! - [`midi`]   MIDI 文件写出（手工字节级，格式 0）
//! - [`engine`] 管线编排（分离 → 逐轨转谱）
//! - [`tasks`]  任务状态机 + 事件总线（HTTP server 与 Tauri 共用）

pub mod engine;
pub mod error;
pub mod hpss;
pub mod midi;
pub mod onset;
pub mod pitch;
pub mod stft;
pub mod tasks;
pub mod wav;

pub use engine::{Note, PipelineReport, StageReport};
pub use error::{EngineError, Result};

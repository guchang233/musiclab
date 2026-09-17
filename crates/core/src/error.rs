//! 引擎错误类型。

use std::fmt;

pub type Result<T> = std::result::Result<T, EngineError>;

#[derive(Debug)]
pub enum EngineError {
    /// IO 错误（读写文件）
    Io(std::io::Error),
    /// WAV 解码失败
    Wav(String),
    /// 操作在阶段边界被协作式取消
    Cancelled,
    /// 参数不合法
    InvalidParams(String),
    /// 内部错误（不应发生）
    Internal(String),
}

impl fmt::Display for EngineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(e) => write!(f, "IO 错误：{e}"),
            Self::Wav(m) => write!(f, "WAV 解码失败：{m}"),
            Self::Cancelled => write!(f, "操作已取消"),
            Self::InvalidParams(m) => write!(f, "参数错误：{m}"),
            Self::Internal(m) => write!(f, "内部错误：{m}"),
        }
    }
}

impl std::error::Error for EngineError {}

impl From<std::io::Error> for EngineError {
    fn from(e: std::io::Error) -> Self {
        Self::Io(e)
    }
}

impl From<hound::Error> for EngineError {
    fn from(e: hound::Error) -> Self {
        Self::Wav(e.to_string())
    }
}

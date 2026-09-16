"""引擎配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def default_cache_dir() -> Path:
    """默认缓存目录：遵循 XDG 规范，回退到 ``~/.cache/musiclab``。"""
    base = os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path.home() / ".cache"
    return root / "musiclab"


@dataclass(frozen=True)
class EngineConfig:
    """全局引擎配置。

    Attributes:
        sample_rate: 引擎工作采样率（加载音频时统一重采样到该值，
            保证缓存键稳定、ML 后端输入一致）。
        cache_dir: 产物缓存目录；None 表示禁用缓存。
        device: ML 后端设备（"auto" / "cuda" / "cpu" / "mps"）。
        separator_name: 指定分离后端名；None 表示按优先级自动选择。
        transcriber_name: 指定（旋律）转录后端名；None 表示自动选择。
        drum_transcriber_name: 指定鼓谱后端名；None 表示自动选择。
        segment_seconds: DSP 后端分块处理时长（秒），控制内存占用。
        demucs_model: Demucs 模型名（如 ``htdemucs`` / ``htdemucs_6s``）。
    """

    sample_rate: int = 44100
    cache_dir: Path | None = field(default_factory=default_cache_dir)
    device: str = "auto"
    separator_name: str | None = None
    transcriber_name: str | None = None
    drum_transcriber_name: str | None = None
    segment_seconds: float = 30.0
    demucs_model: str = "htdemucs"

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正：{self.sample_rate}")
        if self.segment_seconds <= 0:
            raise ValueError(f"segment_seconds 必须为正：{self.segment_seconds}")

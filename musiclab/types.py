"""核心数据类型。

本模块只依赖 numpy，保持轻量：类型层不应拖动 librosa/scipy 等重型依赖。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------------------
# 分轨预设（stem schema）
# ---------------------------------------------------------------------------

#: 2 分轨：人声 / 伴奏
STEM_TWO: tuple[str, ...] = ("vocals", "accompaniment")
#: 4 分轨：人声 / 鼓 / 贝斯 / 其他（Demucs 标准输出）
STEM_FOUR: tuple[str, ...] = ("vocals", "drums", "bass", "other")
#: 6 分轨：4 分轨 + 钢琴 / 吉他（需要 htdemucs_6s 权重）
STEM_SIX: tuple[str, ...] = STEM_FOUR + ("piano", "guitar")

#: CLI / 管线使用的分轨预设表
STEM_PRESETS: dict[str, tuple[str, ...]] = {
    "2": STEM_TWO,
    "4": STEM_FOUR,
    "6": STEM_SIX,
}


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioData:
    """一段内存中的音频。

    Attributes:
        samples: 采样数据，float32，形状为 ``(n,)``（单声道）
            或 ``(n, channels)``（多声道）。
        sample_rate: 采样率（Hz）。
    """

    samples: np.ndarray
    sample_rate: int

    def __post_init__(self) -> None:
        if not isinstance(self.samples, np.ndarray):
            raise TypeError(f"samples 必须是 numpy 数组，得到 {type(self.samples)!r}")
        if self.samples.ndim not in (1, 2):
            raise ValueError(f"samples 维度必须为 1 或 2，得到 {self.samples.ndim}")
        if self.samples.shape[0] == 0:
            raise ValueError("samples 不能为空")
        if not isinstance(self.sample_rate, (int, np.integer)) or self.sample_rate <= 0:
            raise ValueError(f"sample_rate 必须为正整数，得到 {self.sample_rate!r}")
        if not np.issubdtype(self.samples.dtype, np.floating):
            raise TypeError(f"samples 必须为浮点类型，得到 {self.samples.dtype}")
        # 统一 float32，避免引擎间 dtype 漂移
        if self.samples.dtype != np.float32:
            object.__setattr__(self, "samples", self.samples.astype(np.float32))
        if self.samples.ndim == 2 and self.samples.shape[1] == 0:
            raise ValueError("samples 声道数不能为 0")

    @property
    def duration(self) -> float:
        """时长（秒）。"""
        return self.samples.shape[0] / self.sample_rate

    @property
    def channels(self) -> int:
        """声道数。"""
        return 1 if self.samples.ndim == 1 else self.samples.shape[1]

    @property
    def num_samples(self) -> int:
        """采样点数。"""
        return self.samples.shape[0]

    def mono(self) -> np.ndarray:
        """返回声道均值混合后的单声道数组 ``(n,)``。"""
        if self.samples.ndim == 1:
            return self.samples
        return self.samples.mean(axis=1)

    def channel(self, index: int) -> np.ndarray:
        """返回指定声道（单声道音频视作第 0 声道）。"""
        if self.samples.ndim == 1:
            if index != 0:
                raise IndexError(f"单声道音频只有声道 0，请求 {index}")
            return self.samples
        if not 0 <= index < self.samples.shape[1]:
            raise IndexError(f"声道索引 {index} 超出范围 0..{self.channels - 1}")
        return self.samples[:, index]


@dataclass(frozen=True)
class Note:
    """一个 MIDI 音符。

    Attributes:
        start: 起始时间（秒）。
        end: 结束时间（秒）。
        pitch: MIDI 音高（0-127，60 = C4）。
        velocity: 力度（1-127）。
        confidence: 转录置信度（0-1），未知时为 1.0。
    """

    start: float
    end: float
    pitch: int
    velocity: int = 64
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not (self.end >= self.start):
            raise ValueError(f"音符结束时间必须不早于开始时间：{self.start} > {self.end}")
        if not 0 <= self.pitch <= 127:
            raise ValueError(f"pitch 必须在 0..127，得到 {self.pitch}")
        if not 1 <= self.velocity <= 127:
            raise ValueError(f"velocity 必须在 1..127，得到 {self.velocity}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 必须在 0..1，得到 {self.confidence}")

    @property
    def duration(self) -> float:
        """音符时值（秒）。"""
        return self.end - self.start


@dataclass(frozen=True)
class Stem:
    """一条分离出的分轨。

    Attributes:
        name: 分轨名（如 ``vocals`` / ``drums`` / ``bass`` / ``other``）。
        audio: 分轨音频。
    """

    name: str
    audio: AudioData


@dataclass(frozen=True)
class TranscriptionResult:
    """一次转录的结果。

    Attributes:
        notes: 音符列表（按起始时间排序的约定由写出层保证）。
        instrument: 乐器标签（如 ``bass`` / ``piano`` / ``drums``）。
        backend: 产生该结果的后端名（如 ``pyin`` / ``basic-pitch``）。
        duration: 源音频时长（秒）。
        warnings: 转录过程中的告警信息。
    """

    notes: tuple[Note, ...]
    instrument: str
    backend: str
    duration: float
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.duration < 0:
            raise ValueError(f"duration 不能为负：{self.duration}")
        # 规范化：notes 必须是 tuple，便于哈希与不可变约定
        if isinstance(self.notes, list):
            object.__setattr__(self, "notes", tuple(self.notes))

    @property
    def empty(self) -> bool:
        return len(self.notes) == 0

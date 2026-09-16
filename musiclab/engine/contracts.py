"""引擎契约层：后端接口的 Protocol 定义与能力元数据。

设计要点：
- 使用 ``typing.Protocol`` 做结构化类型，后端无需继承任何基类；
- 后端以 ``BackendSpec``（元数据 + 工厂）的形式注册进 ``ModelRegistry``；
- 重型依赖（torch/demucs/basic-pitch）一律延迟导入，导入时机在后端工厂内。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from musiclab.types import AudioData, Stem, TranscriptionResult

#: 分轨方案：若干分轨名组成的元组（如 ("vocals", "drums", "bass", "other")）
StemSchema = tuple[str, ...]


class Availability(Enum):
    """后端可用性状态。"""

    AVAILABLE = "available"
    MISSING_DEPENDENCY = "missing_dependency"
    INIT_ERROR = "init_error"


@dataclass(frozen=True)
class AvailabilityInfo:
    """可用性检查结果。

    Attributes:
        status: 状态。
        reason: 不可用原因（面向用户可读）。
        install_hint: 安装指引（如 ``pip install musiclab[demucs]``）。
    """

    status: Availability
    reason: str = ""
    install_hint: str = ""

    @property
    def available(self) -> bool:
        return self.status is Availability.AVAILABLE


#: 后端层级："ml"（模型推理，质量优先）或 "dsp"（纯信号处理，可用性兜底）
BackendTier = str


@dataclass(frozen=True)
class BackendSpec:
    """注册表条目：后端能力元数据 + 构造工厂。

    分离后端（kind="separator"）需提供 ``supported_schemas``；
    转录后端（kind="transcriber"）需提供 ``instruments`` 与 ``polyphonic``。
    """

    name: str
    kind: str  # "separator" | "transcriber"
    tier: BackendTier  # "ml" | "dsp"
    priority: int  # 数值越大越优先；可用性相同时取 priority 最大者
    availability: Callable[[], AvailabilityInfo]
    factory: Callable[..., object]
    description: str = ""
    # ---- separator 专属 ----
    supported_schemas: tuple[StemSchema, ...] = ()
    # ---- transcriber 专属 ----
    instruments: tuple[str, ...] = ()
    polyphonic: bool = False

    def supports_schema(self, schema: StemSchema) -> bool:
        return schema in self.supported_schemas

    def display(self) -> dict[str, object]:
        """用于 CLI / 报告展示的字典形式。"""
        info = self.availability()
        return {
            "name": self.name,
            "kind": self.kind,
            "tier": self.tier,
            "priority": self.priority,
            "available": info.available,
            "availability": info.status.value,
            "reason": info.reason,
            "description": self.description,
            "supported_schemas": [list(s) for s in self.supported_schemas],
            "instruments": list(self.instruments),
            "polyphonic": self.polyphonic,
        }


@runtime_checkable
class Separator(Protocol):
    """音源分离后端契约。"""

    name: str

    def separate(self, audio: AudioData, schema: StemSchema) -> list[Stem]:
        """把 ``audio`` 分离为 ``schema`` 指定的分轨。

        Raises:
            UnsupportedStemSchemaError: 不支持该分轨方案。
        """
        ...


@runtime_checkable
class Transcriber(Protocol):
    """WAV→MIDI 转录后端契约。"""

    name: str
    instrument: str

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        """把音频转录为音符序列。"""
        ...


@dataclass
class BackendDiagnostics:
    """一次注册表解析的诊断信息，用于错误信息与日志。"""

    requested: str
    considered: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    selected: str | None = None

    def reject(self, name: str, reason: str) -> None:
        self.rejected[name] = reason

    def message(self) -> str:
        parts = [f"请求：{self.requested}"]
        if self.selected:
            parts.append(f"已选择：{self.selected}")
        if self.rejected:
            detail = "；".join(f"{k}（{v}）" for k, v in self.rejected.items())
            parts.append(f"被排除的后端：{detail}")
        return " | ".join(parts)

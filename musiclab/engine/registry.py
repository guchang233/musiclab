"""模型注册表：后端的注册、能力查询与按优先级解析。

注册表只存 ``BackendSpec``（元数据 + 工厂），实例化延迟到解析时，
因此 import 本模块不会有任何重型依赖开销。
"""

from __future__ import annotations

import threading

from musiclab.engine.contracts import (
    BackendSpec,
    Separator,
    StemSchema,
    Transcriber,
)
from musiclab.engine.errors import BackendNotAvailableError, UnsupportedStemSchemaError

# 可重入锁：get_default_registry() 持锁构建时，内部的 register() 会再次进锁。
# 普通 Lock 在此会自锁死（不可重入）。
_LOCK = threading.RLock()


class ModelRegistry:
    """后端注册表。

    线程安全性：注册与解析全程持锁；后端实例本身是否线程安全由实现负责。
    """

    def __init__(self) -> None:
        self._specs: dict[tuple[str, str], BackendSpec] = {}

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(self, spec: BackendSpec) -> None:
        """注册一个后端；同名同类型后端会被覆盖。"""
        key = (spec.kind, spec.name)
        with _LOCK:
            self._specs[key] = spec

    def unregister(self, kind: str, name: str) -> None:
        """移除一个后端。"""
        with _LOCK:
            self._specs.pop((kind, name), None)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def specs(self, kind: str | None = None) -> list[BackendSpec]:
        """列出已注册的后端（按 kind 过滤，priority 降序）。"""
        with _LOCK:
            specs = list(self._specs.values())
        if kind is not None:
            specs = [s for s in specs if s.kind == kind]
        return sorted(specs, key=lambda s: s.priority, reverse=True)

    def get_spec(self, kind: str, name: str) -> BackendSpec:
        """按名称取后端元数据；不存在时抛 KeyError。"""
        with _LOCK:
            spec = self._specs.get((kind, name))
        if spec is None:
            raise KeyError(f"未注册的后端：{kind}/{name}")
        return spec

    def diagnostics(self) -> list[dict[str, object]]:
        """全部后端的可用性诊断（用于 CLI `backends` 命令）。"""
        return [spec.display() for spec in self.specs()]

    # ------------------------------------------------------------------
    # 解析（工厂方法）
    # ------------------------------------------------------------------

    def separator(
        self,
        schema: StemSchema,
        name: str | None = None,
        **factory_kwargs: object,
    ) -> Separator:
        """解析一个支持 ``schema`` 的分离后端。

        Args:
            schema: 目标分轨方案。
            name: 指定后端名；None 表示在可用后端中按 priority 自动选择。
            **factory_kwargs: 透传给后端工厂的额外参数（如 ``config``）。

        Raises:
            BackendNotAvailableError: 指定后端不存在或不可用，
                或没有可用后端支持该方案。
        """
        with _LOCK:
            candidates = [s for s in self._specs.values() if s.kind == "separator"]

        if name is not None:
            matched = [s for s in candidates if s.name == name]
            if not matched:
                raise BackendNotAvailableError(name, "未注册此后端")
            spec = matched[0]
            info = spec.availability()
            if not info.available:
                raise BackendNotAvailableError(spec.name, info.reason, info.install_hint)
            if not spec.supports_schema(schema):
                raise UnsupportedStemSchemaError(spec.name, schema)
            backend = spec.factory(schema=schema, **factory_kwargs)
            assert isinstance(backend, Separator)
            return backend

        # 自动选择：可用 + 支持方案，按 priority 降序
        rejected: dict[str, str] = {}
        for spec in sorted(candidates, key=lambda s: s.priority, reverse=True):
            info = spec.availability()
            if not info.available:
                rejected[spec.name] = info.reason or info.status.value
                continue
            if not spec.supports_schema(schema):
                rejected[spec.name] = f"不支持分轨方案 {list(schema)}"
                continue
            backend = spec.factory(schema=schema, **factory_kwargs)
            assert isinstance(backend, Separator)
            return backend

        if rejected:
            detail = "；".join(f"{k}：{v}" for k, v in rejected.items())
            raise BackendNotAvailableError(
                "separator(auto)", f"没有可用后端支持 {list(schema)}。{detail}"
            )
        raise BackendNotAvailableError(
            "separator(auto)", f"没有注册任何分离后端（请求方案 {list(schema)}）"
        )

    def transcriber(
        self,
        instrument: str = "auto",
        *,
        polyphonic: bool = False,
        name: str | None = None,
        **factory_kwargs: object,
    ) -> Transcriber:
        """解析一个匹配乐器需求的转录后端。

        Args:
            instrument: 乐器标签；后端声明支持 "any" 或同名乐器即视为匹配，
                "drums" 走鼓谱专用后端。
            polyphonic: 是否要求多音轨能力（混合分轨必须为 True）。
            name: 指定后端名；None 表示自动选择。
            **factory_kwargs: 透传给后端工厂的额外参数（如 ``config``）。

        Raises:
            BackendNotAvailableError: 指定后端不可用，或无可用后端满足要求。
        """
        with _LOCK:
            candidates = [s for s in self._specs.values() if s.kind == "transcriber"]

        def _matches_instrument(spec: BackendSpec) -> bool:
            return instrument in spec.instruments or "any" in spec.instruments

        if name is not None:
            matched = [s for s in candidates if s.name == name]
            if not matched:
                raise BackendNotAvailableError(name, "未注册此后端")
            spec = matched[0]
            info = spec.availability()
            if not info.available:
                raise BackendNotAvailableError(spec.name, info.reason, info.install_hint)
            backend = spec.factory(instrument=instrument, **factory_kwargs)
            assert isinstance(backend, Transcriber)
            return backend

        rejected: dict[str, str] = {}
        for spec in sorted(candidates, key=lambda s: s.priority, reverse=True):
            info = spec.availability()
            if not info.available:
                rejected[spec.name] = info.reason or info.status.value
                continue
            if not _matches_instrument(spec):
                continue  # 不匹配乐器不属于“被拒绝”，静默跳过即可
            if polyphonic and not spec.polyphonic:
                rejected[spec.name] = "不支持多音轨输入"
                continue
            backend = spec.factory(instrument=instrument, **factory_kwargs)
            assert isinstance(backend, Transcriber)
            return backend

        if rejected:
            detail = "；".join(f"{k}：{v}" for k, v in rejected.items())
            raise BackendNotAvailableError(
                "transcriber(auto)",
                f"没有可用后端满足乐器={instrument!r}、多音轨={polyphonic}。{detail}",
            )
        raise BackendNotAvailableError(
            "transcriber(auto)",
            f"没有注册任何支持乐器 {instrument!r} 的转录后端",
        )


# ---------------------------------------------------------------------------
# 内置后端的注册入口
# ---------------------------------------------------------------------------

_default_registry: ModelRegistry | None = None


def build_default_registry() -> ModelRegistry:
    """构建注册了全部内置后端的注册表。

    每次调用返回全新实例，便于测试隔离；
    ``get_default_registry()`` 提供进程级单例。
    """
    from musiclab.engine.separation.demucs import demucs_availability, demucs_factory
    from musiclab.engine.separation.spectral import (
        spectral_availability,
        spectral_factory,
    )
    from musiclab.engine.transcription.basic_pitch import (
        basic_pitch_availability,
        basic_pitch_factory,
    )
    from musiclab.engine.transcription.drum_onset import (
        drum_onset_availability,
        drum_onset_factory,
    )
    from musiclab.engine.transcription.pyin_backend import (
        pyin_availability,
        pyin_factory,
    )
    from musiclab.types import STEM_FOUR, STEM_SIX, STEM_TWO

    registry = ModelRegistry()

    # ---- 分离后端：ML 优先，DSP 兜底 ----
    registry.register(
        BackendSpec(
            name="demucs",
            kind="separator",
            tier="ml",
            priority=100,
            availability=demucs_availability,
            factory=demucs_factory,
            description="Demucs v4（hybrid transformer），开源分离质量基准",
            supported_schemas=(STEM_TWO, STEM_FOUR, STEM_SIX),
        )
    )
    registry.register(
        BackendSpec(
            name="spectral-hpss",
            kind="separator",
            tier="dsp",
            priority=10,
            availability=spectral_availability,
            factory=spectral_factory,
            description="HPSS 谐波/打击乐 + 频带路由的降级后端（质量有限，仅兜底）",
            supported_schemas=(STEM_FOUR, ("drums", "other")),
        )
    )

    # ---- 转录后端：ML（多音轨）优先，DSP（单音）兜底 ----
    registry.register(
        BackendSpec(
            name="basic-pitch",
            kind="transcriber",
            tier="ml",
            priority=100,
            availability=basic_pitch_availability,
            factory=basic_pitch_factory,
            description="Spotify Basic Pitch，轻量多音轨音符转录（含 pitch bend）",
            instruments=("any",),
            polyphonic=True,
        )
    )
    registry.register(
        BackendSpec(
            name="pyin",
            kind="transcriber",
            tier="dsp",
            priority=10,
            availability=pyin_availability,
            factory=pyin_factory,
            description="pYIN 基频追踪的降级后端（仅单音旋律，无和弦）",
            instruments=("any",),
            polyphonic=False,
        )
    )
    registry.register(
        BackendSpec(
            name="drum-onset",
            kind="transcriber",
            tier="dsp",
            priority=50,
            availability=drum_onset_availability,
            factory=drum_onset_factory,
            description="鼓组 onset 检测 + 频带分类（kick/snare/hihat → GM 鼓组）",
            instruments=("drums",),
            polyphonic=True,
        )
    )

    return registry


def get_default_registry() -> ModelRegistry:
    """进程级默认注册表单例（懒加载、线程安全）。"""
    global _default_registry
    if _default_registry is None:
        with _LOCK:
            if _default_registry is None:
                _default_registry = build_default_registry()
    return _default_registry

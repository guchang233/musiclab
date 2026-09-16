"""注册表解析逻辑测试（用假后端保证确定性）。"""

from __future__ import annotations

import pytest

from musiclab.engine.contracts import (
    Availability,
    AvailabilityInfo,
    BackendSpec,
)
from musiclab.engine.errors import BackendNotAvailableError, UnsupportedStemSchemaError
from musiclab.engine.registry import ModelRegistry
from musiclab.types import STEM_FOUR, STEM_TWO


class _FakeSeparator:
    def __init__(self, schema, tag=""):
        self.name = f"fake-{tag}" if tag else "fake"
        self._schema = tuple(schema)

    def separate(self, audio, schema=None):
        raise NotImplementedError


class _FakeTranscriber:
    def __init__(self, instrument="auto", tag=""):
        self.name = f"fake-{tag}" if tag else "fake"
        self.instrument = instrument

    def transcribe(self, audio):
        raise NotImplementedError


def _always_ok():
    return AvailabilityInfo(status=Availability.AVAILABLE)


def _missing(msg="依赖缺失"):
    return AvailabilityInfo(
        status=Availability.MISSING_DEPENDENCY, reason=msg, install_hint="pip install fake"
    )


def _sep_spec(name, priority, schemas, available=_always_ok):
    return BackendSpec(
        name=name,
        kind="separator",
        tier="ml" if priority >= 50 else "dsp",
        priority=priority,
        availability=available,
        factory=lambda schema, **kw: _FakeSeparator(schema, tag=name),
        supported_schemas=tuple(schemas),
    )


def _tr_spec(name, priority, *, polyphonic=False, available=_always_ok):
    return BackendSpec(
        name=name,
        kind="transcriber",
        tier="ml" if priority >= 50 else "dsp",
        priority=priority,
        availability=available,
        factory=lambda instrument, **kw: _FakeTranscriber(instrument, tag=name),
        instruments=("any",),
        polyphonic=polyphonic,
    )


class TestSeparatorResolution:
    def test_按优先级选择(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("low", 10, [STEM_FOUR]))
        reg.register(_sep_spec("high", 100, [STEM_FOUR]))
        assert reg.separator(STEM_FOUR).name == "fake-high"

    def test_不可用时降级(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("broken", 100, [STEM_FOUR], available=_missing))
        reg.register(_sep_spec("fallback", 10, [STEM_FOUR]))
        assert reg.separator(STEM_FOUR).name == "fake-fallback"

    def test_指定名称(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("a", 100, [STEM_FOUR]))
        reg.register(_sep_spec("b", 10, [STEM_FOUR]))
        assert reg.separator(STEM_FOUR, name="b").name == "fake-b"

    def test_指定名称但不可用(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("broken", 100, [STEM_FOUR], available=_missing))
        with pytest.raises(BackendNotAvailableError, match="pip install fake"):
            reg.separator(STEM_FOUR, name="broken")

    def test_指定名称不存在(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("a", 100, [STEM_FOUR]))
        with pytest.raises(BackendNotAvailableError, match="未注册"):
            reg.separator(STEM_FOUR, name="ghost")

    def test_方案不支持时跳过(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("two-only", 100, [STEM_TWO]))
        reg.register(_sep_spec("four", 10, [STEM_FOUR]))
        assert reg.separator(STEM_FOUR).name == "fake-four"

    def test_指定名称且方案不支持(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("two-only", 100, [STEM_TWO]))
        with pytest.raises(UnsupportedStemSchemaError):
            reg.separator(STEM_FOUR, name="two-only")

    def test_无可用后端时汇总原因(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("broken", 100, [STEM_FOUR], available=_missing))
        with pytest.raises(BackendNotAvailableError, match="依赖缺失"):
            reg.separator(STEM_FOUR)

    def test_空注册表(self):
        reg = ModelRegistry()
        with pytest.raises(BackendNotAvailableError):
            reg.separator(STEM_FOUR)


class TestTranscriberResolution:
    def test_多音轨要求过滤单音后端(self):
        reg = ModelRegistry()
        reg.register(_tr_spec("mono-dsp", 10, polyphonic=False))
        reg.register(_tr_spec("poly-ml", 100, polyphonic=True))
        assert reg.transcriber(polyphonic=True).name == "fake-poly-ml"

    def test_多音轨无可用时给出原因(self):
        reg = ModelRegistry()
        reg.register(_tr_spec("mono-dsp", 10, polyphonic=False))
        with pytest.raises(BackendNotAvailableError, match="不支持多音轨输入"):
            reg.transcriber(polyphonic=True)

    def test_单音请求兼容多音后端(self):
        reg = ModelRegistry()
        reg.register(_tr_spec("mono-dsp", 10, polyphonic=False))
        reg.register(_tr_spec("poly-ml", 100, polyphonic=True))
        assert reg.transcriber(polyphonic=False).name == "fake-poly-ml"

    def test_指定名称(self):
        reg = ModelRegistry()
        reg.register(_tr_spec("a", 100))
        reg.register(_tr_spec("b", 10))
        assert reg.transcriber(name="b").name == "fake-b"

    def test_乐器不匹配的后端被跳过(self):
        reg = ModelRegistry()
        reg.register(
            BackendSpec(
                name="drums-only",
                kind="transcriber",
                tier="dsp",
                priority=100,
                availability=_always_ok,
                factory=lambda instrument, **kw: _FakeTranscriber(instrument, tag="drums-only"),
                instruments=("drums",),
                polyphonic=True,
            )
        )
        reg.register(_tr_spec("generic", 10))
        # 请求 drums → 只有 drums-only 匹配（工厂生成实例名为 fake-drums-only）
        assert reg.transcriber("drums").name == "fake-drums-only"
        # 请求 piano → drums-only 不匹配，落到 generic
        assert reg.transcriber("piano").name == "fake-generic"


class TestDefaultRegistry:
    def test_默认注册表可解析分离器(self):
        from musiclab.engine import build_default_registry

        reg = build_default_registry()
        sep = reg.separator(STEM_FOUR)
        assert callable(sep.separate)

    def test_默认注册表可解析鼓谱后端(self):
        from musiclab.engine import build_default_registry

        reg = build_default_registry()
        tr = reg.transcriber("drums")
        assert tr.name == "drum-onset"

    def test_诊断包含五个内置后端(self):
        from musiclab.engine import build_default_registry

        reg = build_default_registry()
        names = {s["name"] for s in reg.diagnostics()}
        assert names == {"demucs", "spectral-hpss", "basic-pitch", "pyin", "drum-onset"}

    def test_同名覆盖注册(self):
        reg = ModelRegistry()
        reg.register(_sep_spec("dup", 10, [STEM_FOUR]))
        reg.register(_sep_spec("dup", 20, [STEM_FOUR]))
        assert len([s for s in reg.specs() if s.name == "dup"]) == 1

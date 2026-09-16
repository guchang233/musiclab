"""Demucs 分离后端（ML 层，质量优先）。

依赖 torch + demucs，二者均为可选依赖：
``pip install musiclab[demucs]``。

实现要点：
- 延迟导入：本模块被 import 不会触发 torch 加载；
- 分轨映射完全依据 ``model.sources`` 属性，不硬编码顺序；
- 2 分轨方案由 4 分轨结果合并（vocals 之外求和）得到；
- 6 分轨方案自动切换 ``htdemucs_6s`` 权重；
- 标准化/反标准化流程与 demucs CLI 一致，避免数值溢出。
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from musiclab.config import EngineConfig
from musiclab.engine.contracts import Availability, AvailabilityInfo
from musiclab.engine.errors import BackendNotAvailableError, UnsupportedStemSchemaError
from musiclab.types import AudioData, Stem, STEM_FOUR, STEM_SIX, STEM_TWO

logger = logging.getLogger(__name__)

#: Demucs 系列模型的固定工作采样率
DEMUCS_SAMPLE_RATE = 44100


@lru_cache(maxsize=1)
def demucs_availability() -> AvailabilityInfo:
    """检查 torch/demucs 是否可导入（结果进程内缓存）。"""
    try:
        import torch  # noqa: F401
        import demucs  # noqa: F401
    except ImportError as exc:
        return AvailabilityInfo(
            status=Availability.MISSING_DEPENDENCY,
            reason=f"缺少依赖：{exc}",
            install_hint="pip install musiclab[demucs]",
        )
    return AvailabilityInfo(status=Availability.AVAILABLE)


class DemucsSeparator:
    """Demucs v4 分离后端。"""

    name = "demucs"

    def __init__(
        self,
        schema: tuple[str, ...] | None = None,
        config: EngineConfig | None = None,
        model: str | None = None,
    ) -> None:
        try:
            import torch
            from demucs.pretrained import get_model
        except ImportError as exc:
            raise BackendNotAvailableError(
                "demucs", f"缺少依赖：{exc}", "pip install musiclab[demucs]"
            ) from exc

        self._config = config or EngineConfig()
        self._schema = tuple(schema) if schema is not None else STEM_FOUR
        self.model_name = model or self._config.demucs_model
        # 6 分轨需要专用权重；2 分轨用标准 4 分轨模型合并
        if self._schema == STEM_SIX and self.model_name == "htdemucs":
            self.model_name = "htdemucs_6s"

        self._torch = torch
        self._model = get_model(self.model_name)
        self._device = self._resolve_device(torch)
        self._model.to(self._device)
        self._model.eval()
        logger.info("Demucs 就绪：model=%s, device=%s", self.model_name, self._device)

    def _resolve_device(self, torch) -> str:
        if self._config.device != "auto":
            return self._config.device
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    # ------------------------------------------------------------------

    def separate(self, audio: AudioData, schema: tuple[str, ...] | None = None) -> list[Stem]:
        schema = tuple(schema) if schema is not None else self._schema
        self._validate_schema(schema)

        from demucs.apply import apply_model

        torch = self._torch

        # 统一到 44.1kHz（模型输入要求）
        wav = audio.samples
        if audio.sample_rate != DEMUCS_SAMPLE_RATE:
            from musiclab.audio import resample_audio

            wav = resample_audio(audio, DEMUCS_SAMPLE_RATE).samples

        y = wav.T if wav.ndim == 2 else wav[None, :]  # (ch, n)
        mix = torch.from_numpy(np.ascontiguousarray(y)).to(self._device)

        # 与 demucs CLI 一致的标准化，避免大音量下模型数值溢出
        ref = mix.mean(0)
        mix = (mix - ref.mean()) / (ref.std() + 1e-8)

        with torch.no_grad():
            sources = apply_model(
                self._model, mix[None], device=self._device, progress=False, split=True
            )[0]  # (sources, ch, n)
        sources = sources * ref.std() + ref.mean()

        src_names: tuple[str, ...] = tuple(self._model.sources)
        by_name = {n: sources[i].cpu().numpy() for i, n in enumerate(src_names)}

        # ---- 按 schema 组装输出 ----
        out: list[Stem] = []
        if schema == STEM_TWO:
            if "vocals" not in by_name:
                raise UnsupportedStemSchemaError(self.name, schema)
            accompaniment = sum(
                (by_name[n] for n in src_names if n != "vocals"),
                start=np.zeros_like(next(iter(by_name.values()))),
            )
            out.append(Stem(name="vocals", audio=self._to_audio(by_name["vocals"])))
            out.append(Stem(name="accompaniment", audio=self._to_audio(accompaniment)))
        else:
            for name in schema:
                if name not in by_name:
                    raise UnsupportedStemSchemaError(self.name, schema)
                out.append(Stem(name=name, audio=self._to_audio(by_name[name])))
        return out

    # ------------------------------------------------------------------

    def _validate_schema(self, schema: tuple[str, ...]) -> None:
        if schema in (STEM_TWO, STEM_FOUR, STEM_SIX):
            return
        raise UnsupportedStemSchemaError(self.name, schema)

    def _to_audio(self, data: np.ndarray) -> AudioData:
        """(ch, n) → AudioData；单声道退化为 (n,)。"""
        if data.shape[0] == 1:
            data = data[0]
        else:
            data = data.T
        return AudioData(
            samples=np.ascontiguousarray(data, dtype=np.float32),
            sample_rate=DEMUCS_SAMPLE_RATE,
        )


def demucs_factory(
    schema: tuple[str, ...], config: EngineConfig | None = None
) -> DemucsSeparator:
    return DemucsSeparator(schema=schema, config=config)

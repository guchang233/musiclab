"""频谱分离后端（DSP 降级层）。

算法：STFT → HPSS（谐波/打击乐软掩码分解）→ 谐波分量按频带路由。

诚实声明：这是质量有限的兜底后端——频带路由无法区分同为谐波
能量的人声与伴奏乐器（"vocals" 分轨实际是谐波中频带）。存在意义是
在未安装 ML 依赖时让管线端到端可用，并提供确定性测试基线。

特性：
- 软掩码互补（margin=1.0）→ 各分轨之和 ≈ 原始信号（能量守恒）；
- 按 ``segment_seconds`` 分块处理，内存占用与音频总时长无关。
"""

from __future__ import annotations

import logging

import numpy as np

from musiclab.config import EngineConfig
from musiclab.engine.contracts import Availability, AvailabilityInfo
from musiclab.engine.errors import UnsupportedStemSchemaError
from musiclab.types import AudioData, Stem, STEM_FOUR

logger = logging.getLogger(__name__)

#: 频带切分点（Hz）：谐波分量的路由边界
_BASS_CUTOFF = 150.0
_VOCAL_LOW = 200.0
_VOCAL_HIGH = 5000.0

_N_FFT = 4096
_HOP = 1024
_HPSS_KERNEL = 31


class SpectralSeparator:
    """HPSS + 频带路由的分离后端。"""

    name = "spectral-hpss"
    SUPPORTED_SCHEMAS: tuple[tuple[str, ...], ...] = (STEM_FOUR, ("drums", "other"))

    def __init__(
        self,
        schema: tuple[str, ...] | None = None,
        config: EngineConfig | None = None,
    ) -> None:
        self._config = config or EngineConfig()
        self._schema = tuple(schema) if schema is not None else STEM_FOUR

    # ------------------------------------------------------------------
    # 契约实现
    # ------------------------------------------------------------------

    def separate(self, audio: AudioData, schema: tuple[str, ...] | None = None) -> list[Stem]:
        schema = tuple(schema) if schema is not None else self._schema
        if schema not in self.SUPPORTED_SCHEMAS:
            raise UnsupportedStemSchemaError(self.name, schema)

        logger.info("spectral-hpss 分离开始：%.1fs，schema=%s", audio.duration, schema)

        # 分块处理，控制 STFT 内存占用（与总时长解耦）
        seg_len = int(self._config.segment_seconds * audio.sample_rate)
        n = audio.num_samples
        chunks = [
            audio.samples[a:a + seg_len]
            for a in range(0, n, seg_len)
        ] or [audio.samples]

        stems_acc: dict[str, list[np.ndarray]] = {}
        for idx, chunk in enumerate(chunks):
            part = self._separate_chunk(chunk, audio.sample_rate, schema)
            for name, data in part.items():
                stems_acc.setdefault(name, []).append(data)

        result: list[Stem] = []
        for name in schema:
            data = np.concatenate(stems_acc[name], axis=0)
            result.append(Stem(name=name, audio=AudioData(samples=data, sample_rate=audio.sample_rate)))
        return result

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _separate_chunk(
        self, samples: np.ndarray, sr: int, schema: tuple[str, ...]
    ) -> dict[str, np.ndarray]:
        """处理一个音频块，返回 {分轨名: (n,) 或 (n, ch)}。"""
        import librosa  # 延迟导入

        stereo = samples.ndim == 2
        # 统一为 (ch, n) 处理，单/多声道共用同一条广播路径
        y = samples.T if stereo else samples[None, :]
        n = y.shape[-1]

        # 超短信号收缩 n_fft（librosa 对 n_fft > 信号长度会告警）
        n_fft = _N_FFT
        if n < n_fft:
            n_fft = int(2 ** np.floor(np.log2(max(16, n))))

        S = librosa.stft(y, n_fft=n_fft, hop_length=_HOP)  # (ch, freq, time)
        H, P = librosa.decompose.hpss(S, kernel_size=_HPSS_KERNEL, margin=1.0)

        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)  # (freq,)
        out_spectra: dict[str, np.ndarray] = {}

        if schema == ("drums", "other"):
            out_spectra["drums"] = P
            out_spectra["other"] = H
        else:  # STEM_FOUR
            m_bass = freqs < _BASS_CUTOFF
            m_vocal = (freqs >= _VOCAL_LOW) & (freqs <= _VOCAL_HIGH)
            m_other = ~m_bass & ~m_vocal  # 频带互补 → 能量守恒
            out_spectra["drums"] = P
            out_spectra["bass"] = H * m_bass[None, :, None]
            out_spectra["vocals"] = H * m_vocal[None, :, None]
            out_spectra["other"] = H * m_other[None, :, None]

        out: dict[str, np.ndarray] = {}
        for name, Sx in out_spectra.items():
            yx = librosa.istft(Sx, hop_length=_HOP, n_fft=n_fft, length=n)  # (ch, n)
            out[name] = yx.T if stereo else yx[0]
        return out


# ---------------------------------------------------------------------------
# 注册表工厂与可用性
# ---------------------------------------------------------------------------


def spectral_availability() -> AvailabilityInfo:
    """仅依赖核心科学计算栈，恒可用。"""
    return AvailabilityInfo(status=Availability.AVAILABLE)


def spectral_factory(
    schema: tuple[str, ...], config: EngineConfig | None = None
) -> SpectralSeparator:
    return SpectralSeparator(schema=schema, config=config)

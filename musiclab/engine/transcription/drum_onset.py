"""鼓组转录后端（DSP 层）。

管线：onset 检测 → 起音时刻局部频谱的频带能量比分类 → GM 鼓组映射。

分类规则（简单但有效）：
- 低频（<150Hz）占优 → kick（GM 36）
- 高频（>5000Hz）占优 → closed hihat（GM 42）
- 其余宽带起音 → snare（GM 38）
"""

from __future__ import annotations

import logging

import numpy as np

from musiclab.config import EngineConfig
from musiclab.engine.contracts import Availability, AvailabilityInfo
from musiclab.types import AudioData, Note, TranscriptionResult

logger = logging.getLogger(__name__)

#: GM 鼓组 MIDI 音高
_KICK = 36
_SNARE = 38
_HIHAT = 42

#: 频带边界（Hz）
_LOW = 150.0
_HIGH = 5000.0

#: 分类阈值（基于合成信号实测：kick low≈0.9，snare high≈0.44，hihat high≈0.95）
_LOW_RATIO = 0.60
_HIGH_RATIO = 0.70

#: 判定“信号开头即有能量”的比例（相对全局峰值 RMS）
_HEAD_ONSET_RATIO = 0.15
_HEAD_WINDOW = 0.03

#: 打击乐音符的固定时值（秒）
_NOTE_LEN = 0.12
#: 起音分析窗（onset 前后各取几帧）
_ANALYSIS_PAD = 2

_ANALYSIS_SR = 22050
_HOP = 256
_N_FFT = 2048


class DrumOnsetTranscriber:
    """onset + 频带分类的鼓谱转录后端。"""

    name = "drum-onset"

    def __init__(
        self,
        instrument: str = "drums",
        config: EngineConfig | None = None,
    ) -> None:
        if instrument not in ("drums", "auto"):
            from musiclab.engine.errors import UnsupportedInstrumentError

            raise UnsupportedInstrumentError(f"drum-onset 不支持乐器 {instrument!r}")
        self.instrument = "drums"
        self._config = config or EngineConfig()

    # ------------------------------------------------------------------

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        import librosa  # 延迟导入

        y = audio.mono()
        sr = audio.sample_rate
        if sr > _ANALYSIS_SR:
            y = librosa.resample(y, orig_sr=sr, target_sr=_ANALYSIS_SR)
            sr = _ANALYSIS_SR

        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=_HOP)
        # 紧凑 peak-picking 参数：默认 100ms 平滑窗会吞掉被邻接事件
        # （如 125ms 内的 snare→kick）掩蔽的起音，收窄到 ~46ms
        onsets = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=sr,
            hop_length=_HOP,
            units="time",
            backtrack=False,
            pre_max=2,
            post_max=2,
            pre_avg=4,
            post_avg=4,
            delta=0.02,
            wait=8,
        )

        # librosa 的 flux 机制无法报告 t≈0 的起音（没有前置帧可比），
        # 若信号开头是"高能量且随后衰减"的瞬态（鼓的特征，持续音不满足），补起点起音
        head = y[: int(_HEAD_WINDOW * sr)]
        after = y[int(0.05 * sr): int(0.15 * sr)]
        if head.size and after.size:
            global_peak = float(np.sqrt(np.max(y ** 2) + 1e-12))
            head_rms = float(np.sqrt(np.mean(head.astype(np.float64) ** 2)))
            after_rms = float(np.sqrt(np.mean(after.astype(np.float64) ** 2)))
            is_transient = after_rms < 0.6 * head_rms
            if (
                global_peak > 0
                and head_rms > _HEAD_ONSET_RATIO * global_peak
                and is_transient
                and (len(onsets) == 0 or onsets[0] > _HEAD_WINDOW)
            ):
                onsets = np.insert(onsets, 0, 0.0)

        if len(onsets) == 0:
            return TranscriptionResult(
                notes=(),
                instrument=self.instrument,
                backend=self.name,
                duration=audio.duration,
                warnings=("未检测到打击乐起音",),
            )

        S = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=_HOP))  # (freq, time)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)
        low = freqs < _LOW
        high = freqs > _HIGH
        n_frames = S.shape[1]
        max_env = float(onset_env.max()) if onset_env.size else 0.0

        notes: list[Note] = []
        for t in onsets:
            frame = int(round(float(t) * sr / _HOP))
            lo = max(0, frame - _ANALYSIS_PAD)
            hi = min(n_frames, frame + _ANALYSIS_PAD + 1)
            if lo >= hi:
                continue
            window = S[:, lo:hi]
            total = float(window.sum())
            if total <= 0:
                continue
            low_ratio = float(window[low].sum()) / total
            high_ratio = float(window[high].sum()) / total

            if low_ratio > _LOW_RATIO:
                pitch = _KICK
            elif high_ratio > _HIGH_RATIO:
                pitch = _HIHAT
            else:
                pitch = _SNARE

            strength = (
                float(onset_env[min(frame, len(onset_env) - 1)]) / max_env
                if max_env > 0
                else 0.5
            )
            velocity = int(round(40 + 70 * min(1.0, max(0.0, strength))))
            t0 = min(float(t), max(0.0, audio.duration - _NOTE_LEN))
            notes.append(
                Note(
                    start=t0,
                    end=t0 + _NOTE_LEN,
                    pitch=pitch,
                    velocity=velocity,
                    confidence=min(1.0, max(0.0, strength)),
                )
            )

        return TranscriptionResult(
            notes=tuple(notes),
            instrument=self.instrument,
            backend=self.name,
            duration=audio.duration,
        )


# ---------------------------------------------------------------------------
# 注册表工厂与可用性
# ---------------------------------------------------------------------------


def drum_onset_availability() -> AvailabilityInfo:
    return AvailabilityInfo(status=Availability.AVAILABLE)


def drum_onset_factory(
    instrument: str = "drums", config: EngineConfig | None = None
) -> DrumOnsetTranscriber:
    return DrumOnsetTranscriber(instrument=instrument, config=config)

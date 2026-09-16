"""pYIN 转录后端（DSP 降级层）。

单音旋律转录：pYIN 逐帧估计基频 → MIDI 音高序列 → 连续等高段聚合为音符。

能力边界（诚实声明）：无法处理多声部/和弦（polyphonic=False），
注册表不会把混合分轨路由到这里。

实现要点：
- 按乐器设定 F0 搜索范围（贝斯低至 35Hz，需更长分析窗）；
- 分析统一在 22.05kHz / hop 256（约 11.6ms 时值分辨率）；
- 短于 ``MIN_NOTE_DURATION`` 的音符视为噪声丢弃；
- 力度由段内 RMS 相对全曲最大值映射（40–110）。
"""

from __future__ import annotations

import logging

import numpy as np

from musiclab.config import EngineConfig
from musiclab.engine.contracts import Availability, AvailabilityInfo
from musiclab.types import AudioData, Note, TranscriptionResult

logger = logging.getLogger(__name__)

#: 乐器 → F0 搜索范围（Hz）
F0_RANGES: dict[str, tuple[float, float]] = {
    "bass": (35.0, 500.0),
    "vocals": (80.0, 1100.0),
    "guitar": (70.0, 1300.0),
    "piano": (27.5, 2100.0),
    "auto": (65.0, 1600.0),
}

#: pYIN 分析采样率（提速 + 与模型一致的时值分辨率）
ANALYSIS_SR = 22050
_HOP = 256
#: 最短音符时值（秒）
MIN_NOTE_DURATION = 0.06
#: 音高断点内允许的无声间隙（秒），小于该值合并为同一音符
_MERGE_GAP = 0.08


def hz_to_midi(f: float) -> float:
    """频率（Hz）→ MIDI 音高（浮点）。"""
    return 69.0 + 12.0 * np.log2(f / 440.0)


class PyinTranscriber:
    """pYIN 单音旋律转录后端。"""

    name = "pyin"

    def __init__(
        self,
        instrument: str = "auto",
        config: EngineConfig | None = None,
    ) -> None:
        self.instrument = instrument if instrument != "auto" else "melody"
        self._config = config or EngineConfig()
        self._fmin, self._fmax = F0_RANGES.get(instrument, F0_RANGES["auto"])

    # ------------------------------------------------------------------

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        import librosa  # 延迟导入

        y = audio.mono()
        sr = audio.sample_rate
        if sr > ANALYSIS_SR:
            y = librosa.resample(y, orig_sr=sr, target_sr=ANALYSIS_SR)
            sr = ANALYSIS_SR

        # 低音乐器需要更长分析窗（≥4 个周期）
        frame_length = 2048 if self._fmin >= 55.0 else 4096

        f0, voiced, probs = librosa.pyin(
            y,
            fmin=float(self._fmin),
            fmax=float(self._fmax),
            sr=sr,
            frame_length=frame_length,
            hop_length=_HOP,
        )
        times = librosa.times_like(f0, sr=sr, hop_length=_HOP)

        if f0 is None or not np.any(voiced):
            return TranscriptionResult(
                notes=(),
                instrument=self.instrument,
                backend=self.name,
                duration=audio.duration,
                warnings=("未检测到有声调内容",),
            )

        # 帧级 MIDI 音高（未发声帧为 NaN）
        midi = np.full(len(f0), np.nan)
        valid = voiced & np.isfinite(f0)
        midi[valid] = np.round(hz_to_midi(f0[valid]))

        # 力度参考：帧 RMS
        rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=_HOP)[0]
        max_rms = float(rms.max()) if rms.size else 0.0

        runs = self._equal_pitch_runs(midi)
        runs = self._merge_adjacent(runs, times)
        notes: list[Note] = []
        for pitch, i, j in runs:
            t0 = float(times[i])
            t1 = min(float(times[j]) + _HOP / sr, audio.duration)
            if t1 - t0 < MIN_NOTE_DURATION:
                continue
            seg_rms = float(np.mean(rms[i:j + 1])) if rms.size else 0.0
            velocity = self._velocity(seg_rms, max_rms)
            conf = float(np.mean(probs[i:j + 1])) if j >= i else 0.0
            notes.append(
                Note(
                    start=t0,
                    end=t1,
                    pitch=int(pitch),
                    velocity=velocity,
                    confidence=max(0.0, min(1.0, conf)),
                )
            )

        warnings: tuple[str, ...] = ()
        if not notes:
            warnings = ("检测到音高活动，但没有稳定音符",)

        return TranscriptionResult(
            notes=tuple(notes),
            instrument=self.instrument,
            backend=self.name,
            duration=audio.duration,
            warnings=warnings,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _equal_pitch_runs(midi: np.ndarray) -> list[list[int]]:
        """把帧级 MIDI 序列切分为连续等高的 [pitch, i_start, i_end]。"""
        runs: list[list[int]] = []
        i = 0
        n = len(midi)
        while i < n:
            if np.isnan(midi[i]):
                i += 1
                continue
            j = i
            while j + 1 < n and midi[j + 1] == midi[i]:
                j += 1
            runs.append([int(midi[i]), i, j])
            i = j + 1
        return runs

    @staticmethod
    def _merge_adjacent(runs: list[list[int]], times: np.ndarray) -> list[list[int]]:
        """合并音高相同、间隙小于阈值的两段（容忍换气/瞬态丢帧）。"""
        if not runs:
            return []
        merged = [runs[0]]
        for run in runs[1:]:
            last = merged[-1]
            same_pitch = last[0] == run[0]
            gap = float(times[run[1]] - times[last[2]])
            if same_pitch and gap < _MERGE_GAP:
                last[2] = run[2]
            else:
                merged.append(run)
        return merged

    @staticmethod
    def _velocity(seg_rms: float, max_rms: float) -> int:
        if max_rms <= 0:
            return 64
        ratio = max(0.0, min(1.0, seg_rms / max_rms))
        return int(round(40 + 70 * ratio))


# ---------------------------------------------------------------------------
# 注册表工厂与可用性
# ---------------------------------------------------------------------------


def pyin_availability() -> AvailabilityInfo:
    """librosa 属核心依赖，恒可用。"""
    return AvailabilityInfo(status=Availability.AVAILABLE)


def pyin_factory(
    instrument: str = "auto", config: EngineConfig | None = None
) -> PyinTranscriber:
    return PyinTranscriber(instrument=instrument, config=config)

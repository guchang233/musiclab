"""Basic Pitch 转录后端（ML 层，多音轨）。

依赖 basic-pitch（可选）：``pip install musiclab[basic-pitch]``。

能力：多音轨音符转录（和弦/混合分轨），输出含 pitch bend 事件。
当前 MVP 版本只取 note_events 的音符本体（音高/起止/力度），
pitch bend 的写出留待钢琴卷帘编辑器需求一起设计。
"""

from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np

from musiclab.config import EngineConfig
from musiclab.engine.contracts import Availability, AvailabilityInfo
from musiclab.engine.errors import BackendNotAvailableError
from musiclab.types import AudioData, Note, TranscriptionResult

logger = logging.getLogger(__name__)

#: Basic Pitch ICASSP 模型的工作采样率
ANALYSIS_SR = 22050

_MIN_NOTE_LENGTH_MS = 80


@lru_cache(maxsize=1)
def basic_pitch_availability() -> AvailabilityInfo:
    """检查 basic_pitch 是否可导入（结果进程内缓存）。"""
    try:
        import basic_pitch  # noqa: F401
    except ImportError as exc:
        return AvailabilityInfo(
            status=Availability.MISSING_DEPENDENCY,
            reason=f"缺少依赖：{exc}",
            install_hint="pip install musiclab[basic-pitch]",
        )
    return AvailabilityInfo(status=Availability.AVAILABLE)


class BasicPitchTranscriber:
    """Spotify Basic Pitch 多音轨转录后端。"""

    name = "basic-pitch"

    def __init__(
        self,
        instrument: str = "auto",
        config: EngineConfig | None = None,
    ) -> None:
        try:
            from basic_pitch.inference import predict  # noqa: F401

            self._predict_fn = predict
        except ImportError as exc:
            raise BackendNotAvailableError(
                "basic-pitch", f"缺少依赖：{exc}", "pip install musiclab[basic-pitch]"
            ) from exc
        self.instrument = instrument
        self._config = config or EngineConfig()

    # ------------------------------------------------------------------

    def transcribe(self, audio: AudioData) -> TranscriptionResult:
        import librosa  # 延迟导入

        y = audio.mono()
        if audio.sample_rate != ANALYSIS_SR:
            y = librosa.resample(y, orig_sr=audio.sample_rate, target_sr=ANALYSIS_SR)

        try:
            _, _, note_events = self._predict_fn(
                np.ascontiguousarray(y, dtype=np.float32),
                minimum_note_length=_MIN_NOTE_LENGTH_MS,
            )
        except TypeError:
            # 兼容旧版本签名（无 minimum_note_length 参数）
            _, _, note_events = self._predict_fn(
                np.ascontiguousarray(y, dtype=np.float32)
            )
        except Exception as exc:  # noqa: BLE001 —— 推理失败的统一出口
            raise BackendNotAvailableError(
                "basic-pitch", f"推理失败：{exc}"
            ) from exc

        notes: list[Note] = []
        for event in note_events:
            # note_event 结构：(pitch:int, start:float, end:float, velocity:int, pitch_bends)
            pitch = int(event[0])
            start = float(event[1])
            end = float(event[2])
            velocity = int(event[3]) if len(event) > 3 and event[3] else 64
            velocity = max(1, min(127, velocity))
            notes.append(
                Note(start=start, end=max(start, end), pitch=pitch, velocity=velocity)
            )

        notes.sort(key=lambda n: (n.start, n.pitch))
        return TranscriptionResult(
            notes=tuple(notes),
            instrument=self.instrument,
            backend=self.name,
            duration=audio.duration,
        )


def basic_pitch_factory(
    instrument: str = "auto", config: EngineConfig | None = None
) -> BasicPitchTranscriber:
    return BasicPitchTranscriber(instrument=instrument, config=config)

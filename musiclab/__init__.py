"""musiclab —— 音乐工作站内核。

公开 API：
- 数据类型：AudioData / Note / Stem / TranscriptionResult
- 音频 I/O：load_audio / write_wav / write_stems
- 引擎注册表：build_default_registry / ModelRegistry
- 管线：TranscriptionPipeline / EngineConfig
"""

from musiclab.types import (
    AudioData,
    Note,
    Stem,
    TranscriptionResult,
    STEM_FOUR,
    STEM_PRESETS,
    STEM_SIX,
    STEM_TWO,
)
from musiclab.config import EngineConfig
from musiclab.audio import load_audio, write_wav, write_stems
from musiclab.engine import ModelRegistry, build_default_registry
from musiclab.midi import write_midi
from musiclab.pipeline import TranscriptionPipeline, PipelineReport

__version__ = "0.1.0"

__all__ = [
    "AudioData",
    "Note",
    "Stem",
    "TranscriptionResult",
    "STEM_TWO",
    "STEM_FOUR",
    "STEM_SIX",
    "STEM_PRESETS",
    "EngineConfig",
    "load_audio",
    "write_wav",
    "write_stems",
    "ModelRegistry",
    "build_default_registry",
    "write_midi",
    "TranscriptionPipeline",
    "PipelineReport",
    "__version__",
]

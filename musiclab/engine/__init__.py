"""引擎层：契约、注册表与各后端实现。"""

from musiclab.engine.contracts import (
    Availability,
    AvailabilityInfo,
    BackendDiagnostics,
    BackendSpec,
    Separator,
    Transcriber,
)
from musiclab.engine.errors import (
    AudioLoadError,
    BackendNotAvailableError,
    CacheError,
    MusicLabError,
    PipelineError,
    UnsupportedInstrumentError,
    UnsupportedStemSchemaError,
)
from musiclab.engine.registry import (
    ModelRegistry,
    build_default_registry,
    get_default_registry,
)

__all__ = [
    "Availability",
    "AvailabilityInfo",
    "BackendDiagnostics",
    "BackendSpec",
    "Separator",
    "Transcriber",
    "AudioLoadError",
    "BackendNotAvailableError",
    "CacheError",
    "MusicLabError",
    "PipelineError",
    "UnsupportedInstrumentError",
    "UnsupportedStemSchemaError",
    "ModelRegistry",
    "build_default_registry",
    "get_default_registry",
]

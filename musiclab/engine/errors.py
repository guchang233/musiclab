"""统一异常体系。"""

from __future__ import annotations


class MusicLabError(Exception):
    """所有 musiclab 异常的基类。"""


class AudioLoadError(MusicLabError):
    """音频加载/解码失败。"""


class BackendNotAvailableError(MusicLabError):
    """请求的后端不可用（依赖缺失、模型缺失等）。

    Attributes:
        backend: 后端名。
        reason: 不可用原因。
        install_hint: 可选的安装指引。
    """

    def __init__(self, backend: str, reason: str, install_hint: str = "") -> None:
        self.backend = backend
        self.reason = reason
        self.install_hint = install_hint
        message = f"后端 {backend!r} 不可用：{reason}"
        if install_hint:
            message += f"（{install_hint}）"
        super().__init__(message)


class UnsupportedStemSchemaError(MusicLabError):
    """后端不支持请求的分轨方案。"""

    def __init__(self, backend: str, schema: tuple[str, ...]) -> None:
        self.backend = backend
        self.schema = schema
        super().__init__(f"后端 {backend!r} 不支持分轨方案 {schema}")


class UnsupportedInstrumentError(MusicLabError):
    """后端不支持请求的乐器。"""


class PipelineError(MusicLabError):
    """管线执行失败。"""


class CacheError(MusicLabError):
    """缓存读写失败。"""

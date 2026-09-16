"""内容寻址产物缓存。

键 = sha256(步骤名 + 后端名 + 规范化参数 + 音频内容摘要)。
命中即免重算：分离（昂贵）的结果被缓存后，反复调转录参数无需重跑。

写入语义：临时目录 + 原子 rename；并发写同一键时"先到者胜"，
后到者丢弃自己的临时副本，保证缓存目录永远处于完整状态。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Mapping

import numpy as np

from musiclab.engine.errors import CacheError
from musiclab.types import AudioData

logger = logging.getLogger(__name__)

#: manifest 文件名（一个缓存条目完成的标志）
MANIFEST_NAME = "manifest.json"


def audio_digest(audio: AudioData) -> str:
    """音频内容的确定性摘要（采样率 + 形状 + 采样数据）。"""
    h = hashlib.sha256()
    h.update(str(audio.sample_rate).encode())
    h.update(str(audio.samples.shape).encode())
    h.update(np.ascontiguousarray(audio.samples, dtype=np.float32).tobytes())
    return h.hexdigest()


def _canonical_params(params: Mapping[str, object]) -> str:
    """参数规范化 JSON：键排序、元组转列表、Path 转字符串。"""

    def _conv(value: object) -> object:
        if isinstance(value, tuple):
            return [_conv(v) for v in value]
        if isinstance(value, list):
            return [_conv(v) for v in value]
        if isinstance(value, dict):
            return {str(k): _conv(v) for k, v in value.items()}
        if isinstance(value, Path):
            return str(value)
        return value

    return json.dumps(_conv(dict(params)), sort_keys=True, ensure_ascii=False)


class ArtifactCache:
    """磁盘产物缓存。"""

    def __init__(self, root: str | Path | None, *, enabled: bool = True) -> None:
        self._root = Path(root) if root is not None else None
        self._enabled = bool(enabled and root is not None)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def root(self) -> Path | None:
        return self._root

    # ------------------------------------------------------------------
    # 键与查询
    # ------------------------------------------------------------------

    def key(
        self,
        *,
        step: str,
        backend: str,
        params: Mapping[str, object],
        audio: AudioData,
    ) -> str:
        """计算缓存键。音频以内容摘要参与，路径无关。"""
        payload = {
            "step": step,
            "backend": backend,
            "params": _canonical_params(params),
            "audio": audio_digest(audio),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def entry_dir(self, key: str) -> Path:
        """缓存键对应的目录（两级分桶避免单目录文件过多）。"""
        if self._root is None:
            raise CacheError("缓存未启用")
        return self._root / key[:2] / key

    def lookup(self, key: str) -> Path | None:
        """命中则返回缓存目录，否则 None。"""
        if not self._enabled:
            return None
        entry = self.entry_dir(key)
        if (entry / MANIFEST_NAME).is_file():
            return entry
        return None

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        files: Mapping[str, Path],
        *,
        meta: Mapping[str, object] | None = None,
    ) -> Path:
        """把一组文件原子地存入缓存。

        Args:
            key: 缓存键。
            files: {缓存内文件名: 源文件路径}。
            meta: 写入 manifest 的附加元数据。

        Returns:
            缓存目录路径。
        """
        if not self._enabled or self._root is None:
            raise CacheError("缓存未启用")

        final = self.entry_dir(key)
        if (final / MANIFEST_NAME).is_file():
            return final  # 已有完整缓存

        token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        tmp = final.parent / f".tmp-{key}-{token}"
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            for name, src in files.items():
                if not Path(src).is_file():
                    raise CacheError(f"待缓存文件不存在：{src}")
                shutil.copy2(src, tmp / name)
            manifest = {
                "key": key,
                "files": sorted(files.keys()),
                **(dict(meta) if meta else {}),
            }
            (tmp / MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, final)  # 原子提交（目标不存在时才成功）
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            if (final / MANIFEST_NAME).is_file():
                return final  # 并发竞争：他人先写入，用既有结果
            raise
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

        logger.debug("缓存写入：%s（%d 个文件）", key[:12], len(files))
        return final

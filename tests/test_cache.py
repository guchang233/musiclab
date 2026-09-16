"""产物缓存测试。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.engine.errors import CacheError
from musiclab.pipeline.cache import ArtifactCache, audio_digest

from tests.conftest import audio, sine


@pytest.fixture()
def cache(tmp_path):
    return ArtifactCache(tmp_path / "cache")


@pytest.fixture()
def a():
    return audio(sine(440.0, 0.1))


class TestKeys:
    def test_键对相同输入确定(self, cache, a):
        k1 = cache.key(step="separate", backend="x", params={"schema": ["a"]}, audio=a)
        k2 = cache.key(step="separate", backend="x", params={"schema": ["a"]}, audio=a)
        assert k1 == k2

    def test_参数不同则键不同(self, cache, a):
        k1 = cache.key(step="t", backend="x", params={"p": 1}, audio=a)
        k2 = cache.key(step="t", backend="x", params={"p": 2}, audio=a)
        assert k1 != k2

    def test_后端不同则键不同(self, cache, a):
        k1 = cache.key(step="t", backend="x", params={}, audio=a)
        k2 = cache.key(step="t", backend="y", params={}, audio=a)
        assert k1 != k2

    def test_音频内容不同则键不同(self):
        a1 = audio(sine(440.0, 0.1))
        a2 = audio(sine(441.0, 0.1))
        assert audio_digest(a1) != audio_digest(a2)

    def test_元组与列表参数产生相同键(self, cache, a):
        k1 = cache.key(step="t", backend="x", params={"s": ("a", "b")}, audio=a)
        k2 = cache.key(step="t", backend="x", params={"s": ["a", "b"]}, audio=a)
        assert k1 == k2


class TestStore:
    def test_存取往返(self, cache, a, tmp_path):
        src = tmp_path / "s.wav"
        src.write_bytes(b"fake-wav-bytes")
        key = cache.key(step="s", backend="x", params={}, audio=a)

        assert cache.lookup(key) is None
        entry = cache.store(key, {"stem.wav": src}, meta={"schema": ["vocals"]})
        assert (entry / "manifest.json").is_file()
        assert (entry / "stem.wav").read_bytes() == b"fake-wav-bytes"

        hit = cache.lookup(key)
        assert hit is not None
        assert (hit / "stem.wav").read_bytes() == b"fake-wav-bytes"

    def test_重复写入幂等(self, cache, a, tmp_path):
        src = tmp_path / "s.wav"
        src.write_bytes(b"v1")
        key = cache.key(step="s", backend="x", params={}, audio=a)
        cache.store(key, {"f": src})
        # 第二次写入同键：既有缓存优先
        entry = cache.store(key, {"f": src})
        assert (entry / "f").read_bytes() == b"v1"

    def test_源文件不存在(self, cache, a, tmp_path):
        key = cache.key(step="s", backend="x", params={}, audio=a)
        with pytest.raises(CacheError):
            cache.store(key, {"f": tmp_path / "ghost.wav"})

    def test_键分桶两级目录(self, cache, a, tmp_path):
        src = tmp_path / "s.wav"
        src.write_bytes(b"x")
        key = cache.key(step="s", backend="x", params={}, audio=a)
        entry = cache.store(key, {"f": src})
        assert entry.parent.name == key[:2]
        assert entry.name == key


class TestDisabled:
    def test_禁用时lookup恒空(self, tmp_path, a):
        cache = ArtifactCache(None)
        key = cache.key(step="s", backend="x", params={}, audio=a)
        assert cache.lookup(key) is None

    def test_禁用时store报错(self, tmp_path, a):
        cache = ArtifactCache(None)
        key = cache.key(step="s", backend="x", params={}, audio=a)
        with pytest.raises(CacheError):
            cache.store(key, {})

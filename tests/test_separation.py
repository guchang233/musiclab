"""分离引擎测试（spectral-hpss DSP 后端，确定性合成音频）。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.config import EngineConfig
from musiclab.engine.errors import UnsupportedStemSchemaError
from musiclab.engine.separation.spectral import SpectralSeparator
from musiclab.types import STEM_FOUR, STEM_SIX

from tests.conftest import audio, kick, mix_at, sine


@pytest.fixture(scope="session")
def separated(song_audio):
    sep = SpectralSeparator(schema=STEM_FOUR, config=EngineConfig(segment_seconds=30.0))
    return sep.separate(song_audio, STEM_FOUR)


class TestSpectralSeparator:
    def test_分轨数量与命名(self, separated):
        assert [s.name for s in separated] == list(STEM_FOUR)

    def test_形状与采样率保持(self, separated, song_audio):
        for stem in separated:
            assert stem.audio.sample_rate == song_audio.sample_rate
            assert stem.audio.num_samples == song_audio.num_samples
            assert stem.audio.channels == song_audio.channels

    def test_能量守恒_分轨之和近似原信号(self, separated, song_audio):
        total = sum(s.audio.samples for s in separated)
        # HPSS margin=1.0 → H+P=S 严格互补；频带掩码互斥 → 能量守恒
        # 剩余误差仅来自 STFT 往返的数值精度
        err = np.max(np.abs(total - song_audio.samples))
        assert err < 5e-3, f"重建误差 {err:.5f}"

    def test_鼓的能量主要落入drums轨(self):
        drums_only = audio(mix_at([(i * 0.5, kick()) for i in range(4)], total=2.0))
        sep = SpectralSeparator(schema=STEM_FOUR)
        stems = {s.name: s for s in sep.separate(drums_only, STEM_FOUR)}
        in_drums = np.sum(np.abs(stems["drums"].audio.samples))
        in_bass = np.sum(np.abs(stems["bass"].audio.samples))
        # 衰减正弦的持续段有谐波成分（会分给 bass），但打击起音能量必须以 drums 为主
        assert in_drums > in_bass

    def test_贝斯的能量主要落入bass轨(self, song_audio, separated):
        stems = {s.name: s for s in separated}
        # 贝斯线在 E2/G2/A2（82–110Hz），应落在 bass（<150Hz 谐波）
        bass_energy = np.sum(np.abs(stems["bass"].audio.samples))
        vocals_energy = np.sum(np.abs(stems["vocals"].audio.samples))
        assert bass_energy > 2 * vocals_energy

    def test_立体声处理(self, sr):
        stereo = np.stack([sine(440.0, 0.5), sine(440.0, 0.5)], axis=1)
        sep = SpectralSeparator(schema=STEM_FOUR)
        stems = sep.separate(audio(stereo), STEM_FOUR)
        for stem in stems:
            assert stem.audio.channels == 2
            assert stem.audio.num_samples == stereo.shape[0]

    def test_分块处理不破坏长度(self, song_audio):
        # 强制 1 秒分块 → 3 秒音频被切成 3 块
        sep = SpectralSeparator(
            schema=STEM_FOUR, config=EngineConfig(segment_seconds=1.0)
        )
        stems = sep.separate(song_audio, STEM_FOUR)
        for stem in stems:
            assert stem.audio.num_samples == song_audio.num_samples

    def test_不支持的分轨方案(self, song_audio):
        sep = SpectralSeparator(schema=STEM_FOUR)
        with pytest.raises(UnsupportedStemSchemaError):
            sep.separate(song_audio, STEM_SIX)

    def test_超短音频(self):
        tiny = audio(sine(440.0, 0.05))
        sep = SpectralSeparator(schema=STEM_FOUR)
        stems = sep.separate(tiny, STEM_FOUR)
        assert len(stems) == 4

"""值对象验证测试。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.types import AudioData, Note, TranscriptionResult


class TestAudioData:
    def test_拒绝非数组(self):
        with pytest.raises(TypeError):
            AudioData(samples=[0.0, 1.0], sample_rate=44100)

    def test_拒绝错误维度(self):
        with pytest.raises(ValueError):
            AudioData(samples=np.zeros((2, 2, 2), dtype=np.float32), sample_rate=44100)

    def test_拒绝空音频(self):
        with pytest.raises(ValueError):
            AudioData(samples=np.zeros(0, dtype=np.float32), sample_rate=44100)

    def test_拒绝非法采样率(self):
        with pytest.raises(ValueError):
            AudioData(samples=np.zeros(10, dtype=np.float32), sample_rate=0)

    def test_拒绝整数类型(self):
        with pytest.raises(TypeError):
            AudioData(samples=np.zeros(10, dtype=np.int16), sample_rate=44100)

    def test_自动转float32(self):
        a = AudioData(samples=np.zeros(10, dtype=np.float64), sample_rate=44100)
        assert a.samples.dtype == np.float32

    def test_属性(self):
        stereo = np.random.default_rng(0).standard_normal((1000, 2)).astype(np.float32)
        a = AudioData(samples=stereo, sample_rate=1000)
        assert a.channels == 2
        assert a.num_samples == 1000
        assert a.duration == pytest.approx(1.0)
        assert a.mono().shape == (1000,)
        assert a.channel(1).shape == (1000,)

    def test_channel越界(self):
        a = AudioData(samples=np.zeros(10, dtype=np.float32), sample_rate=1000)
        with pytest.raises(IndexError):
            a.channel(1)


class TestNote:
    def test_合法音符(self):
        n = Note(start=0.0, end=0.5, pitch=60, velocity=80, confidence=0.9)
        assert n.duration == pytest.approx(0.5)

    def test_音高越界(self):
        with pytest.raises(ValueError):
            Note(start=0, end=1, pitch=128)

    def test_力度越界(self):
        with pytest.raises(ValueError):
            Note(start=0, end=1, pitch=60, velocity=0)

    def test_时间倒置(self):
        with pytest.raises(ValueError):
            Note(start=1.0, end=0.5, pitch=60)

    def test_置信度越界(self):
        with pytest.raises(ValueError):
            Note(start=0, end=1, pitch=60, confidence=1.5)


class TestTranscriptionResult:
    def test_列表规范化为元组(self):
        r = TranscriptionResult(
            notes=[Note(start=0, end=1, pitch=60)], instrument="auto", backend="t", duration=1.0
        )
        assert isinstance(r.notes, tuple)
        assert not r.empty

"""音频 I/O 测试。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.audio import load_audio, resample_audio, write_stems, write_wav
from musiclab.engine.errors import AudioLoadError
from musiclab.types import AudioData, Stem

from conftest import audio, sine


class TestLoadAudio:
    def test_文件不存在(self, tmp_path):
        with pytest.raises(AudioLoadError):
            load_audio(tmp_path / "nope.wav")

    def test_wav往返(self, tmp_path, sr):
        path = tmp_path / "x.wav"
        write_wav(path, audio(sine(440.0, 0.5)))
        loaded = load_audio(path)
        assert loaded.sample_rate == sr
        assert loaded.channels == 1
        # PCM_16 量化误差不超过 1/32768
        assert np.max(np.abs(loaded.samples - sine(440.0, 0.5))) < 1 / 32768

    def test_重采样加载(self, tmp_path, sr):
        path = tmp_path / "x.wav"
        write_wav(path, audio(sine(440.0, 0.5)))
        loaded = load_audio(path, sample_rate=16000)
        assert loaded.sample_rate == 16000
        assert loaded.duration == pytest.approx(0.5, abs=0.01)

    def test_立体声往返(self, tmp_path, sr):
        stereo = np.stack([sine(440.0, 0.3), sine(880.0, 0.3)], axis=1)
        path = tmp_path / "st.wav"
        write_wav(path, AudioData(samples=stereo, sample_rate=sr))
        loaded = load_audio(path)
        assert loaded.channels == 2


class TestResample:
    def test_无变化(self, mono_sine_audio):
        assert resample_audio(mono_sine_audio, mono_sine_audio.sample_rate) is mono_sine_audio

    def test_降采样(self, mono_sine_audio):
        out = resample_audio(mono_sine_audio, 11025)
        assert out.sample_rate == 11025
        assert out.num_samples == pytest.approx(mono_sine_audio.num_samples / 2, rel=0.01)


class TestWriteStems:
    def test_批量写出与float无损往返(self, tmp_path, sr):
        stems = [
            Stem(name="a", audio=audio(sine(440.0, 0.2))),
            Stem(name="b", audio=audio(sine(880.0, 0.2))),
        ]
        paths = write_stems(tmp_path / "out", stems)
        assert [p.name for p in paths] == ["a.wav", "b.wav"]
        # float32 无损往返 → 字节级一致（缓存键稳定的前提）
        for stem, path in zip(stems, paths):
            reloaded = load_audio(path)
            assert np.array_equal(reloaded.samples, stem.audio.samples)

    def test_重复分轨名(self, tmp_path):
        stems = [Stem(name="a", audio=audio(sine(440.0, 0.1)))] * 2
        with pytest.raises(ValueError):
            write_stems(tmp_path / "out", stems)

"""转录引擎测试（pyin + drum-onset，确定性合成音频）。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.engine.transcription.drum_onset import DrumOnsetTranscriber
from musiclab.engine.transcription.pyin_backend import PyinTranscriber

from conftest import audio


class TestPyinTranscriber:
    def test_单音准确率(self, mono_sine_audio):
        result = PyinTranscriber().transcribe(mono_sine_audio)
        assert len(result.notes) == 1
        assert result.notes[0].pitch == 69  # A4
        assert result.notes[0].start < 0.1
        assert result.notes[0].end > 0.9

    def test_旋律音符序列(self, melody_audio):
        result = PyinTranscriber().transcribe(melody_audio)
        pitches = [n.pitch for n in result.notes]
        assert pitches == [60, 64, 67]  # C4 E4 G4

    def test_乐器标签写入结果(self, melody_audio):
        result = PyinTranscriber(instrument="bass").transcribe(melody_audio)
        assert result.instrument == "bass"
        assert result.backend == "pyin"

    def test_静音输入(self):
        silent = audio(np.zeros(22050, dtype=np.float32))
        result = PyinTranscriber().transcribe(silent)
        assert result.empty
        assert result.warnings

    def test_力度范围合法(self, mono_sine_audio):
        result = PyinTranscriber().transcribe(mono_sine_audio)
        for n in result.notes:
            assert 1 <= n.velocity <= 127
            assert 0.0 <= n.confidence <= 1.0


class TestDrumOnsetTranscriber:
    def test_鼓件分类(self, drums_audio):
        result = DrumOnsetTranscriber().transcribe(drums_audio)
        pitches = [n.pitch for n in result.notes]
        # 4 kicks（GM36）+ 2 snares（GM38）+ 4 hihats（GM42）
        assert pitches.count(36) == 4
        assert pitches.count(38) == 2
        assert pitches.count(42) == 4

    def test_起音时刻准确(self, drums_audio):
        result = DrumOnsetTranscriber().transcribe(drums_audio)
        # 第一个起音应在 0 附近
        assert result.notes[0].start < 0.05

    def test_乐器与后端标签(self, drums_audio):
        result = DrumOnsetTranscriber().transcribe(drums_audio)
        assert result.instrument == "drums"
        assert result.backend == "drum-onset"

    def test_纯持续音无起音(self, mono_sine_audio):
        result = DrumOnsetTranscriber().transcribe(mono_sine_audio)
        # 纯正弦没有打击起音（onset 检测可能报 0 或极少数假阳性）
        assert len(result.notes) <= 1

    def test_不支持的乐器(self):
        from musiclab.engine.errors import UnsupportedInstrumentError

        with pytest.raises(UnsupportedInstrumentError):
            DrumOnsetTranscriber(instrument="piano")

    def test_力度与置信度合法(self, drums_audio):
        result = DrumOnsetTranscriber().transcribe(drums_audio)
        for n in result.notes:
            assert 1 <= n.velocity <= 127
            assert 0.0 <= n.confidence <= 1.0

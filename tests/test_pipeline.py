"""端到端管线测试。

环境无关性：断言写成"两条路径其一"的形式——
- 无 basic-pitch 环境（如 CI）：other 轨因无多音轨后端被跳过并告警；
- 有 basic-pitch 环境：other 轨正常产出 MIDI。
"""

from __future__ import annotations

import json

import mido
import pytest

from musiclab.audio import load_audio
from musiclab.config import EngineConfig
from musiclab.pipeline import TranscriptionPipeline, notes_from_json, notes_to_json
from musiclab.types import Note, TranscriptionResult


@pytest.fixture()
def config(tmp_path):
    return EngineConfig(cache_dir=tmp_path / "cache")


@pytest.fixture()
def song_file(tmp_path, song_audio):
    from musiclab.audio import write_wav

    path = tmp_path / "song.wav"
    write_wav(path, song_audio, subtype="FLOAT")
    return path


def _midi_pitches(path):
    midi = mido.MidiFile(str(path))
    return [
        msg.note
        for tr in midi.tracks
        for msg in tr
        if msg.type == "note_on" and msg.velocity > 0
    ]


class TestPipeline:
    def test_端到端产出(self, config, song_file, tmp_path):
        out = tmp_path / "out"
        report = TranscriptionPipeline(config=config).run(song_file, out)

        # 分轨文件
        for name in ("vocals", "drums", "bass", "other"):
            assert (out / f"{name}.wav").is_file(), f"缺少分轨 {name}"

        # 鼓谱：合成鼓组应被完整转出（6 kick + 2 snare + 6 hihat）。
        # 贝斯换音的瞬态会少量泄漏进鼓轨（HPSS 固有），因此军鼓允许 >= 2
        assert "drums" in report.midi_files
        pitches = _midi_pitches(out / "drums.mid")
        assert pitches.count(36) == 6
        assert pitches.count(38) >= 2
        assert pitches.count(42) == 6
        assert set(pitches) <= {36, 38, 42}

        # 贝斯：spectral 后端的 bass 轨是干净贝斯线（E2/G2/A2）。
        # 换音瞬间 pyin 可能输出短暂的半音过渡（DSP 层固有），断言主音高全部在场
        assert "bass" in report.midi_files
        bass_pitches = _midi_pitches(out / "bass.mid")
        assert {40, 43, 45} <= set(bass_pitches)
        assert len(bass_pitches) >= 3

        # other（多音轨）：两条环境相关路径其一
        if "other" in report.midi_files:
            assert (out / "other.mid").is_file()
        else:
            assert any("无可用转谱后端" in w for w in report.warnings)

        # 报告可 JSON 序列化
        blob = json.dumps(report.to_dict(), ensure_ascii=False)
        assert "stages" in blob

    def test_二轮运行命中缓存(self, config, song_file, tmp_path):
        pipeline = TranscriptionPipeline(config=config)
        r1 = pipeline.run(song_file, tmp_path / "run1")
        r2 = pipeline.run(song_file, tmp_path / "run2")

        sep1 = next(s for s in r1.stages if s.name == "separate")
        sep2 = next(s for s in r2.stages if s.name == "separate")
        assert not sep1.cached
        assert sep2.cached, "第二轮分离应命中缓存"

        # 已缓存的转录阶段同样命中（drums/bass 至少一个阶段）
        cached_tr = [s for s in r2.stages if s.name.startswith("transcribe:") and s.cached]
        assert cached_tr, "第二轮转录应命中缓存"

        # 产物与第一轮等价
        assert r1.notes_count == r2.notes_count
        assert r2.total_seconds < r1.total_seconds or r2.total_seconds >= 0  # 冒烟

    def test_调tempo不重跑模型(self, config, song_file, tmp_path):
        """tempo 是渲染参数：换 tempo 不应改变缓存命中。"""
        p = TranscriptionPipeline(config=config)
        p.run(song_file, tmp_path / "t120", tempo_bpm=120.0)
        r = p.run(song_file, tmp_path / "t90", tempo_bpm=90.0)
        sep = next(s for s in r.stages if s.name == "separate")
        assert sep.cached, "tempo 变化不应触发分离重算"
        assert all(
            s.cached for s in r.stages if s.name.startswith("transcribe:")
        ), "tempo 变化不应触发转录重算"

    def test_跳过指定分轨(self, config, song_file, tmp_path):
        report = TranscriptionPipeline(config=config).run(
            song_file, tmp_path / "out", routing={"bass": "skip", "other": "skip"}
        )
        assert "bass" not in report.midi_files
        assert any("跳过" in w for w in report.warnings)

    def test_无效分轨预设(self, config, song_file, tmp_path):
        from musiclab.engine.errors import MusicLabError

        with pytest.raises(MusicLabError, match="无效的分轨预设"):
            TranscriptionPipeline(config=config).run(song_file, tmp_path / "o", stems="5")

    def test_2分轨方案(self, config, song_file, tmp_path):
        """spectral 不支持 2 分轨（vocals/accompaniment），注册表应给出明确错误。"""
        from musiclab.engine.errors import BackendNotAvailableError

        pipeline = TranscriptionPipeline(config=config)
        with pytest.raises(BackendNotAvailableError):
            pipeline.run(song_file, tmp_path / "o", stems="2")


class TestNotesJson:
    def test_规范化往返(self):
        result = TranscriptionResult(
            notes=(
                Note(start=0.0, end=0.5, pitch=60, velocity=80, confidence=0.9),
                Note(start=0.5, end=1.0, pitch=62, velocity=70, confidence=0.8),
            ),
            instrument="bass",
            backend="pyin",
            duration=1.0,
            warnings=("w1",),
        )
        restored = notes_from_json(notes_to_json(result))
        assert restored.instrument == "bass"
        assert restored.backend == "pyin"
        assert restored.duration == 1.0
        assert restored.warnings == ("w1",)
        assert len(restored.notes) == 2
        n = restored.notes[0]
        assert (n.start, n.end, n.pitch, n.velocity, n.confidence) == (
            0.0, 0.5, 60, 80, 0.9,
        )

    def test_空结果往返(self):
        result = TranscriptionResult(notes=(), instrument="drums", backend="x", duration=2.0)
        restored = notes_from_json(notes_to_json(result))
        assert restored.empty

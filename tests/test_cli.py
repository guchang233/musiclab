"""CLI 冒烟测试。"""

from __future__ import annotations

import pytest

from musiclab.cli import main

from tests.conftest import audio, melody


@pytest.fixture()
def melody_wav(tmp_path):
    from musiclab.audio import write_wav

    path = tmp_path / "melody.wav"
    write_wav(path, audio(melody([(60, 0.4), (64, 0.4), (67, 0.4)])), subtype="FLOAT")
    return path


@pytest.fixture()
def song_wav(tmp_path, song_audio):
    from musiclab.audio import write_wav

    path = tmp_path / "song.wav"
    write_wav(path, song_audio, subtype="FLOAT")
    return path


class TestBackends:
    def test_列出全部后端(self, capsys):
        assert main(["backends"]) == 0
        out = capsys.readouterr().out
        for name in ("demucs", "spectral-hpss", "basic-pitch", "pyin", "drum-onset"):
            assert name in out


class TestSeparate:
    def test_默认4分轨(self, tmp_path, song_wav, capsys):
        out = tmp_path / "stems"
        assert main(["separate", str(song_wav), "-o", str(out)]) == 0
        for name in ("vocals", "drums", "bass", "other"):
            assert (out / f"{name}.wav").is_file()

    def test_指定后端(self, tmp_path, song_wav, capsys):
        out = tmp_path / "stems"
        assert main(["separate", str(song_wav), "-o", str(out), "--backend", "spectral-hpss"]) == 0
        assert (out / "drums.wav").is_file()


class TestTranscribe:
    def test_单音转谱(self, tmp_path, melody_wav, capsys):
        out = tmp_path / "m.mid"
        assert main(["transcribe", str(melody_wav), "-o", str(out), "--backend", "pyin"]) == 0
        assert out.is_file()

    def test_鼓谱转谱(self, tmp_path, song_wav):
        out = tmp_path / "d.mid"
        assert main(["transcribe", str(song_wav), "-o", str(out), "--instrument", "drums"]) == 0
        assert out.is_file()

    def test_未知后端名退出码3(self, tmp_path, melody_wav):
        code = main(["transcribe", str(melody_wav), "-o", str(tmp_path / "x.mid"), "--backend", "ghost"])
        assert code == 3

    def test_文件不存在退出码2(self, tmp_path):
        code = main(["transcribe", str(tmp_path / "nope.wav"), "-o", str(tmp_path / "x.mid")])
        assert code == 2


class TestPipelineCmd:
    def test_端到端(self, tmp_path, song_wav, capsys):
        out = tmp_path / "out"
        assert main(["pipeline", str(song_wav), "-o", str(out), "--skip", "other"]) == 0
        assert (out / "drums.mid").is_file()
        assert (out / "bass.mid").is_file()
        assert not (out / "other.mid").is_file()  # 显式跳过

    def test_报告JSON(self, tmp_path, song_wav, capsys):
        out = tmp_path / "out"
        assert main(["pipeline", str(song_wav), "-o", str(out), "--json", "--skip", "other"]) == 0
        blob = capsys.readouterr().out
        report = __import__("json").loads(blob)
        assert report["schema"] == ["vocals", "drums", "bass", "other"]
        assert any(s["name"] == "separate" for s in report["stages"])

    def test_无缓存模式(self, tmp_path, song_wav):
        out = tmp_path / "out"
        assert main(["pipeline", str(song_wav), "-o", str(out), "--no-cache", "--skip", "other"]) == 0
        assert (out / "drums.mid").is_file()

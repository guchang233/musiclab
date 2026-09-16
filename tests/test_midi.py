"""MIDI 写出测试（mido 读回校验）。"""

from __future__ import annotations

import mido
import pytest

from musiclab.midi import write_midi
from musiclab.types import Note, TranscriptionResult


def _result(notes, instrument="piano", backend="test"):
    return TranscriptionResult(
        notes=tuple(sorted(notes, key=lambda n: n.start)),
        instrument=instrument,
        backend=backend,
        duration=max(n.end for n in notes) if notes else 0.0,
    )


def _read_note_events(path):
    """读回 MIDI，返回 [(abs_tick, type, pitch, velocity, channel)]。"""
    midi = mido.MidiFile(str(path))
    events = []
    for track in midi.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type in ("note_on", "note_off"):
                events.append((t, msg.type, msg.note, msg.velocity, msg.channel))
    return events


class TestWriteMidi:
    def test_单音符往返(self, tmp_path):
        notes = [Note(start=0.0, end=0.5, pitch=60, velocity=80)]
        path = write_midi(tmp_path / "out.mid", _result(notes), tempo_bpm=120.0)
        events = _read_note_events(path)
        assert len(events) == 2
        on = [e for e in events if e[1] == "note_on"][0]
        off = [e for e in events if e[1] == "note_off"][0]
        assert on[2] == 60 and on[3] == 80
        # tempo 120 → 960 tick/秒
        assert on[0] == 0
        assert off[0] == 480

    def test_时间戳换算(self, tmp_path):
        notes = [Note(start=0.25, end=0.75, pitch=64)]
        path = write_midi(tmp_path / "t.mid", _result(notes), tempo_bpm=120.0)
        events = _read_note_events(path)
        on = [e for e in events if e[1] == "note_on"][0]
        assert on[0] == 240  # 0.25s × 960

    def test_鼓走通道10(self, tmp_path):
        notes = [Note(start=0.0, end=0.1, pitch=36, velocity=90)]
        path = write_midi(tmp_path / "d.mid", _result(notes, instrument="drums"))
        events = _read_note_events(path)
        assert all(e[4] == 9 for e in events)

    def test_鼓不加program(self, tmp_path):
        notes = [Note(start=0.0, end=0.1, pitch=38)]
        path = write_midi(tmp_path / "d.mid", _result(notes, instrument="drums"))
        midi = mido.MidiFile(str(path))
        programs = [
            m for tr in midi.tracks for m in tr if m.type == "program_change"
        ]
        assert programs == []

    def test_GM音色映射(self, tmp_path):
        notes = [Note(start=0.0, end=0.2, pitch=40)]
        path = write_midi(tmp_path / "b.mid", _result(notes, instrument="bass"))
        midi = mido.MidiFile(str(path))
        programs = [m for tr in midi.tracks for m in tr if m.type == "program_change"]
        assert programs[0].program == 33  # GM Electric Bass (finger)

    def test_非法tempo(self, tmp_path):
        with pytest.raises(ValueError):
            write_midi(tmp_path / "x.mid", _result([Note(0, 0.1, 60)]), tempo_bpm=0)

    def test_力度边界合法(self, tmp_path):
        notes = [
            Note(start=0.0, end=0.1, pitch=60, velocity=1),
            Note(start=0.2, end=0.3, pitch=62, velocity=127),
        ]
        path = write_midi(tmp_path / "v.mid", _result(notes))
        velocities = [e[3] for e in _read_note_events(path) if e[1] == "note_on"]
        assert velocities == [1, 127]

    def test_同tick音符先off后on(self, tmp_path):
        """相邻音符（legato）在同一 tick 的 note_off 必须先于 note_on。"""
        notes = [
            Note(start=0.0, end=0.5, pitch=60),
            Note(start=0.5, end=1.0, pitch=62),
        ]
        path = write_midi(tmp_path / "l.mid", _result(notes), tempo_bpm=120.0)
        events = [e for e in _read_note_events(path) if e[0] == 480]
        types = [e[1] for e in events]
        # 同 tick 上 off(60) 必须排在 on(62) 前面
        assert types == ["note_off", "note_on"]
        assert events[0][2] == 60 and events[1][2] == 62

    def test_空结果也能写出(self, tmp_path):
        path = write_midi(tmp_path / "e.mid", _result([], instrument="piano"))
        assert path.is_file()

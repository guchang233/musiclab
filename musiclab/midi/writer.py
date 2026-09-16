"""MIDI 写出：转录结果 → 标准 MIDI 文件（mido 实现）。

约定：
- type 1 格式：轨道 0 为 tempo/拍号，轨道 1 为音符；
- 鼓（instrument="drums"）固定使用 channel 10（索引 9，GM 打击乐组）；
- 其余乐器按 GM 音色表映射 program，未收录的乐器回退到钢琴（program 0）。
"""

from __future__ import annotations

from pathlib import Path

import mido

from musiclab.types import TranscriptionResult

#: 每拍 tick 数（MIDI 标准 480 精度）
TICKS_PER_BEAT = 480

#: 乐器标签 → GM 音色 program（鼓为 None，走 channel 10）
GM_PROGRAMS: dict[str, int | None] = {
    "drums": None,
    "piano": 0,
    "guitar": 24,
    "bass": 33,
    "vocals": 53,
    "other": 0,
    "auto": 0,
}

#: 打击乐通道（0 起索引，GM 规定 channel 10 为鼓组）
DRUM_CHANNEL = 9


def _seconds_to_ticks(seconds: float, tempo_bpm: float) -> int:
    return max(0, round(seconds * tempo_bpm / 60.0 * TICKS_PER_BEAT))


def _program_for(instrument: str) -> int | None:
    key = instrument.lower()
    if key in GM_PROGRAMS:
        return GM_PROGRAMS[key]
    # 未知乐器：melodic 一律钢琴
    return 0 if key != "drums" else None


def write_midi(
    path: str | Path,
    result: TranscriptionResult,
    *,
    tempo_bpm: float = 120.0,
    instrument: str | None = None,
) -> Path:
    """把转录结果写出为 MIDI 文件。

    Args:
        path: 输出路径（.mid）。
        result: 转录结果。
        tempo_bpm: 写入的 tempo（音符时间戳不受影响，仅影响播放时长）。
        instrument: 覆盖音色标签；None 时使用 ``result.instrument``。

    Returns:
        写出的文件路径。
    """
    if tempo_bpm <= 0:
        raise ValueError(f"tempo_bpm 必须为正：{tempo_bpm}")

    tag = (instrument or result.instrument).lower()
    is_drums = tag == "drums"
    midi = mido.MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)

    # ---- 轨道 0：tempo / 拍号 ----
    meta_track = mido.MidiTrack()
    meta_track.append(mido.MetaMessage("track_name", name="musiclab", time=0))
    meta_track.append(
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0)
    )
    meta_track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    midi.tracks.append(meta_track)

    # ---- 轨道 1：音符 ----
    note_track = mido.MidiTrack()
    note_track.append(mido.MetaMessage("track_name", name=tag or "melody", time=0))
    channel = DRUM_CHANNEL if is_drums else 0
    program = _program_for(tag)
    if program is not None:
        note_track.append(
            mido.Message("program_change", program=program, channel=channel, time=0)
        )

    events: list[tuple[int, int, int, int]] = []  # (tick, is_on, note, velocity)
    for note in sorted(result.notes, key=lambda n: (n.start, n.pitch)):
        on_tick = _seconds_to_ticks(note.start, tempo_bpm)
        off_tick = _seconds_to_ticks(max(note.end, note.start), tempo_bpm)
        off_tick = max(off_tick, on_tick)  # 保证 tick 单调
        velocity = max(1, min(127, int(note.velocity)))
        events.append((on_tick, 1, note.pitch, velocity))
        events.append((max(off_tick, on_tick), 0, note.pitch, 0))

    # 同一 tick 上先 note_off 再 note_on，避免相邻音符 Legato 粘连
    events.sort(key=lambda e: (e[0], e[1]))

    last_tick = 0
    for tick, is_on, pitch, velocity in events:
        delta = tick - last_tick
        last_tick = tick
        if is_on:
            note_track.append(
                mido.Message("note_on", note=pitch, velocity=velocity, channel=channel, time=delta)
            )
        else:
            note_track.append(
                mido.Message("note_off", note=pitch, velocity=0, channel=channel, time=delta)
            )
    midi.tracks.append(note_track)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(path))
    return path

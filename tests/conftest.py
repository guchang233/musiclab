"""合成音频 fixture：全部测试基于确定性合成信号，不依赖任何真实音频。"""

from __future__ import annotations

import numpy as np
import pytest

from musiclab.types import AudioData

SR = 22050


def midi_to_hz(midi: int | float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def sine(freq: float, dur: float, *, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    t = np.arange(int(dur * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def melody(notes: list[tuple[int, float]], *, amp: float = 0.5, sr: int = SR) -> np.ndarray:
    """按 [(midi, dur), ...] 生成单音旋律。"""
    chunks = [sine(midi_to_hz(m), d, amp=amp, sr=sr) for m, d in notes]
    return np.concatenate(chunks)


def kick(dur: float = 0.15, *, sr: int = SR) -> np.ndarray:
    """低频衰减正弦（打击/低频占优）。"""
    t = np.arange(int(dur * sr)) / sr
    return (0.9 * np.sin(2 * np.pi * 60.0 * t) * np.exp(-t * 25.0)).astype(np.float32)


def snare(dur: float = 0.12, *, sr: int = SR) -> np.ndarray:
    """中频体 + 宽带噪声（宽带、无主导频带）。"""
    t = np.arange(int(dur * sr)) / sr
    rng = np.random.default_rng(7)
    body = 0.5 * np.sin(2 * np.pi * 190.0 * t) * np.exp(-t * 30.0)
    noise = 0.4 * rng.standard_normal(len(t)) * np.exp(-t * 35.0)
    return (body + noise).astype(np.float32)


def hihat(dur: float = 0.08, *, sr: int = SR) -> np.ndarray:
    """高通滤波噪声（高频占优）。"""
    from scipy.signal import butter, sosfilt

    t = np.arange(int(dur * sr)) / sr
    rng = np.random.default_rng(11)
    noise = rng.standard_normal(len(t))
    sos = butter(4, 6000.0, btype="highpass", fs=sr, output="sos")
    return (0.35 * sosfilt(sos, noise) * np.exp(-t * 70.0)).astype(np.float32)


def mix_at(offsets: list[tuple[float, np.ndarray]], total: float, *, sr: int = SR) -> np.ndarray:
    """把 [(起始秒, 信号), ...] 叠加到 total 秒的空轨上。"""
    out = np.zeros(int(total * sr), dtype=np.float32)
    for off, sig in offsets:
        i = int(off * sr)
        out[i:i + len(sig)] += sig[: len(out) - i]
    return out


def audio(samples: np.ndarray, *, sr: int = SR) -> AudioData:
    return AudioData(samples=samples, sample_rate=sr)


@pytest.fixture(scope="session")
def sr() -> int:
    return SR


@pytest.fixture()
def mono_sine_audio() -> AudioData:
    """标准音 A4（440Hz，1 秒）。"""
    return audio(sine(440.0, 1.0))


@pytest.fixture()
def melody_audio() -> AudioData:
    """C-E-G 三音旋律，各 0.4 秒。"""
    return audio(melody([(60, 0.4), (64, 0.4), (67, 0.4)]))


@pytest.fixture()
def drums_audio() -> AudioData:
    """1 小节鼓组（BPM 120，8 分网格 + 16 分位军鼓，互不重叠）。"""
    events = [
        (0.0, kick()), (0.5, kick()), (1.0, kick()), (1.5, kick()),
        (0.25, hihat()), (0.75, hihat()), (1.25, hihat()), (1.75, hihat()),
        (0.375, snare()), (1.375, snare()),
    ]
    return audio(mix_at(events, total=2.0))


@pytest.fixture(scope="session")
def song_audio() -> AudioData:
    """合成“整曲”：贝斯线 + 鼓组 + 中频和弦垫底，共 3 秒。"""
    # 贝斯线：E2 → G2 → A2（谐波、低频）
    bass_line = melody([(40, 1.0), (43, 1.0), (45, 1.0)], amp=0.6)
    events: list[tuple[float, np.ndarray]] = [(0.0, bass_line)]
    # 鼓组：BPM 120，鼓件时刻互不重叠（onset 检测按事件计）
    for i in range(6):
        events.append((i * 0.5, kick()))
        events.append((i * 0.5 + 0.25, hihat()))
    events += [(1.375, snare()), (2.375, snare())]
    # 和弦垫底：C 大三和弦（谐波、中频）
    chord = sum(
        sine(midi_to_hz(m), 3.0, amp=0.18) for m in (60, 64, 67)
    ) / 3.0
    events.append((0.0, chord))
    return audio(mix_at(events, total=3.0))

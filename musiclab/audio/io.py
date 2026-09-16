"""音频 I/O：加载、重采样、写出。

加载策略：优先 soundfile（本地库、快），失败后回退 librosa/ffmpeg
（支持 mp3/flac/ogg 等压缩格式），两条路径都失败才报错。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import soundfile as sf

from musiclab.engine.errors import AudioLoadError
from musiclab.types import AudioData, Stem

logger = logging.getLogger(__name__)

#: 加载/处理时统一使用的精度
_DTYPE = np.float32

#: soundfile 直接支持且常见的扩展名
_DIRECT_EXTS = {".wav", ".flac", ".aiff", ".aif", ".ogg"}


def load_audio(
    path: str | Path,
    *,
    sample_rate: int | None = None,
    mono: bool = False,
) -> AudioData:
    """加载音频文件。

    Args:
        path: 音频文件路径（wav/mp3/flac/ogg 等，取决于系统解码器）。
        sample_rate: 目标采样率；None 表示保留原始采样率。
        mono: 是否下混为单声道。

    Raises:
        AudioLoadError: 文件不存在、解码失败或内容为空。
    """
    path = Path(path)
    if not path.is_file():
        raise AudioLoadError(f"音频文件不存在：{path}")

    samples: np.ndarray | None = None
    sr: int | None = None

    # 路径一：soundfile 直读（wav/flac/ogg 等）。
    # 经 Python 文件句柄打开，规避 libsndfile 对非 ASCII 路径的潜在问题。
    try:
        with open(path, "rb") as f:
            data, sr = sf.read(f, dtype=_DTYPE, always_2d=False)
        samples = np.asarray(data)
    except Exception as exc:  # noqa: BLE001 —— 具体异常类型因格式而异，统一兜底
        logger.debug("soundfile 读取失败（%s），尝试 librosa 回退：%s", path, exc)

    # 路径二：librosa（内部经 ffmpeg/audioread 支持 mp3 等格式）
    if samples is None:
        try:
            import librosa  # 延迟导入：CLI 启动不加载 librosa

            data, lsr = librosa.load(str(path), sr=None, mono=False)
            samples = np.asarray(data, dtype=_DTYPE)
            sr = int(lsr)
        except Exception as exc:  # noqa: BLE001
            raise AudioLoadError(f"无法解码音频文件 {path}：{exc}") from exc

    if samples.size == 0:
        raise AudioLoadError(f"音频文件内容为空：{path}")

    # 声道规范化：统一为 (n,) 或 (n, ch)
    if samples.ndim == 2 and samples.shape[0] < samples.shape[1]:
        # soundfile 多声道返回 (n, ch)；librosa mono=False 返回 (ch, n)，此处统一
        if samples.shape[0] <= 8 and samples.shape[1] > 8:
            samples = samples.T
    if mono:
        samples = samples.mean(axis=1) if samples.ndim == 2 else samples

    audio = AudioData(samples=samples, sample_rate=int(sr))

    if sample_rate is not None and audio.sample_rate != sample_rate:
        audio = resample_audio(audio, sample_rate)
    return audio


def resample_audio(audio: AudioData, target_sr: int) -> AudioData:
    """重采样到目标采样率（高质量 soxr）。"""
    if target_sr <= 0:
        raise ValueError(f"目标采样率必须为正：{target_sr}")
    if audio.sample_rate == target_sr:
        return audio

    import librosa  # 延迟导入

    y = audio.samples.T  # librosa 期望 (..., n) / (ch, n)
    out = librosa.resample(
        y.astype(_DTYPE), orig_sr=audio.sample_rate, target_sr=target_sr
    )
    if out.ndim == 2:
        out = out.T  # 回到 (n, ch)
    return AudioData(samples=np.ascontiguousarray(out), sample_rate=target_sr)


def write_wav(path: str | Path, audio: AudioData, *, subtype: str = "PCM_16") -> Path:
    """把音频写出为 wav。

    Args:
        subtype: soundfile 子类型。管线中间产物请用 ``"FLOAT"``
            （float32 无损往返，保证内容寻址缓存的键稳定）；
            面向用户的混音导出用默认 ``"PCM_16"``。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = audio.samples
    if data.ndim == 1:
        data = data[:, np.newaxis]
    # 经文件句柄 + 显式 format 写出：不让 libsndfile 做任何路径/扩展名推断，
    # 行为完全确定（曾观察到按文件名推断在部分环境下偶发 Format not recognised）
    with open(path, "wb") as f:
        sf.write(f, data, audio.sample_rate, subtype=subtype, format="WAV")
    return path


def write_stems(out_dir: str | Path, stems: list[Stem]) -> list[Path]:
    """把分轨批量写出为 ``<name>.wav``（float32，无损往返），返回路径列表。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    seen: set[str] = set()
    for stem in stems:
        if stem.name in seen:
            raise ValueError(f"重复的分轨名：{stem.name}")
        seen.add(stem.name)
        paths.append(write_wav(out_dir / f"{stem.name}.wav", stem.audio, subtype="FLOAT"))
    return paths

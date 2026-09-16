"""管线编排：加载 → 分离（缓存）→ 逐轨转录（乐器路由）→ MIDI 写出。

乐器路由表决定每个分轨的去向：
- ``"skip"``：跳过该轨（默认仅人声跳过不成立时按单音处理，见下）；
- 乐器标签：交给注册表解析后端。

多音轨约束：``other``/``piano``/``guitar`` 等混合分轨必须由
多音轨能力的后端转录；单音后端（如 pyin）会被注册表拒绝，
此时管线记录告警并跳过，绝不静默输出垃圾音符。

转录结果以规范化的 notes.json 缓存；MIDI 渲染（tempo 等参数）
每次执行即时进行，因此调 tempo 不需要重跑任何模型。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from musiclab.audio import load_audio, write_stems
from musiclab.config import EngineConfig
from musiclab.engine.errors import (
    BackendNotAvailableError,
    MusicLabError,
    OperationCancelledError,
)
from musiclab.engine.registry import ModelRegistry
from musiclab.midi import write_midi
from musiclab.pipeline.cache import ArtifactCache
from musiclab.types import Note, STEM_PRESETS, Stem, TranscriptionResult

logger = logging.getLogger(__name__)

#: 天然多音轨的分轨 → 必须由多音轨后端转录
POLYPHONIC_STEMS = frozenset({"other", "piano", "guitar"})

#: 默认路由：分轨名 → "skip" 或乐器标签
DEFAULT_ROUTING: dict[str, str] = {
    "vocals": "vocals",
    "accompaniment": "auto",
    "drums": "drums",
    "bass": "bass",
    "other": "other",
    "piano": "piano",
    "guitar": "guitar",
}


# ---------------------------------------------------------------------------
# 报告结构
# ---------------------------------------------------------------------------


@dataclass
class StageReport:
    """一个处理阶段的执行记录。"""

    name: str
    backend: str
    seconds: float
    cached: bool = False
    outputs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "seconds": round(self.seconds, 3),
            "cached": self.cached,
            "outputs": self.outputs,
        }


@dataclass
class PipelineReport:
    """整条管线的执行报告。"""

    input: str
    output_dir: str
    schema: tuple[str, ...]
    stages: list[StageReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    midi_files: dict[str, str] = field(default_factory=dict)
    notes_count: dict[str, int] = field(default_factory=dict)
    total_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "output_dir": self.output_dir,
            "schema": list(self.schema),
            "stages": [s.to_dict() for s in self.stages],
            "warnings": self.warnings,
            "midi_files": self.midi_files,
            "notes_count": self.notes_count,
            "total_seconds": round(self.total_seconds, 3),
        }


# ---------------------------------------------------------------------------
# notes 规范化 JSON（缓存中间格式）
# ---------------------------------------------------------------------------


def notes_to_json(result: TranscriptionResult) -> str:
    data = {
        "instrument": result.instrument,
        "backend": result.backend,
        "duration": result.duration,
        "warnings": list(result.warnings),
        "notes": [
            [n.start, n.end, n.pitch, n.velocity, n.confidence] for n in result.notes
        ],
    }
    return json.dumps(data, ensure_ascii=False)


def notes_from_json(blob: str) -> TranscriptionResult:
    data = json.loads(blob)
    notes = tuple(
        Note(start=float(r[0]), end=float(r[1]), pitch=int(r[2]),
             velocity=int(r[3]), confidence=float(r[4]))
        for r in data.get("notes", [])
    )
    return TranscriptionResult(
        notes=notes,
        instrument=data.get("instrument", "auto"),
        backend=data.get("backend", "unknown"),
        duration=float(data.get("duration", 0.0)),
        warnings=tuple(data.get("warnings", ())),
    )


# ---------------------------------------------------------------------------
# 管线
# ---------------------------------------------------------------------------


class TranscriptionPipeline:
    """端到端管线：分离 + 逐轨转谱。"""

    def __init__(
        self,
        config: EngineConfig | None = None,
        registry: ModelRegistry | None = None,
    ) -> None:
        self._config = config or EngineConfig()
        self._registry = registry or self._default_registry()
        self._cache = ArtifactCache(self._config.cache_dir)

    @staticmethod
    def _default_registry() -> ModelRegistry:
        from musiclab.engine import build_default_registry

        return build_default_registry()

    # ------------------------------------------------------------------

    def run(
        self,
        input_path: str | Path,
        output_dir: str | Path,
        *,
        stems: str = "4",
        routing: Mapping[str, str] | None = None,
        tempo_bpm: float = 120.0,
        on_event: Callable[[Mapping[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> PipelineReport:
        """执行完整管线。

        Args:
            input_path: 输入音频。
            output_dir: 输出目录（分轨 wav 与 MIDI 都写到这里）。
            stems: 分轨预设（"2" / "4" / "6"）。
            routing: 路由覆盖（{分轨名: "skip" 或乐器标签}）。
            tempo_bpm: MIDI 渲染 tempo。
            on_event: 阶段事件回调（进度留痕），每个阶段开始/结束时调用。
            should_cancel: 取消探测回调，返回 True 时在阶段边界抛出
                :class:`OperationCancelledError`。

        Returns:
            执行报告。
        """

        def emit(phase: str, index: int, total: int, stage: str, **extra: Any) -> None:
            if on_event is not None:
                on_event(
                    {"kind": "stage", "phase": phase, "stage": stage,
                     "index": index, "total": total, **extra}
                )

        def check_cancel() -> None:
            if should_cancel is not None and should_cancel():
                raise OperationCancelledError("管线在阶段边界被取消")

        started = time.perf_counter()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        route = dict(DEFAULT_ROUTING)
        if routing:
            route.update(dict(routing))

        schema = self._resolve_schema(stems)
        report = PipelineReport(
            input=str(input_path), output_dir=str(output_dir), schema=schema
        )
        total = 2 + len(schema)  # load + separate + 逐轨转录

        # ---- 1. 加载 ----
        check_cancel()
        emit("start", 1, total, "load")
        t0 = time.perf_counter()
        audio = load_audio(input_path, sample_rate=self._config.sample_rate)
        report.stages.append(
            StageReport(
                name="load",
                backend="soundfile/librosa",
                seconds=time.perf_counter() - t0,
                outputs=[str(input_path)],
            )
        )
        emit("end", 1, total, "load", seconds=report.stages[-1].seconds)
        logger.info(
            "已加载 %s：%.1fs / %dch / %dHz",
            input_path, audio.duration, audio.channels, audio.sample_rate,
        )

        # ---- 2. 分离（带缓存） ----
        check_cancel()
        emit("start", 2, total, "separate")
        stems_list = self._separate_stage(audio, schema, output_dir, report)
        sep_stage = report.stages[-1]
        emit("end", 2, total, "separate", backend=sep_stage.backend,
             cached=sep_stage.cached)

        # ---- 3. 逐轨转录（乐器路由） ----
        for i, stem in enumerate(stems_list):
            check_cancel()
            index = 3 + i
            emit("start", index, total, f"transcribe:{stem.name}")
            self._transcribe_stage(stem, route, output_dir, tempo_bpm, report)
            tr_stage = next(
                (s for s in report.stages if s.name == f"transcribe:{stem.name}"), None
            )
            # 被路由跳过 / 无可用后端时没有 StageReport，只发告警性质的事件
            if tr_stage is None:
                emit("end", index, total, f"transcribe:{stem.name}", skipped=True)
            else:
                emit("end", index, total, f"transcribe:{stem.name}",
                     backend=tr_stage.backend, cached=tr_stage.cached,
                     notes=report.notes_count.get(stem.name, 0))

        report.total_seconds = time.perf_counter() - started
        logger.info(
            "管线完成：%.1fs，MIDI %d 个，告警 %d 条",
            report.total_seconds, len(report.midi_files), len(report.warnings),
        )
        return report

    # ------------------------------------------------------------------
    # 阶段实现
    # ------------------------------------------------------------------

    def _separate_stage(
        self,
        audio,
        schema: tuple[str, ...],
        output_dir: Path,
        report: PipelineReport,
    ) -> list[Stem]:
        t0 = time.perf_counter()
        separator = self._registry.separator(schema, name=self._config.separator_name)

        params: dict[str, Any] = {
            "schema": schema,
            "model": getattr(separator, "model_name", None),
        }
        key = self._cache.key(
            step="separate", backend=separator.name, params=params, audio=audio
        )
        cached_dir = self._cache.lookup(key)

        if cached_dir is not None:
            stems_list = [
                Stem(name=name, audio=load_audio(cached_dir / f"{name}.wav"))
                for name in schema
            ]
            write_stems(output_dir, stems_list)  # 仍写出，方便用户直接取用
            report.stages.append(
                StageReport(
                    name="separate",
                    backend=separator.name,
                    seconds=time.perf_counter() - t0,
                    cached=True,
                    outputs=[str(cached_dir)],
                )
            )
            return stems_list

        stems_list = separator.separate(audio, schema)
        paths = write_stems(output_dir, stems_list)
        if self._cache.enabled:
            self._cache.store(
                key,
                {p.name: p for p in paths},
                meta={"schema": list(schema), "backend": separator.name},
            )
        report.stages.append(
            StageReport(
                name="separate",
                backend=separator.name,
                seconds=time.perf_counter() - t0,
                outputs=[str(p) for p in paths],
            )
        )
        return stems_list

    def _transcribe_stage(
        self,
        stem: Stem,
        route: dict[str, str],
        output_dir: Path,
        tempo_bpm: float,
        report: PipelineReport,
    ) -> None:
        policy = route.get(stem.name, "skip")
        if policy == "skip":
            report.warnings.append(f"分轨 {stem.name!r}：按路由配置跳过转谱")
            return
        instrument = policy

        is_drums = instrument == "drums"
        polyphonic = stem.name in POLYPHONIC_STEMS
        t0 = time.perf_counter()
        try:
            if is_drums:
                transcriber = self._registry.transcriber(
                    instrument="drums", name=self._config.drum_transcriber_name
                )
            else:
                transcriber = self._registry.transcriber(
                    instrument=instrument,
                    polyphonic=polyphonic,
                    name=self._config.transcriber_name,
                )
        except BackendNotAvailableError as exc:
            report.warnings.append(f"分轨 {stem.name!r}：无可用转谱后端（{exc}）")
            return

        # 转录结果缓存（notes.json 为规范化中间产物）
        key = self._cache.key(
            step="transcribe",
            backend=transcriber.name,
            params={"instrument": transcriber.instrument, "stem": stem.name},
            audio=stem.audio,
        )
        cached_dir = self._cache.lookup(key)
        if cached_dir is not None:
            result = notes_from_json((cached_dir / "notes.json").read_text(encoding="utf-8"))
            report.stages.append(
                StageReport(
                    name=f"transcribe:{stem.name}",
                    backend=transcriber.name,
                    seconds=time.perf_counter() - t0,
                    cached=True,
                    outputs=[str(cached_dir)],
                )
            )
        else:
            result = transcriber.transcribe(stem.audio)
            notes_file = output_dir / f"{stem.name}.notes.json"
            notes_file.write_text(notes_to_json(result), encoding="utf-8")
            if self._cache.enabled:
                self._cache.store(
                    key, {"notes.json": notes_file}, meta={"stem": stem.name}
                )
            report.stages.append(
                StageReport(
                    name=f"transcribe:{stem.name}",
                    backend=transcriber.name,
                    seconds=time.perf_counter() - t0,
                    outputs=[str(notes_file)],
                )
            )

        midi_path = write_midi(
            output_dir / f"{stem.name}.mid", result, tempo_bpm=tempo_bpm
        )
        report.midi_files[stem.name] = str(midi_path)
        report.notes_count[stem.name] = len(result.notes)
        for w in result.warnings:
            report.warnings.append(f"分轨 {stem.name!r}（{transcriber.name}）：{w}")

    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_schema(stems: str) -> tuple[str, ...]:
        key = str(stems)
        if key not in STEM_PRESETS:
            valid = ", ".join(sorted(STEM_PRESETS))
            raise MusicLabError(f"无效的分轨预设 {stems!r}，可选值：{valid}")
        return STEM_PRESETS[key]

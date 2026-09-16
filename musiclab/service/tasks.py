"""任务状态机 + 事件总线 + worker 队列。

状态机::

    queued ──► running ──► succeeded
       │           │
       └─► cancelled └─► failed / cancelled

取消语义：
- ``queued`` 任务直接取消（未出队执行）；
- ``running`` 任务设置 ``cancel_requested``，引擎在阶段边界抛出
  ``OperationCancelledError``，任务落为 ``cancelled``。

事件：``{"ts": float, "kind": str, ...}``，kind 取值：
``state``（状态迁移）、``stage``（管线阶段进度）、``done``（终态摘要）。
每个任务事件留痕上限 :data:`MAX_EVENTS`，防止长任务无限膨胀内存。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from musiclab.config import EngineConfig
from musiclab.engine.errors import MusicLabError, OperationCancelledError

logger = logging.getLogger(__name__)

#: 单任务事件留痕上限（超出丢弃最旧）
MAX_EVENTS = 2000

#: 终态集合
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


class TaskValidationError(MusicLabError):
    """任务参数不合法（提交时拒绝）。"""


# ---------------------------------------------------------------------------
# 任务模型
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """一个可跟踪的服务任务。"""

    id: str
    type: str  # separate / transcribe / pipeline
    params: dict[str, Any]
    state: str = "queued"
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None
    cancel_requested: bool = False
    result: dict[str, Any] | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    # 线程同步原语（不参与序列化）
    _cond: threading.Condition = field(
        default_factory=threading.Condition, repr=False, compare=False
    )

    def to_dict(self, *, with_events: bool = False) -> dict[str, Any]:
        d = {
            "id": self.id,
            "type": self.type,
            "params": self.params,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
            "result": self.result,
            "error": self.error,
        }
        if with_events:
            d["events"] = list(self.events)
        return d


# ---------------------------------------------------------------------------
# 任务管理器
# ---------------------------------------------------------------------------


class TaskManager:
    """线程安全的任务队列与执行器。

    Args:
        workers: worker 线程数（引擎为重 CPU/ML 任务，默认 1 串行）。
        config: 引擎配置（缓存目录等）。
        clock: 可注入时钟，测试用。
    """

    def __init__(
        self,
        *,
        workers: int = 1,
        config: EngineConfig | None = None,
    ) -> None:
        if workers < 0:
            raise ValueError("workers 不能为负")
        self._config = config or EngineConfig()
        self._lock = threading.Lock()
        self._queue: queue.Queue[str] = queue.Queue()
        self._tasks: dict[str, Task] = {}
        self._subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = {}
        self._workers: list[threading.Thread] = []
        self._closed = False
        self._handlers: dict[str, Callable[[Task], dict[str, Any]]] = {
            "separate": self._run_separate,
            "transcribe": self._run_transcribe,
            "pipeline": self._run_pipeline,
        }
        for i in range(workers):
            t = threading.Thread(target=self._worker_loop, name=f"musiclab-worker-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    # ------------------------------------------------------------------
    # 公开操作
    # ------------------------------------------------------------------

    def submit(self, task_type: str, params: dict[str, Any]) -> Task:
        """提交任务。参数校验失败抛 :class:`TaskValidationError`。"""
        if task_type not in self._handlers:
            raise TaskValidationError(
                f"未知任务类型 {task_type!r}，可选：{', '.join(sorted(self._handlers))}"
            )
        self._validate_params(task_type, params)

        with self._lock:
            if self._closed:
                raise MusicLabError("服务已关闭，拒绝新任务")
            task = Task(id=uuid.uuid4().hex[:12], type=task_type, params=dict(params))
            self._tasks[task.id] = task
            self._subscribers[task.id] = []

        self._emit(task, {"kind": "state", "state": "queued"})
        self._queue.put(task.id)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[Task]:
        with self._lock:
            return list(self._tasks.values())

    def cancel(self, task_id: str) -> Task:
        """请求取消。queued 直接取消；running 协作式取消；终态报错。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.state in TERMINAL_STATES:
                raise MusicLabError(f"任务已处于终态 {task.state!r}，无法取消")
            task.cancel_requested = True
            if task.state == "queued":
                self._finish_locked(task, "cancelled")
                return task
        # running：等 worker 在阶段边界捕获取消
        with task._cond:
            task._cond.wait_for(lambda: task.state in TERMINAL_STATES, timeout=60.0)
        return task

    def close(self, timeout: float = 5.0) -> None:
        """停收新任务并等待 worker 退出。"""
        with self._lock:
            self._closed = True
        for _ in self._workers:
            self._queue.put("")  # 毒丸
        for t in self._workers:
            t.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 事件订阅（SSE 数据源）
    # ------------------------------------------------------------------

    def subscribe(self, task_id: str) -> tuple[queue.Queue[dict[str, Any]], int]:
        """订阅任务后续事件，返回 (队列, 当前留痕长度)。

        SSE 先回放 ``task.events[:长度]``，再从队列消费增量。
        """
        q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=MAX_EVENTS)
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            seen = len(task.events)
            self._subscribers[task_id].append(q)
            terminal = task.state in TERMINAL_STATES
        if terminal:
            q.put(None)  # 已终态：订阅即结束
        return q, seen

    def unsubscribe(self, task_id: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(task_id)
            if subs and q in subs:
                subs.remove(q)

    # ------------------------------------------------------------------ -
    # 内部：状态迁移与事件
    # ------------------------------------------------------------------

    def _emit(self, task: Task, event: dict[str, Any]) -> None:
        """发布事件：写入任务留痕（带上限）并分发给订阅者。

        必须持有 task._cond 或在任务归属线程内调用。
        """
        with task._cond:
            event = {"ts": time.monotonic(), **event}
            task.events.append(event)
            if len(task.events) > MAX_EVENTS:
                del task.events[: len(task.events) - MAX_EVENTS]
        for q in list(self._subscribers.get(task.id, [])):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # 订阅者消费太慢：丢弃（SSE 端会重连补齐）

    def _set_state(self, task: Task, state: str) -> None:
        with task._cond:
            task.state = state
            task._cond.notify_all()
        self._emit(task, {"kind": "state", "state": state})

    def _finish_locked(self, task: Task, state: str, *, result=None, error=None) -> None:
        # 仅在持有 self._lock 时调用（queued 取消路径）
        with task._cond:
            task.state = state
            task.finished_at = time.monotonic()
            task.result = result
            task.error = error
            task._cond.notify_all()
        self._emit(task, {"kind": "state", "state": state})
        self._send_poison(task)

    def _send_poison(self, task: Task) -> None:
        """向订阅者发送流结束信号。"""
        for q in list(self._subscribers.get(task.id, [])):
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    # worker
    # ------------------------------------------------------------------

    def _worker_loop(self) -> None:
        while True:
            task_id = self._queue.get()
            if not task_id:  # 毒丸：关闭
                return
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or task.state in TERMINAL_STATES:
                    continue  # 已在队列中被取消
                task.started_at = time.monotonic()
            self._set_state(task, "running")
            try:
                result = self._handlers[task.type](task)
            except OperationCancelledError as exc:
                with task._cond:
                    task.finished_at = time.monotonic()
                    task.error = str(exc)
                self._set_state(task, "cancelled")
            except Exception as exc:  # noqa: BLE001 —— 任务边界的最后防线
                logger.exception("任务 %s 失败", task.id)
                with task._cond:
                    task.finished_at = time.monotonic()
                    task.error = str(exc)
                self._set_state(task, "failed")
            else:
                with task._cond:
                    task.finished_at = time.monotonic()
                    task.result = result
                self._set_state(task, "succeeded")
                self._emit(task, {"kind": "done", "result": result})
            finally:
                self._send_poison(task)

    # ------------------------------------------------------------------
    # 任务处理器（引擎调用）
    # ------------------------------------------------------------------

    def _validate_params(self, task_type: str, params: dict[str, Any]) -> None:
        from pathlib import Path

        from musiclab.types import STEM_PRESETS

        common = {"input": str}
        spec = {
            "separate": {"output": str},
            "transcribe": {"output": str},
            "pipeline": {"output": str},
        }
        for key in common:
            if key not in params or not isinstance(params[key], str) or not params[key]:
                raise TaskValidationError(f"缺少必填参数 {key!r}（非空字符串）")
        for key in spec[task_type]:
            if key not in params or not isinstance(params[key], str) or not params[key]:
                raise TaskValidationError(f"缺少必填参数 {key!r}（非空字符串）")
        if not Path(params["input"]).is_file():
            raise TaskValidationError(f"输入文件不存在：{params['input']}")
        stems = params.get("stems", "4")
        if stems not in STEM_PRESETS:
            raise TaskValidationError(
                f"无效分轨预设 {stems!r}，可选：{', '.join(sorted(STEM_PRESETS))}"
            )

    def _run_pipeline(self, task: Task) -> dict[str, Any]:
        from musiclab.pipeline import TranscriptionPipeline

        params = task.params
        skip = list(params.get("skip", []))
        pipeline = TranscriptionPipeline(config=self._config)
        report = pipeline.run(
            params["input"],
            params["output"],
            stems=str(params.get("stems", "4")),
            routing={name: "skip" for name in skip},
            tempo_bpm=float(params.get("tempo_bpm", 120.0)),
            on_event=lambda ev: self._emit(task, dict(ev)),
            should_cancel=lambda: task.cancel_requested,
        )
        return report.to_dict()

    def _run_separate(self, task: Task) -> dict[str, Any]:
        from musiclab.audio import load_audio, write_stems
        from musiclab.engine import get_default_registry
        from musiclab.types import STEM_PRESETS

        params = task.params
        if task.cancel_requested:
            raise OperationCancelledError("任务在启动前被取消")
        stems = str(params.get("stems", "4"))
        if stems not in STEM_PRESETS:
            raise TaskValidationError(f"无效分轨预设 {stems!r}")
        schema = STEM_PRESETS[stems]

        registry = get_default_registry()
        separator = registry.separator(schema, name=params.get("backend"))
        self._emit(task, {"kind": "stage", "phase": "start", "stage": "separate",
                          "index": 1, "total": 1, "backend": separator.name})
        audio = load_audio(params["input"], sample_rate=self._config.sample_rate)
        stem_list = separator.separate(audio, schema)
        paths = write_stems(params["output"], stem_list)
        self._emit(task, {"kind": "stage", "phase": "end", "stage": "separate",
                          "index": 1, "total": 1})
        return {"stems": [str(p) for p in paths], "backend": separator.name}

    def _run_transcribe(self, task: Task) -> dict[str, Any]:
        from musiclab.audio import load_audio
        from musiclab.engine import get_default_registry
        from musiclab.midi import write_midi

        params = task.params
        if task.cancel_requested:
            raise OperationCancelledError("任务在启动前被取消")
        instrument = str(params.get("instrument", "auto"))
        polyphonic = bool(params.get("polyphonic", False))

        registry = get_default_registry()
        transcriber = registry.transcriber(
            instrument, polyphonic=polyphonic, name=params.get("backend")
        )
        self._emit(task, {"kind": "stage", "phase": "start", "stage": "transcribe",
                          "index": 1, "total": 1, "backend": transcriber.name})
        audio = load_audio(params["input"], sample_rate=self._config.sample_rate)
        result = transcriber.transcribe(audio)
        path = write_midi(
            params["output"], result, tempo_bpm=float(params.get("tempo_bpm", 120.0))
        )
        self._emit(task, {"kind": "stage", "phase": "end", "stage": "transcribe",
                          "index": 1, "total": 1, "notes": len(result.notes)})
        return {"midi": str(path), "backend": transcriber.name, "notes": len(result.notes)}

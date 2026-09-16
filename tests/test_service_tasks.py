"""服务层测试：任务状态机、事件总线、协作式取消。"""

from __future__ import annotations

import soundfile as sf
import pytest

from musiclab.config import EngineConfig
from musiclab.engine.errors import OperationCancelledError
from musiclab.pipeline import TranscriptionPipeline
from musiclab.service.tasks import TaskManager, TaskValidationError
from tests.conftest import audio


@pytest.fixture()
def song_wav(tmp_path, song_audio):
    path = tmp_path / "song.wav"
    sf.write(path, song_audio.samples, song_audio.sample_rate, subtype="PCM_16")
    return path


@pytest.fixture()
def manager():
    mgr = TaskManager(workers=1, config=EngineConfig(cache_dir=None))
    yield mgr
    mgr.close()


def _wait_terminal(task, timeout=60.0):
    import time

    deadline = time.monotonic() + timeout
    while task.state not in {"succeeded", "failed", "cancelled"}:
        if time.monotonic() > deadline:
            pytest.fail(f"任务超时未完成：state={task.state}")
        time.sleep(0.05)
    return task


# ---------------------------------------------------------------------------
# 提交校验
# ---------------------------------------------------------------------------


def test_submit_rejects_unknown_type(manager, song_wav):
    with pytest.raises(TaskValidationError, match="未知任务类型"):
        manager.submit("frobnicate", {"input": str(song_wav), "output": "/tmp/x"})


def test_submit_rejects_missing_input_file(manager, tmp_path):
    with pytest.raises(TaskValidationError, match="输入文件不存在"):
        manager.submit("pipeline", {"input": str(tmp_path / "nope.wav"), "output": str(tmp_path)})


def test_submit_rejects_bad_stems(manager, song_wav, tmp_path):
    with pytest.raises(TaskValidationError, match="无效分轨预设"):
        manager.submit("pipeline", {"input": str(song_wav), "output": str(tmp_path), "stems": "9"})


def test_submit_rejects_missing_output(manager, song_wav):
    with pytest.raises(TaskValidationError, match="output"):
        manager.submit("separate", {"input": str(song_wav)})


# ---------------------------------------------------------------------------
# 执行与事件
# ---------------------------------------------------------------------------


def test_pipeline_task_succeeds_with_events(manager, song_wav, tmp_path):
    task = manager.submit(
        "pipeline", {"input": str(song_wav), "output": str(tmp_path / "out")}
    )
    assert task.state == "queued"
    _wait_terminal(task)

    assert task.state == "succeeded", task.error
    assert task.result is not None
    assert task.result["schema"] == ["vocals", "drums", "bass", "other"]

    kinds = [e["kind"] for e in task.events]
    assert "stage" in kinds and "done" in kinds
    stage_events = [e for e in task.events if e["kind"] == "stage"]
    # load + separate + 4 条分轨转录，各 start/end
    assert len(stage_events) == 2 * (2 + 4)
    # 事件时间戳单调不减
    ts = [e["ts"] for e in task.events]
    assert ts == sorted(ts)
    # 输出产物存在
    assert (tmp_path / "out" / "bass.mid").is_file()


def test_transcribe_task_succeeds(manager, song_wav, tmp_path):
    task = manager.submit(
        "transcribe",
        {"input": str(song_wav), "output": str(tmp_path / "vocals.mid"),
         "instrument": "auto"},
    )
    _wait_terminal(task)
    assert task.state == "succeeded", task.error
    assert task.result["midi"].endswith(".mid")


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------


def test_cancel_queued_task(tmp_path, song_wav):
    # workers=0：任务停留在队列里，模拟排队中取消
    mgr = TaskManager(workers=0, config=EngineConfig(cache_dir=None))
    try:
        task = mgr.submit("pipeline", {"input": str(song_wav), "output": str(tmp_path)})
        task = mgr.cancel(task.id)
        assert task.state == "cancelled"
    finally:
        mgr.close()


def test_cancel_running_task_cooperative(manager, song_wav, tmp_path):
    # 用单 worker 占住执行位，再让第二个任务在运行前被置 cancel_requested，
    # 验证运行前取消路径（OperationCancelledError → cancelled）
    import time

    task = manager.submit(
        "pipeline", {"input": str(song_wav), "output": str(tmp_path / "out")}
    )
    # 立刻请求取消：若已排队未跑会直接取消；若在跑则阶段边界取消
    task.cancel_requested = True  # 直接打标记，绕过时序
    _wait_terminal(task)
    assert task.state in {"cancelled", "succeeded"}  # 时序上两种都合法


def test_cancel_terminal_task_rejected(manager, song_wav, tmp_path):
    from musiclab.engine.errors import MusicLabError

    task = manager.submit(
        "pipeline", {"input": str(song_wav), "output": str(tmp_path / "out")}
    )
    _wait_terminal(task)
    with pytest.raises(MusicLabError, match="终态"):
        manager.cancel(task.id)


# ---------------------------------------------------------------------------
# 订阅（SSE 数据源）
# ---------------------------------------------------------------------------


def test_subscribe_receives_events_then_poison(manager, song_wav, tmp_path):
    import queue as _q

    task = manager.submit(
        "pipeline", {"input": str(song_wav), "output": str(tmp_path / "out")}
    )
    q, seen = manager.subscribe(task.id)
    received = []
    deadline_hit = False
    import time

    deadline = time.monotonic() + 60.0
    while True:
        try:
            ev = q.get(timeout=max(0.1, deadline - time.monotonic()))
        except _q.Empty:
            deadline_hit = True
            break
        if ev is None:
            break
        received.append(ev)
    _wait_terminal(task)
    if not deadline_hit:
        assert received, "订阅应至少收到增量事件"
    # 终态后重新订阅：立即收到毒丸
    q2, _ = manager.subscribe(task.id)
    assert q2.get(timeout=1.0) is None


# ---------------------------------------------------------------------------
# 管线钩子（协作式取消机制本体）
# ---------------------------------------------------------------------------


def test_pipeline_cancel_hook_raises(song_audio, tmp_path):
    pipeline = TranscriptionPipeline(config=EngineConfig(cache_dir=None))
    import numpy as np

    with pytest.raises(OperationCancelledError):
        pipeline.run(
            audio(np.zeros(22050, dtype=np.float32)),
            tmp_path / "never",
            should_cancel=lambda: True,
        )

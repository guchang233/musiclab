"""HTTP API 测试：端点、状态码、任务生命周期、事件回放与 SSE。"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest
import soundfile as sf

from musiclab.config import EngineConfig
from musiclab.service import serve
from musiclab.service.tasks import TaskManager


@pytest.fixture(scope="module")
def server():
    manager = TaskManager(workers=1, config=EngineConfig(cache_dir=None))
    srv, _thread = serve(manager=manager, host="127.0.0.1", port=0)
    yield srv
    srv.shutdown()
    srv.server_close()
    manager.close()


@pytest.fixture(scope="module")
def base(server) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def song_wav(tmp_path_factory):
    """合成整曲写盘（模块级复用）。"""
    import numpy as np

    from tests.conftest import hihat, kick, melody, mix_at, snare

    path = tmp_path_factory.mktemp("srv") / "song.wav"
    bass = melody([(40, 1.0), (43, 1.0), (45, 1.0)], amp=0.6)
    events: list[tuple[float, np.ndarray]] = [(0.0, bass)]
    for i in range(4):
        events.append((i * 0.5, kick()))
        events.append((i * 0.5 + 0.25, hihat()))
    events += [(0.875, snare())]
    sf.write(path, mix_at(events, total=2.0), 22050, subtype="PCM_16")
    return path


def _request(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=70) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _wait_task(base: str, task_id: str, timeout: float = 70.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status, payload = _request("GET", f"{base}/api/tasks/{task_id}")
        assert status == 200
        if payload["state"] in {"succeeded", "failed", "cancelled"}:
            return payload
        if time.monotonic() > deadline:
            pytest.fail(f"任务超时：{payload['state']}")
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# 基础端点
# ---------------------------------------------------------------------------


def test_health(base):
    status, payload = _request("GET", f"{base}/api/health")
    assert status == 200
    assert payload["status"] == "ok"


def test_backends(base):
    status, payload = _request("GET", f"{base}/api/backends")
    assert status == 200
    names = {b["name"] for b in payload["backends"]}
    assert {"spectral-hpss", "pyin"} <= names  # DSP 降级后端必在


def test_unknown_path_404(base):
    status, payload = _request("GET", f"{base}/api/nope")
    assert status == 404
    assert payload["error"]["code"] == "not_found"


def test_method_not_allowed_shape(base):
    # POST 到只读端点 → 404（路由不匹配），仍是结构化 JSON
    status, payload = _request("POST", f"{base}/api/health", {"x": 1})
    assert status == 404
    assert "error" in payload


# ---------------------------------------------------------------------------
# 提交校验
# ---------------------------------------------------------------------------


def test_submit_bad_json(base):
    req = urllib.request.Request(f"{base}/api/tasks", data=b"not json", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, json.loads(exc.read())
    assert status == 400
    assert payload["error"]["code"] == "bad_request"


def test_submit_unknown_type(base, song_wav, tmp_path):
    status, payload = _request(
        "POST", f"{base}/api/tasks",
        {"type": "frobnicate", "params": {"input": str(song_wav), "output": str(tmp_path)}},
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_params"


def test_submit_missing_input_file(base, tmp_path):
    status, payload = _request(
        "POST", f"{base}/api/tasks",
        {"type": "pipeline", "params": {"input": str(tmp_path / "nope.wav"), "output": str(tmp_path)}},
    )
    assert status == 400


def test_submit_nonexistent_task_404(base):
    status, payload = _request("GET", f"{base}/api/tasks/deadbeef")
    assert status == 404
    assert payload["error"]["code"] == "task_not_found"


# ---------------------------------------------------------------------------
# 任务生命周期
# ---------------------------------------------------------------------------


def test_pipeline_task_lifecycle(base, song_wav, tmp_path):
    status, task = _request(
        "POST", f"{base}/api/tasks",
        {"type": "pipeline", "params": {"input": str(song_wav), "output": str(tmp_path / "out")}},
    )
    assert status == 201
    assert task["state"] == "queued"

    final = _wait_task(base, task["id"])
    assert final["state"] == "succeeded", final["error"]
    assert final["result"]["schema"] == ["vocals", "drums", "bass", "other"]
    assert final["started_at"] is not None and final["finished_at"] is not None

    # 事件留痕可回放
    status, payload = _request("GET", f"{base}/api/tasks/{task['id']}?events=1")
    assert status == 200
    kinds = [e["kind"] for e in payload["events"]]
    assert "stage" in kinds and "done" in kinds

    # 事件端点（普通 GET → JSON 数组）
    status, payload = _request("GET", f"{base}/api/tasks/{task['id']}/events")
    assert status == 200
    assert payload["events"]

    # 列表包含该任务
    status, payload = _request("GET", f"{base}/api/tasks")
    assert any(t["id"] == task["id"] for t in payload["tasks"])


def test_cancel_terminal_task_409(base, song_wav, tmp_path):
    status, task = _request(
        "POST", f"{base}/api/tasks",
        {"type": "pipeline", "params": {"input": str(song_wav), "output": str(tmp_path / "out")}},
    )
    assert status == 201
    _wait_task(base, task["id"])
    status, payload = _request("POST", f"{base}/api/tasks/{task['id']}/cancel")
    assert status == 409
    assert payload["error"]["code"] == "state_conflict"


def test_cancel_unknown_task_404(base):
    status, _ = _request("POST", f"{base}/api/tasks/deadbeef/cancel")
    assert status == 404


def test_transcribe_task_via_api(base, song_wav, tmp_path):
    status, task = _request(
        "POST", f"{base}/api/tasks",
        {"type": "transcribe",
         "params": {"input": str(song_wav), "output": str(tmp_path / "m.mid")}},
    )
    assert status == 201
    final = _wait_task(base, task["id"])
    assert final["state"] == "succeeded", final["error"]
    assert final["result"]["midi"].endswith(".mid")


# ---------------------------------------------------------------------------
# SSE 流
# ---------------------------------------------------------------------------


def test_sse_stream(base, song_wav, tmp_path):
    import http.client
    import socket

    status, task = _request(
        "POST", f"{base}/api/tasks",
        {"type": "pipeline", "params": {"input": str(song_wav), "output": str(tmp_path / "out")}},
    )
    assert status == 201

    host, port = base.split("//")[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=70)
    conn.request(
        "GET", f"/api/tasks/{task['id']}/events",
        headers={"Accept": "text/event-stream"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/event-stream")

    # 读到流结束（任务终态后服务端主动断开）
    buf = b""
    while True:
        try:
            chunk = resp.read(4096)
        except (socket.timeout, http.client.RemoteDisconnected):
            break
        if not chunk:
            break
        buf += chunk
        # 足够验证即提前断开，避免读满
        if (b'"kind": "done"' in buf) or (b'"state"' in buf and len(buf) > 200):
            break
    conn.close()

    lines = [
        line for line in buf.decode("utf-8", "replace").splitlines()
        if line.startswith("data: ")
    ]
    assert lines, "SSE 应至少输出一条 data 事件"
    events = [json.loads(l[len("data: "):]) for l in lines]
    assert all("kind" in e for e in events)

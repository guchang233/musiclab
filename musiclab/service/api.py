"""核心服务的 HTTP 接口（纯标准库，零新增依赖）。

端点一览::

    GET  /api/health                 存活探测
    GET  /api/backends               后端可用性诊断
    POST /api/tasks                  提交任务 {type, params} → 201 {task}
    GET  /api/tasks                  任务列表
    GET  /api/tasks/{id}             任务详情（?events=1 附带事件留痕）
    GET  /api/tasks/{id}/events      SSE 事件流（Accept: text/event-stream）
                                     或普通 GET 返回已留痕事件 JSON
    POST /api/tasks/{id}/cancel      请求取消

错误统一 JSON：``{"error": {code, message}}``；
状态码：400 参数错误、404 不存在、405 方法不支持、409 状态冲突。
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from musiclab.engine.errors import MusicLabError
from musiclab.service.tasks import TaskManager, TaskValidationError


class _ApiHandler(BaseHTTPRequestHandler):
    """HTTP 处理器：路由分发到 TaskManager。"""

    server: "ApiServer"

    # 静默默认访问日志（服务进程由自己的事件留痕）
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # ------------------------------------------------------------------
    # 响应工具
    # ------------------------------------------------------------------

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 —— http.server 约定
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        query = self.path.split("?", 1)[1] if "?" in self.path else ""

        if path == "/api/health":
            self._json(200, {"status": "ok", "version": self.server.version})
        elif path == "/api/backends":
            self._json(200, {"backends": self.server.manager_backends()})
        elif path == "/api/tasks":
            self._json(200, {"tasks": [t.to_dict() for t in self.server.manager.list_tasks()]})
        elif path.startswith("/api/tasks/"):
            parts = path.split("/")
            task_id = parts[3]
            task = self.server.manager.get(task_id)
            if task is None:
                self._error(404, "task_not_found", f"任务不存在：{task_id}")
            elif len(parts) >= 5 and parts[4] == "events":
                self._events(task_id, want_sse="text/event-stream" in self.headers.get("Accept", ""))
            else:
                with_events = "events=1" in query
                self._json(200, task.to_dict(with_events=with_events))
        else:
            self._error(404, "not_found", f"未知路径：{path}")

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/api/tasks":
            self._submit()
        elif path.startswith("/api/tasks/") and path.endswith("/cancel"):
            task_id = path.split("/")[3]
            try:
                task = self.server.manager.cancel(task_id)
            except KeyError:
                self._error(404, "task_not_found", f"任务不存在：{task_id}")
            except MusicLabError as exc:
                self._error(409, "state_conflict", str(exc))
            else:
                self._json(200, task.to_dict())
        else:
            self._error(404, "not_found", f"未知路径：{path}")

    # ------------------------------------------------------------------
    # 端点实现
    # ------------------------------------------------------------------

    def _submit(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._error(400, "bad_request", "请求体必须是合法 JSON")
            return
        if not isinstance(body, dict):
            self._error(400, "bad_request", "请求体必须是 JSON 对象")
            return
        task_type = body.get("type")
        params = body.get("params")
        if not isinstance(task_type, str) or not isinstance(params, dict):
            self._error(400, "bad_request", "需要 {type: str, params: dict}")
            return
        try:
            task = self.server.manager.submit(task_type, params)
        except TaskValidationError as exc:
            self._error(400, "invalid_params", str(exc))
        except MusicLabError as exc:
            self._error(400, "invalid_params", str(exc))
        else:
            self._json(201, task.to_dict())

    def _events(self, task_id: str, want_sse: bool) -> None:
        manager = self.server.manager
        try:
            q, seen = manager.subscribe(task_id)
        except KeyError:
            self._error(404, "task_not_found", f"任务不存在：{task_id}")
            return
        try:
            task = manager.get(task_id)
            assert task is not None
            if not want_sse:
                # 普通请求：返回当前已留痕事件（测试/调试友好）
                self._json(200, {"events": list(task.events)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            # 先回放已留痕，再消费增量
            for ev in list(task.events)[:seen]:
                self._sse_write(ev)
            while True:
                ev = q.get()
                if ev is None:
                    break
                self._sse_write(ev)
        finally:
            manager.unsubscribe(task_id, q)

    def _sse_write(self, event: dict[str, Any]) -> None:
        data = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


class ApiServer(ThreadingHTTPServer):
    """承载 TaskManager 的 HTTP 服务。"""

    daemon_threads = True

    def __init__(self, manager: TaskManager, *, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.manager = manager
        self.version = "0.1.0"
        super().__init__((host, port), _ApiHandler)

    def manager_backends(self) -> list[dict[str, Any]]:
        from musiclab.engine import get_default_registry

        return list(get_default_registry().diagnostics())


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    workers: int = 1,
    manager: TaskManager | None = None,
    background: bool = True,
) -> tuple[ApiServer, threading.Thread | None]:
    """启动服务。

    Args:
        background: True 时在后台线程 serve_forever 并立即返回；
            False 时不启动线程，由调用方（如 CLI 主线程）自行
            ``server.serve_forever()``。

    Returns:
        (server, thread)：background=False 时 thread 为 None。
    """
    mgr = manager or TaskManager(workers=workers)
    server = ApiServer(mgr, host=host, port=port)
    if not background:
        return server, None
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="musiclab-api")
    thread.start()
    return server, thread

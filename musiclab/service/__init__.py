"""核心服务进程：任务队列 + 事件总线（UI 壳消费的后端）。

本层是「UI / AI 推理分离」架构中的核心服务进程：
- 任务以队列调度，在 worker 线程中执行引擎调用；
- 执行过程产生结构化事件（阶段进度、状态迁移），供 SSE 推送；
- 支持协作式取消：运行中的任务在阶段边界检查取消标记。

所有状态迁移与事件都带单调时间戳，事件按任务留痕（有上限）。
"""

from musiclab.service.api import ApiServer, serve
from musiclab.service.tasks import Task, TaskManager, TaskValidationError

__all__ = ["ApiServer", "Task", "TaskManager", "TaskValidationError", "serve"]

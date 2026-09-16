# UI 壳（Tauri + 核心服务进程）

三层进程模型中的 UI 层骨架：

```
Tauri UI 进程（本目录）
   │  HTTP JSON + SSE
   ▼
核心服务进程（musiclab serve，sidecar 方式拉起）
   │
   ▼
engine（分离 / 转录后端，worker 线程内执行）
```

- **前端**：`web/`（`index.html` + `app.js`），纯原生 JS 无打包器，直接消费
  `POST /api/tasks`、`GET /api/tasks/{id}`、SSE `GET /api/tasks/{id}/events`。
  也可以脱离 Tauri 单独用浏览器打开（只要 `musiclab serve` 在跑）。
- **Rust 壳**：`src-tauri/`，启动时拉起 sidecar（`musiclab serve`），
  退出时回收子进程。

## 开发运行

```bash
# 1. 核心服务（单独调试 UI 时）
musiclab serve --port 8765

# 2. 浏览器直接打开 ui/web/index.html 即可（零构建）

# 3. Tauri 壳（需要 Rust + 系统 webkit2gtk 依赖）
cd ui && cargo tauri build   # 或 cd ui/src-tauri && cargo run
```

## 打包说明

- Tauri 壳通过环境变量 `MUSICLAB_SERVE_CMD` 指定 sidecar 启动命令
  （默认 `musiclab serve --port 8765`）；打包时把
  PyInstaller 打出的 `musiclab-server` 可执行文件放进
  `externalBin`，并设置该变量。
- 前端不打包任何逻辑：全部业务能力都在核心服务进程，
  UI 壳只做呈现与调度（可整体替换）。

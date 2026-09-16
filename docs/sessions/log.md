# 会话日志索引

最新在最上（倒序）。每条对应 `docs/sessions/<YYYYMMDD-HHMM>.md`。

## 2026-09-16 19:1X - Release 补 Windows exe，全链路跑通（v0.1.1）

- 详见 [20260916-1910.md](./20260916-1910.md)
- 关键成果：build-windows job（PyInstaller exe 103.7MB）；修复 Windows 编码崩溃、Tauri frontendDist、缺图标三连；Release v0.1.1 全 job success（exe/deb/whl/tar.gz）。
- 注意：tag 与包内版本号需手动同步（pyproject / tauri.conf）。

## 2026-09-16 18:5X - 修复 CI 失败：tests 包导入问题

- 详见 [20260916-1855.md](./20260916-1855.md)
- 关键成果：`tests/__init__.py` + 统一 `tests.conftest` 导入；CI 已绿（`f213d79`）。
- 下一步：打 `v0.1.0` tag 验证 Release 链路（含 Tauri deb）。

## 2026-09-16 18:3X - CI：自动测试 / 编译 / 发布 Release

- 详见 [20260916-1830.md](./20260916-1830.md)
- 关键成果：
  1. `ci.yml`：push/PR → 3.10-3.13 矩阵测试 + 构建检查。
  2. `release.yml`：tag `v*` → 测试 → 编译（Python 包 + Tauri deb）→ 自动发 Release。
- 下一步：推 `v0.1.0` tag 真实验证全链路；PyInstaller sidecar；插件系统。

## 2026-09-16 17:5X - 阶段②：核心服务进程 + Tauri 壳骨架

- 详见 [20260916-1750.md](./20260916-1750.md)
- 关键成果：
  1. `musiclab/service/`：任务队列 + 事件总线 + HTTP/SSE API（纯标准库）。
  2. pipeline 增加进度事件与协作式取消钩子；`musiclab serve` CLI。
  3. `ui/` Tauri 壳 + sidecar 骨架；测试 122 passed。
- 下一步：Tauri 首次真实构建；PyInstaller sidecar 打包；插件系统。

## 2026-09-16 16:5X - GitHub 推送 + 建立会话留痕机制

- 详见 [20260916-1600.md](./20260916-1600.md)
- 关键成果：
  1. 创建 GitHub 仓库 `guchang233/musiclab` 并推送到 `main`（commit `053116d`）。
  2. 新增 `AGENTS.md` 项目规则文件，建立「会话留痕」强制机制。
  3. 建立 `docs/sessions/` 目录、索引、模板。
- 下一步：接入 Tauri UI 壳 + sidecar；或继续扩展插件后端。
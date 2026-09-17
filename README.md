# MusicLab App

原生桌面应用：**音频分轨 + WAV→MIDI 转录**。纯 Rust 实现，无 Python、无 sidecar、无网络依赖。

```
crates/core     引擎（WAV IO / STFT / HPSS 分离 / YIN 音高 / onset 鼓点 / MIDI 写出 / 任务管理）
crates/server   无头 HTTP 服务（tiny_http，内嵌 Web UI，浏览器可用）
src-tauri       Tauri 桌面壳（直接内嵌引擎，无本地端口）
ui/             前端（原生 HTML/JS/CSS，桌面与浏览器共用同一份）
```

## 功能

- **分轨**：HPSS（中值滤波软掩码）；预设 `3` = bass / harmonic / drums，`2` = harmonic / percussive
- **转录**：YIN 基频追踪 → 音符分组（旋律/贝斯）；频谱通量 onset → GM 鼓件（kick/snare/hihat）
- **输出**：每轨一个 WAV 分轨 + 一个 SMF 格式 0 MIDI（鼓走 channel 9）
- **任务系统**：后台线程执行、进度事件、协作式取消、增量事件拉取

## 开发

```bash
# 测试（纯算法，无系统依赖）
cargo test

# 无头服务模式（浏览器打开 http://localhost:8790）
cargo run -p musiclab-server

# 桌面应用
cargo run -p musiclab-app
```

### 系统依赖（Linux 桌面构建）

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev
```

## API（server 模式）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 |
| GET | `/api/schemas` | 分轨预设 |
| POST | `/api/tasks` | 提交 `{input, out_dir?, stems?, tempo_bpm?}` |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务快照（含 result） |
| GET | `/api/tasks/{id}/events?since=N` | 增量事件 |
| POST | `/api/tasks/{id}/cancel` | 协作式取消 |

## 构建

```bash
cargo tauri build          # 桌面安装包（NSIS / deb / appimage）
cargo build --release -p musiclab-server   # 无头单二进制
```

CI：push 自动测试；打 `v*` tag 自动构建并发布 Release。

## 许可

MIT

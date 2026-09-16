//! MusicLab 桌面壳（骨架）。
//!
//! 职责（保持薄壳，不做业务）：
//! 1. 启动时拉起核心服务进程 sidecar（`musiclab serve`）；
//! 2. 打开窗口加载静态前端（前端经 HTTP/SSE 访问核心服务）；
//! 3. 退出时回收 sidecar 子进程。
//!
//! sidecar 命令可用环境变量 `MUSICLAB_SERVE_CMD` 整体覆盖
//! （打包时指向 PyInstaller 产物），参数按空格切分。

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

struct Sidecar(Mutex<Child>);

fn spawn_sidecar() -> Child {
    let cmdline = std::env::var("MUSICLAB_SERVE_CMD")
        .unwrap_or_else(|_| "musiclab serve --port 8765".to_string());
    let mut parts = cmdline.split_whitespace();
    let program = parts.next().expect("MUSICLAB_SERVE_CMD 不能为空");
    Command::new(program)
        .args(parts)
        .spawn()
        .expect("无法启动核心服务进程（musiclab serve）")
}

fn main() {
    let child = spawn_sidecar();

    tauri::Builder::default()
        .setup(move |app| {
            // 退出时回收 sidecar
            app.manage(Sidecar(Mutex::new(child)));
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<Sidecar>() {
                    let mut child = state.0.lock().unwrap();
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Tauri 运行失败");
}

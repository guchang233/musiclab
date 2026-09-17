//! MusicLab 桌面壳：直接内嵌原生引擎（无 sidecar、无本地端口）。
//!
//! 前端（../ui）通过 Tauri command 与 [`TaskManager`] 交互，
//! 与 musiclab-server 暴露同一套语义。

use musiclab_core::engine::schemas;
use musiclab_core::tasks::{TaskInfo, TaskManager, TaskParams};

#[tauri::command]
fn get_schemas() -> serde_json::Value {
    serde_json::json!({
        "schemas": schemas(),
        "detail": {
            "2": ["harmonic", "percussive"],
            "3": ["bass", "harmonic", "drums"],
        }
    })
}

#[tauri::command]
fn submit_task(mgr: tauri::State<TaskManager>, params: TaskParams) -> Result<String, String> {
    if !params.input.exists() {
        return Err(format!("输入文件不存在：{}", params.input.display()));
    }
    if musiclab_core::engine::schema_for(&params.stems).is_none() {
        return Err(format!("无效分轨预设 {:?}", params.stems));
    }
    Ok(mgr.submit(params))
}

#[tauri::command]
fn list_tasks(mgr: tauri::State<TaskManager>) -> Vec<TaskInfo> {
    mgr.list()
}

#[tauri::command]
fn get_task(mgr: tauri::State<TaskManager>, id: String) -> Option<TaskInfo> {
    mgr.get(&id)
}

#[tauri::command]
fn events_since(
    mgr: tauri::State<TaskManager>,
    id: String,
    since: usize,
) -> Option<serde_json::Value> {
    mgr.events_since(&id, since)
        .map(|(events, latest)| serde_json::json!({ "events": events, "latest": latest }))
}

#[tauri::command]
fn cancel_task(mgr: tauri::State<TaskManager>, id: String) -> bool {
    mgr.cancel(&id)
}

#[tauri::command]
async fn pick_audio_file(app: tauri::AppHandle) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .add_filter("WAV 音频", &["wav"])
            .blocking_pick_file()
            .and_then(|f| f.into_path().ok())
            .map(|p| p.display().to_string())
    })
    .await
    .ok()
    .flatten()
}

#[tauri::command]
fn reveal_path(app: tauri::AppHandle, path: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener()
        .reveal_item_in_dir(&path)
        .map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(TaskManager::new())
        .invoke_handler(tauri::generate_handler![
            get_schemas,
            submit_task,
            list_tasks,
            get_task,
            events_since,
            cancel_task,
            pick_audio_file,
            reveal_path,
        ])
        .run(tauri::generate_context!())
        .expect("Tauri 启动失败");
}

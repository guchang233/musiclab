//! MusicLab HTTP 服务（tiny_http，零异步运行时）。
//!
//! 路由：
//! - `GET  /`                        内嵌前端页面
//! - `GET  /api/health`              健康检查
//! - `GET  /api/schemas`             分轨预设列表
//! - `POST /api/tasks`               提交任务 `{input, out_dir?, stems?, tempo_bpm?}`
//! - `GET  /api/tasks`               任务列表
//! - `GET  /api/tasks/{id}`          任务快照（含 result）
//! - `GET  /api/tasks/{id}/events?since=N`  增量事件（前端轮询）
//! - `POST /api/tasks/{id}/cancel`   协作式取消

use std::io::Read as _;
use std::sync::Arc;

use musiclab_core::engine::schemas;
use musiclab_core::tasks::{TaskManager, TaskParams};
use tiny_http::{Header, Method, Response, Server};

const INDEX_HTML: &str = include_str!("../../../ui/index.html");
const APP_JS: &str = include_str!("../../../ui/app.js");
const STYLE_CSS: &str = include_str!("../../../ui/style.css");

fn json_header() -> Header {
    Header::from_bytes(
        &b"Content-Type"[..],
        &b"application/json; charset=utf-8"[..],
    )
    .unwrap()
}

fn html_header() -> Header {
    Header::from_bytes(&b"Content-Type"[..], &b"text/html; charset=utf-8"[..]).unwrap()
}

fn js_header() -> Header {
    Header::from_bytes(&b"Content-Type"[..], &b"text/javascript; charset=utf-8"[..]).unwrap()
}

fn css_header() -> Header {
    Header::from_bytes(&b"Content-Type"[..], &b"text/css; charset=utf-8"[..]).unwrap()
}

fn main() {
    let port: u16 = std::env::args()
        .nth(1)
        .or_else(|| std::env::var("PORT").ok())
        .and_then(|p| p.parse().ok())
        .unwrap_or(8790);
    let addr = format!("0.0.0.0:{port}");
    let server = Server::http(&addr).expect("监听端口失败");
    let mgr = Arc::new(TaskManager::new());
    println!("MusicLab server listening on http://{addr}");

    let mut handles = Vec::new();
    for request in server.incoming_requests() {
        let mgr = mgr.clone();
        handles.push(std::thread::spawn(move || handle(request, &mgr)));
        // 回收已结束的线程
        handles.retain(|h| !h.is_finished());
    }
}

fn handle(mut request: tiny_http::Request, mgr: &TaskManager) {
    let method = request.method().clone();
    let url = request.url().to_string();
    let path = url.split('?').next().unwrap_or("").to_string();
    let query = url
        .split_once('?')
        .map(|(_, q)| q.to_string())
        .unwrap_or_default();

    let respond = |req: tiny_http::Request, status: u16, body: String, ct: Header| {
        let resp = Response::from_string(body)
            .with_status_code(status)
            .with_header(ct)
            .with_header(
                Header::from_bytes(&b"Access-Control-Allow-Origin"[..], &b"*"[..]).unwrap(),
            );
        let _ = req.respond(resp);
    };

    match (&method, path.as_str()) {
        (Method::Get, "/") => respond(request, 200, INDEX_HTML.into(), html_header()),
        (Method::Get, "/app.js") => respond(request, 200, APP_JS.into(), js_header()),
        (Method::Get, "/style.css") => respond(request, 200, STYLE_CSS.into(), css_header()),
        (Method::Get, "/api/health") => {
            respond(request, 200, r#"{"ok":true}"#.into(), json_header())
        }
        (Method::Get, "/api/schemas") => {
            let body = serde_json::json!({
                "schemas": schemas(),
                "detail": {
                    "2": ["harmonic", "percussive"],
                    "3": ["bass", "harmonic", "drums"],
                }
            });
            respond(request, 200, body.to_string(), json_header())
        }
        (Method::Post, "/api/tasks") => {
            let mut body = String::new();
            if request.as_reader().read_to_string(&mut body).is_err() {
                respond(
                    request,
                    400,
                    r#"{"error":"无法读取请求体"}"#.into(),
                    json_header(),
                );
                return;
            }
            let params: TaskParams = match serde_json::from_str(&body) {
                Ok(p) => p,
                Err(e) => {
                    respond(
                        request,
                        400,
                        serde_json::json!({"error": format!("参数解析失败：{e}")}).to_string(),
                        json_header(),
                    );
                    return;
                }
            };
            // 输入文件本地可达性校验（提前失败，友好报错）
            if !params.input.exists() {
                respond(
                    request,
                    404,
                    serde_json::json!({"error": format!("输入文件不存在：{}", params.input.display())})
                        .to_string(),
                    json_header(),
                );
                return;
            }
            if musiclab_core::engine::schema_for(&params.stems).is_none() {
                respond(
                    request,
                    400,
                    serde_json::json!({"error": format!("无效分轨预设 {:?}，可选 {:?}", params.stems, schemas())})
                        .to_string(),
                    json_header(),
                );
                return;
            }
            let id = mgr.submit(params);
            respond(
                request,
                201,
                serde_json::json!({"id": id}).to_string(),
                json_header(),
            )
        }
        (Method::Get, "/api/tasks") => respond(
            request,
            200,
            serde_json::json!({"tasks": mgr.list()}).to_string(),
            json_header(),
        ),
        _ => {
            if let Some(rest) = path.strip_prefix("/api/tasks/") {
                let (id, action) = match rest.split_once('/') {
                    Some((id, action)) => (id, Some(action)),
                    None => (rest, None),
                };
                match (&method, action) {
                    (Method::Get, None) => match mgr.get(id) {
                        Some(info) => respond(
                            request,
                            200,
                            serde_json::to_string(&info).unwrap(),
                            json_header(),
                        ),
                        None => respond(
                            request,
                            404,
                            r#"{"error":"任务不存在"}"#.into(),
                            json_header(),
                        ),
                    },
                    (Method::Get, Some("events")) => {
                        let since: usize = query
                            .split('&')
                            .find_map(|kv| kv.strip_prefix("since="))
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(0);
                        match mgr.events_since(id, since) {
                            Some((events, latest)) => respond(
                                request,
                                200,
                                serde_json::json!({"events": events, "latest": latest}).to_string(),
                                json_header(),
                            ),
                            None => respond(
                                request,
                                404,
                                r#"{"error":"任务不存在"}"#.into(),
                                json_header(),
                            ),
                        }
                    }
                    (Method::Post, Some("cancel")) => {
                        let ok = mgr.cancel(id);
                        respond(
                            request,
                            if ok { 202 } else { 409 },
                            serde_json::json!({"cancelled": ok}).to_string(),
                            json_header(),
                        )
                    }
                    _ => respond(
                        request,
                        404,
                        r#"{"error":"未知路由"}"#.into(),
                        json_header(),
                    ),
                }
            } else {
                respond(
                    request,
                    404,
                    r#"{"error":"未知路由"}"#.into(),
                    json_header(),
                )
            }
        }
    }
}

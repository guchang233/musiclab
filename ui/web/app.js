/* MusicLab UI 逻辑：任务提交 / 列表 / SSE 进度。
 * 核心服务地址可用 ?api=... 覆盖（默认同源或 127.0.0.1:8765）。 */

"use strict";

const API =
  new URLSearchParams(location.search).get("api") || "http://127.0.0.1:8765";

const els = {
  svcState: document.getElementById("svc-state"),
  svcVersion: document.getElementById("svc-version"),
  form: document.getElementById("submit-form"),
  type: document.getElementById("f-type"),
  input: document.getElementById("f-input"),
  output: document.getElementById("f-output"),
  stems: document.getElementById("f-stems"),
  tempo: document.getElementById("f-tempo"),
  polyphonic: document.getElementById("f-polyphonic"),
  submitBtn: document.getElementById("submit-btn"),
  submitError: document.getElementById("submit-error"),
  list: document.getElementById("task-list"),
  emptyHint: document.getElementById("empty-hint"),
  tpl: document.getElementById("task-item-tpl"),
};

/** 任务 id → DOM 节点与 SSE 连接 */
const tasks = new Map();

async function api(method, path, body) {
  const resp = await fetch(API + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data?.error?.message || `HTTP ${resp.status}`);
  }
  return data;
}

// ---------------------------------------------------------------------
// 服务健康
// ---------------------------------------------------------------------

async function checkHealth() {
  try {
    const h = await api("GET", "/api/health");
    els.svcState.textContent = "在线";
    els.svcState.className = "pill pill-succeeded";
    els.svcVersion.textContent = "v" + h.version;
  } catch {
    els.svcState.textContent = "离线";
    els.svcState.className = "pill pill-failed";
    els.svcVersion.textContent = "";
  }
}

// ---------------------------------------------------------------------
// 任务渲染
// ---------------------------------------------------------------------

const CANCELABLE = new Set(["queued", "running"]);
const PROGRESS_EVERY = { pipeline: true };

function ensureItem(task) {
  if (tasks.has(task.id)) return tasks.get(task.id);
  const node = els.tpl.content.cloneNode(true).querySelector(".task");
  node.querySelector(".cancel-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      await api("POST", `/api/tasks/${task.id}/cancel`);
    } catch (err) {
      console.warn("取消失败", err);
    }
  });
  els.list.appendChild(node);
  els.emptyHint.hidden = true;
  const entry = { node, evtSource: null, lastStage: null };
  tasks.set(task.id, entry);
  return entry;
}

function renderTask(task) {
  const { node } = ensureItem(task);
  node.querySelector(".task-type").textContent = task.type;
  node.querySelector(".task-id").textContent = task.id.slice(0, 8);
  const pill = node.querySelector(".pill");
  const label = { queued: "排队中", running: "运行中", succeeded: "完成",
                  failed: "失败", cancelled: "已取消" }[task.state] || task.state;
  pill.textContent = label;
  pill.className = "pill pill-" + task.state;
  node.querySelector(".cancel-btn").hidden = !CANCELABLE.has(task.state);

  // 进度：从事件里推最新阶段（fetch 模式）
  const fill = node.querySelector(".fill");
  const ptext = node.querySelector(".progress-text");
  if (PROGRESS_EVERY[task.type]) {
    api("GET", `/api/tasks/${task.id}/events`)
      .then(({ events }) => {
        const stages = events.filter((e) => e.kind === "stage" && e.phase === "end");
        if (!stages.length) return;
        const last = stages[stages.length - 1];
        fill.style.width = `${Math.round((last.index / last.total) * 100)}%`;
        ptext.textContent = `${last.stage}（${last.index}/${last.total}）`;
      })
      .catch(() => {});
  }

  const detail = node.querySelector(".task-detail");
  if (task.state === "succeeded" && task.result) {
    const r = task.result;
    if (task.type === "pipeline") {
      const lines = Object.entries(r.midi_files || {}).map(
        ([stem, path]) => `  ${stem}: ${r.notes_count?.[stem] ?? 0} 音符 → ${path}`
      );
      detail.textContent = `总耗时 ${r.total_seconds?.toFixed?.(1) ?? r.total_seconds}s\n${lines.join("\n")}`;
    } else {
      detail.textContent = JSON.stringify(r);
    }
  } else if (task.state === "failed") {
    detail.textContent = "错误：" + (task.error || "未知");
  }
}

// ---------------------------------------------------------------------
// SSE 订阅（运行中任务）
// ---------------------------------------------------------------------

function subscribe(task) {
  const entry = ensureItem(task);
  if (entry.evtSource || task.state !== "running") return;
  const es = new EventSource(`${API}/api/tasks/${task.id}/events`);
  es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.kind === "state" && ev.state !== "running") {
      es.close();
      entry.evtSource = null;
      refresh();
      return;
    }
    if (ev.kind === "stage") {
      const { node } = entry;
      const fill = node.querySelector(".fill");
      const ptext = node.querySelector(".progress-text");
      fill.style.width = `${Math.round((ev.index / ev.total) * 100)}%`;
      ptext.textContent = `${ev.stage}${ev.phase === "end" ? " ✓" : "…"}（${ev.index}/${ev.total}）`;
      if (ev.phase === "end" && ev.stage === "separate" && ev.backend) {
        ptext.textContent += ` [${ev.backend}]`;
      }
    }
  };
  es.onerror = () => { es.close(); entry.evtSource = null; };
  entry.evtSource = es;
}

// ---------------------------------------------------------------------
// 刷新循环
// ---------------------------------------------------------------------

async function refresh() {
  try {
    const { tasks: list } = await api("GET", "/api/tasks");
    for (const t of list) {
      renderTask(t);
      if (t.state === "running") subscribe(t);
    }
  } catch {
    checkHealth();
  }
}

// ---------------------------------------------------------------------
// 提交
// ---------------------------------------------------------------------

els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  els.submitError.hidden = true;
  const params = {
    input: els.input.value.trim(),
    output: els.output.value.trim(),
    stems: els.stems.value,
    tempo_bpm: Number(els.tempo.value) || 120,
  };
  if (els.type.value === "transcribe") params.polyphonic = els.polyphonic.checked;

  els.submitBtn.disabled = true;
  try {
    const task = await api("POST", "/api/tasks", { type: els.type.value, params });
    renderTask(task);
    refresh();
  } catch (err) {
    els.submitError.textContent = err.message;
    els.submitError.hidden = false;
  } finally {
    els.submitBtn.disabled = false;
  }
});

// 类型切换：管线/分离显示 stems，转谱显示 polyphonic
function syncTypeFields() {
  const isTr = els.type.value === "transcribe";
  document.getElementById("f-stems").closest("label").style.display = isTr ? "none" : "";
  document.getElementById("f-polyphonic").closest("label.check").style.display = isTr ? "" : "none";
}
els.type.addEventListener("change", syncTypeFields);
syncTypeFields();

checkHealth();
refresh();
setInterval(refresh, 2000);
setInterval(checkHealth, 5000);

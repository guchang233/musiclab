// MusicLab 前端：同一份代码同时跑在 Tauri 壳（invoke）与浏览器（HTTP API）。
/* global window, document */
(function () {
  "use strict";

  const hasTauri = typeof window.__TAURI__ !== "undefined";
  const invoke = hasTauri ? window.__TAURI__.core.invoke : null;

  // ---------- API 适配层 ----------
  const api = {
    async schemas() {
      if (invoke) {
        const d = await invoke("get_schemas");
        return { schemas: d.schemas, detail: d.detail };
      }
      return fetch("/api/schemas").then((r) => r.json());
    },
    async submit(params) {
      if (invoke) return invoke("submit_task", { params });
      const r = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(params),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "提交失败");
      return j.id;
    },
    async list() {
      if (invoke) return invoke("list_tasks");
      const r = await fetch("/api/tasks");
      const j = await r.json();
      return j.tasks;
    },
    async get(id) {
      if (invoke) return invoke("get_task", { id });
      const r = await fetch("/api/tasks/" + id);
      return r.json();
    },
    async events(id, since) {
      if (invoke) return invoke("events_since", { id, since });
      const r = await fetch(`/api/tasks/${id}/events?since=${since}`);
      return r.json();
    },
    async cancel(id) {
      if (invoke) return invoke("cancel_task", { id });
      const r = await fetch(`/api/tasks/${id}/cancel`, { method: "POST" });
      return r.json();
    },
    async pickFile() {
      if (invoke) {
        return invoke("pick_audio_file");
      }
      return null; // 浏览器模式手动输入路径
    },
    async openPath(p) {
      if (invoke) {
        await invoke("reveal_path", { path: p });
      } else {
        window.open("file://" + p, "_blank");
      }
    },
  };

  // ---------- 状态 ----------
  const tasks = new Map(); // id -> {info, latestSeq, logText}
  let pollTimer = null;

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const els = {
    modeTag: $("mode-tag"),
    input: $("input-path"),
    browse: $("btn-browse"),
    stems: $("stems"),
    tempo: $("tempo"),
    outdir: $("outdir"),
    submit: $("btn-submit"),
    hint: $("submit-hint"),
    list: $("task-list"),
    connDot: $("conn-status"),
    connText: $("conn-text"),
  };

  function setConn(ok, text) {
    els.connDot.className = ok ? "running" : "offline";
    els.connDot.textContent = "●";
    els.connText.textContent = text;
  }

  function hint(msg, isError) {
    els.hint.textContent = msg;
    els.hint.className = "hint" + (isError ? " error" : "");
  }

  // ---------- 任务渲染 ----------
  function statusBadge(status) {
    const label = { running: "运行中", done: "完成", error: "失败", cancelled: "已取消" }[status] || status;
    return `<span class="badge ${status}">${label}</span>`;
  }

  function baseName(p) {
    return String(p).split(/[\\/]/).pop();
  }

  function renderTask(id) {
    const st = tasks.get(id);
    if (!st) return;
    const info = st.info;
    const evCount = st.latestSeq + 1;

    let progress = 0;
    let stageText = "";
    if (info.status === "running" && evCount > 0) {
      const evs = st.events;
      const total = evs.length ? (evs[evs.length - 1].total || 5) : 5;
      let done = 0;
      for (const e of evs) if (e.kind === "stage" && e.phase === "end") done++;
      progress = Math.round((done / total) * 100);
      stageText = `进度 ${done}/${total}`;
    } else if (info.status === "done") {
      progress = 100;
      const r = info.result;
      stageText = r
        ? `完成：${r.notes_count ? Object.entries(r.notes_count).map(([k, v]) => `${k}×${v}`).join(" / ") : ""}（${r.total_seconds.toFixed(1)}s）`
        : "完成";
    } else if (info.status === "error") {
      progress = 100;
      stageText = "";
    }

    let files = "";
    if (info.status === "done" && info.result) {
      const chips = [
        ...info.result.stem_files.map((f) => [f, baseName(f)]),
        ...Object.values(info.result.midi_files).map((f) => [f, baseName(f)]),
      ];
      files = `<div class="result-files">${chips
        .map(([f, n]) => `<span class="file-chip" data-path="${f.replace(/"/g, "&quot;")}" title="${f}">${n}</span>`)
        .join("")}</div>`;
    }

    let errBox = "";
    if (info.status === "error" && info.error) {
      errBox = `<div class="error-msg">${info.error}</div>`;
    }

    let cancelBtn = "";
    if (info.status === "running") {
      cancelBtn = `<button class="btn small danger" data-cancel="${id}">取消</button>`;
    }

    let dirChip = "";
    if (info.status === "done" && info.result) {
      dirChip = `<button class="btn small" data-open="${info.result.output_dir}">打开输出目录</button>`;
    }

    const el = document.getElementById("task-" + id);
    const html = `
      <div class="task-head">
        <span class="task-title" title="${info.params.input}">${baseName(info.params.input)}</span>
        ${statusBadge(info.status)}
        <span class="badge">${info.params.stems} 轨</span>
        ${cancelBtn}
        ${dirChip}
      </div>
      <div class="progress-track"><div class="progress-bar" style="width:${progress}%"></div></div>
      <div class="stage-line">${stageText}</div>
      ${files}
      ${errBox}
      <div class="evlog" id="evlog-${id}">${st.logText || "…"}</div>
    `;
    if (el) {
      el.innerHTML = html;
    } else {
      const div = document.createElement("div");
      div.className = "task";
      div.id = "task-" + id;
      div.innerHTML = html;
      if (els.list.querySelector(".empty")) els.list.innerHTML = "";
      els.list.prepend(div);
    }
  }

  function appendLog(st, ev) {
    if (ev.kind === "stage") {
      const arrow = ev.phase === "start" ? "▶" : "✓";
      st.logText += `${arrow} [${ev.index}/${ev.total}] ${ev.stage}` +
        (ev.phase === "end" && ev.extra && ev.extra.notes !== undefined ? ` (${ev.extra.notes} 音符)` : "") +
        "\n";
    } else if (ev.kind === "done") {
      st.logText += `● 完成（${(ev.total_seconds || 0).toFixed(1)}s）\n`;
    } else if (ev.kind === "error") {
      st.logText += `✗ 失败：${ev.message}\n`;
    } else if (ev.kind === "cancelled") {
      st.logText += "■ 已取消\n";
    }
  }

  // ---------- 轮询 ----------
  async function pollTask(id) {
    const st = tasks.get(id);
    if (!st) return;
    try {
      const [info, evRes] = await Promise.all([api.get(id), api.events(id, st.latestSeq)]);
      st.info = info;
      if (evRes.events && evRes.events.length) {
        for (const ev of evRes.events) {
          if ((ev.seq || 0) > st.latestSeq) {
            st.events.push(ev);
            st.latestSeq = ev.seq;
            appendLog(st, ev);
          }
        }
      }
      renderTask(id);
      if (info.status !== "running") {
        const logEl = document.getElementById("evlog-" + id);
        if (logEl) logEl.scrollTop = logEl.scrollHeight;
      }
    } catch (e) {
      console.warn("poll failed", e);
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      let anyRunning = false;
      // 拉取列表发现新任务（仅浏览器多标签场景）
      for (const id of tasks.keys()) {
        if (tasks.get(id).info.status === "running") anyRunning = true;
      }
      if (anyRunning) {
        for (const id of tasks.keys()) {
          if (tasks.get(id).info.status === "running") pollTask(id);
        }
      }
    }, 400);
  }

  // ---------- 事件绑定 ----------
  els.submit.addEventListener("click", async () => {
    const input = els.input.value.trim();
    if (!input) {
      hint("请先选择或输入 WAV 文件路径", true);
      return;
    }
    const params = {
      input,
      stems: els.stems.value,
      tempo_bpm: parseFloat(els.tempo.value) || 120,
    };
    if (els.outdir.value.trim()) params.out_dir = els.outdir.value.trim();
    els.submit.disabled = true;
    hint("提交中…");
    try {
      const id = await api.submit(params);
      tasks.set(id, { info: { id, status: "running", params }, latestSeq: -1, events: [], logText: "" });
      renderTask(id);
      hint("任务已提交：" + id.slice(0, 8));
      pollTask(id);
      startPolling();
    } catch (e) {
      hint(e.message || String(e), true);
    } finally {
      els.submit.disabled = false;
    }
  });

  els.browse.addEventListener("click", async () => {
    const p = await api.pickFile();
    if (p) els.input.value = p;
    else if (!hasTauri) hint("浏览器模式：请手动输入服务器上的文件路径", true);
  });

  els.list.addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.cancel) {
      await api.cancel(t.dataset.cancel);
    } else if (t.dataset.open) {
      await api.openPath(t.dataset.open);
    } else if (t.dataset.path) {
      await api.openPath(t.dataset.path);
    }
  });

  // ---------- 启动 ----------
  async function init() {
    if (hasTauri) {
      els.modeTag.textContent = "Tauri 桌面版 · 原生引擎";
      setConn(true, "本地引擎已连接");
      try {
        const existing = await api.list();
        for (const info of existing.slice(0, 10)) {
          tasks.set(info.id, { info, latestSeq: info.events_count - 1, events: [], logText: "（历史任务）" });
          renderTask(info.id);
        }
      } catch (e) {
        console.warn(e);
      }
    } else {
      try {
        const r = await fetch("/api/health");
        const j = await r.json();
        setConn(j.ok, "已连接到 musiclab-server");
        try {
          const j2 = await (await fetch("/api/tasks")).json();
          for (const info of j2.tasks.slice(0, 10)) {
            tasks.set(info.id, { info, latestSeq: info.events_count - 1, events: [], logText: "（历史任务）" });
            renderTask(info.id);
          }
        } catch (e) { console.warn(e); }
      } catch (e) {
        setConn(false, "无法连接 musiclab-server");
      }
    }
  }
  init();
})();

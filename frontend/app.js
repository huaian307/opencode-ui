/* OpenCode UI —— 前端逻辑（零依赖，无构建）
 *
 * 数据流（全部经 server.py 代理，代理会自动附加 Basic 鉴权）：
 *   会话列表   GET  /api/session?limit=50&order=desc
 *   消息列表   GET  /api/session/{id}/message?limit=80     ← 返回「新→旧」，必须重排
 *   发消息     POST /api/session/{id}/prompt  { text, files? }
 *   中断       POST /api/session/{id}/interrupt
 *   新建       POST /api/session  {}
 *   实时事件   GET  /api/event  (SSE)
 *   QQ音乐     /qq/state /qq/control /qq/volume /qq/lyrics /qq/search /qq/app /qq/spectrum
 *   选择/权限  /api/session/{id}/form  /permission   （详见 AGENTS.md）
 *
 * ⚠ 消息结构有两种形态，混用会让「用户提问」整条消失：
 *   助手：{ type:"assistant", content:[{type:"text"|"reasoning"|"tool", ...}] }
 *   用户：{ type:"user", text:"...", files:[], metadata:{...} }  ← 顶层 text，没有 content
 *
 * ⚠ /api/session/{id}/message 只返回「已完成」的消息；生成中的内容只能靠 SSE 增量事件。
 */

/** 本文件被加载时带着的 ?v= —— 在脚本同步执行期用 document.currentScript 取到。
 *  用途：和服务端 /healthz 报告的版本比对，不一致就自动刷新，
 *  免得面板窗口一直跑旧代码，让人以为"改了没生效"。 */
const UI_VER = (() => {
  try {
    const s = document.currentScript;
    return (s && s.src ? new URL(s.src).searchParams.get("v") : "") || "";
  } catch { return ""; }
})();

const $ = (id) => document.getElementById(id);

/* ---------------- 基础工具 ---------------- */

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!res.ok) {
    const detail = body && typeof body === "object"
      ? (body.error || body.message || JSON.stringify(body))
      : String(body || res.statusText);
    throw new Error(`${res.status} ${detail}`);
  }
  return body;
}

const unwrap = (b) => (b && typeof b === "object" && "data" in b ? b.data : b);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** 极简 Markdown：```代码块```、`行内代码`、**加粗**；其余按纯文本安全转义 */
function renderText(src) {
  return String(src ?? "").split(/```/).map((chunk, i) => {
    if (i % 2 === 1) {
      const nl = chunk.indexOf("\n");
      return `<pre>${esc((nl >= 0 ? chunk.slice(nl + 1) : chunk).replace(/\n+$/, ""))}</pre>`;
    }
    return esc(chunk)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  }).join("");
}

const fmtTime = (ms) => (ms ? new Date(ms).toLocaleString("zh-CN", { hour12: false }) : "");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------------- 全局状态 ---------------- */

const state = {
  sessions: [],
  current: null,
  messages: [],         // 服务端「已完成」的消息
  live: {},             // 生成中的消息缓冲（由 SSE 增量驱动）
  reveal: {},           // 打字机已显示字数 { msgID: { text, reasoning } }
  pending: [],          // 本地乐观回显「我发的消息」
  attachments: [],      // 待发送附件
  named: {},            // 已经自动命名过的会话 { id: true } —— 每个会话最多命名一次
  follow: true,         // 是否跟随最新内容自动往下滚
  refreshing: false,
  pendingRefresh: false,
};

/* ---------------- 侧栏：会话列表 ---------------- */

async function loadSessions() {
  try {
    const body = await api("/api/session?limit=50&order=desc");
    state.sessions = unwrap(body) || [];
    renderSessions();
  } catch (err) {
    $("session-list").innerHTML = `<div class="error-box">加载会话失败：${esc(err.message)}</div>`;
  }
}

function renderSessions() {
  const q = $("search").value.trim().toLowerCase();
  const items = state.sessions.filter((s) => !q || (s.title || "").toLowerCase().includes(q));
  if (!items.length) {
    $("session-list").innerHTML = `<div class="muted" style="padding:10px">没有匹配的会话</div>`;
    return;
  }
  $("session-list").innerHTML = items.map((s) => {
    const active = state.current && s.id === state.current.id ? " active" : "";
    const dir = (s.location && s.location.directory) || "";
    const when = s.time ? fmtTime(s.time.updated || s.time.created) : "";
    return `<div class="session${active}" data-id="${esc(s.id)}" title="${esc(dir)}">
      <button type="button" class="del" data-del="${esc(s.id)}"
              title="删除这个会话" aria-label="删除会话">×</button>
      <span class="t">${esc(s.title || "(无标题)")}</span>
      <span class="s">${esc(when)}${s.outcome ? " · " + esc(s.outcome) : ""}</span>
    </div>`;
  }).join("");
}

/** 删除会话：`DELETE /api/session/{id}` → 204（接口一直支持，以前前端没有入口）。
 *  删掉的如果是当前会话，就把界面清回"未选择"再自动选剩下最新的一条。 */
async function deleteSession(id, title) {
  const label = (title || "").trim() || "(无标题)";
  let n = 0;
  try { n = (state.messages || []).length; } catch { /* ignore */ }
  const extra = (state.current && state.current.id === id && n)
    ? `\n（含当前这 ${n} 条消息，无法撤销）` : "（无法撤销）";
  if (!confirm(`删除会话「${label}」？${extra}`)) return;
  try {
    await api(`/api/session/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (err) {
    alert(`删除失败：${err.message}`);
    return;
  }
  if (state.current && state.current.id === id) {
    state.current = null;
    state.messages = [];
    state.live = {};
    state.reveal = {};
    state.pending = [];
    state.attachments = [];
    staticSig = null;
    liveShellSig = null;
    heroShown = false;
    renderAttachRow();
    $("session-title").textContent = "未选择会话";
    $("session-meta").textContent = "";
    $("input").disabled = true;
    $("btn-send").disabled = true;
    $("btn-stop").disabled = true;
    renderMessages();
  }
  await loadSessions();
  if (!state.current && state.sessions.length) await openSession(state.sessions[0].id);
}

/* ---------------- 消息渲染 ---------------- */

/** 附件两种形态都要能显示：
 *  已落库 { data(base64), mime, source, name } / 待发送 { dataUrl, mime, name } */
function attachSrc(f) {
  if (!f) return "";
  if (typeof f.dataUrl === "string") return f.dataUrl;
  if (typeof f.data === "string" && typeof f.mime === "string") return `data:${f.mime};base64,${f.data}`;
  return "";
}

function renderAttachments(list) {
  if (!Array.isArray(list) || !list.length) return "";
  return `<div class="files">` + list.map((f) => {
    const name = String((f && (f.name || f.filename || f.path || f.file)) || "附件")
      .split(/[\\/]/).pop();
    const src = attachSrc(f);
    const mime = (f && f.mime) || "";
    if (src && /^image\//.test(mime)) {
      return `<a class="shot" href="${esc(src)}" target="_blank" title="${esc(name)}">`
        + `<img src="${esc(src)}" alt="${esc(name)}"></a>`;
    }
    return `<span class="file">📎 ${esc(name)}</span>`;
  }).join("") + `</div>`;
}

function renderToolPart(part, key) {
  const st = (part.state && part.state.status) || (part.executed ? "completed" : "pending");
  const cls = st === "completed" ? "ok" : st === "error" ? "err" : "run";
  const input = part.state && part.state.input ? JSON.stringify(part.state.input, null, 2) : "";
  const out = (part.state && (part.state.output || part.state.error)) || "";
  return `<details class="part" data-k="${esc(key)}">
    <summary><span class="tag ${cls}">${esc(st)}</span> ${esc(part.name || "tool")}</summary>
    <div class="body">${esc(input)}${out ? "\n\n" + esc(String(out).slice(0, 20000)) : ""}</div>
  </details>`;
}

function renderReasoningPart(part, key, msgId) {
  const t = part.text || "";
  if (!t.trim()) return "";
  const head = t.slice(0, 56).replace(/\s+/g, " ");
  if (part.live) {
    return `<details class="part auto-open" data-k="${esc(key)}">
      <summary><span class="tag thinking">思考中…</span> ${esc(head)}</summary>
      <div class="body stream"><span data-live-reasoning data-msgid="${esc(msgId)}">${esc(t)}</span></div>
    </details>`;
  }
  return `<details class="part" data-k="${esc(key)}">
    <summary><span class="tag">思考</span> ${esc(head)}…</summary>
    <div class="body">${esc(t)}</div>
  </details>`;
}

function renderMessage(m) {
  const type = m.type || "assistant";
  if (type === "idle") return "";

  // 系统类提示：做成细分隔线
  if (type === "system" || type === "location-switched" || type === "synthetic") {
    const label = type === "synthetic" ? "自动续写"
      : type === "location-switched" ? "工作目录已切换" : "系统";
    const body = (m.text || "").trim();
    return `<div class="notice"><span class="line"></span>
      <span class="label">${esc(label)}${body ? "：" + esc(body.slice(0, 160)) : ""}</span>
      <span class="line"></span></div>`;
  }

  let parts = "";
  if (Array.isArray(m.content) && m.content.length) {
    parts = m.content.map((p, i) => {
      if (!p || typeof p !== "object") return "";
      const key = `${m.id}:${i}`;
      if (p.type === "text") {
        if (p.live) {
          return `<div class="text"><span data-live-text data-msgid="${esc(m.id)}">`
            + `${renderText(p.text)}</span><span class="caret"></span></div>`;
        }
        return `<div class="text">${renderText(p.text)}</div>`;
      }
      if (p.type === "reasoning") return renderReasoningPart(p, key, m.id);
      if (p.type === "tool") return renderToolPart(p, key);
      // 未知 part：兜底显示原始结构，绝不静默丢弃
      return `<details class="part" data-k="${esc(key)}">
        <summary><span class="tag">${esc(p.type || "unknown")}</span> 原始数据</summary>
        <div class="body">${esc(JSON.stringify(p, null, 2).slice(0, 8000))}</div>
      </details>`;
    }).filter(Boolean).join("");
  } else if (typeof m.text === "string" && m.text.trim()) {
    parts = `<div class="text">${renderText(m.text)}</div>`;
  } else if (typeof m.content === "string" && m.content.trim()) {
    parts = `<div class="text">${renderText(m.content)}</div>`;
  }

  const files = renderAttachments((m.files && m.files.length ? m.files : null)
    || (m.metadata && m.metadata.attachments));

  if (!parts && !files) {
    const raw = JSON.stringify(m, null, 2);
    if (!raw || raw.length < 40) return "";
    return `<div class="msg assistant"><div class="who"><strong>${esc(type)}</strong></div>
      <div class="bubble"><details class="part"><summary><span class="tag">未知结构</span> 原始数据</summary>
      <div class="body">${esc(raw.slice(0, 8000))}</div></details></div></div>`;
  }

  const isUser = type === "user";
  const mm = m.model || (m.metadata && m.metadata.model) || null;
  const modelID = mm ? (mm.id || mm.modelID) : "";
  const agent = (m.agent || (m.metadata && m.metadata.agent)) || "";
  const streaming = type === "assistant" && m.time && !m.time.completed;

  return `<div class="msg ${isUser ? "user" : "assistant"}${m.pending ? " pending" : ""}">
    <div class="who">
      ${isUser ? "" : `<img class="avatar" src="/assets/avatar.png" alt="">`}
      <strong>${isUser ? "我" : "助手"}</strong>
      ${agent ? `<span class="badge">${esc(agent)}</span>` : ""}
      ${modelID ? `<span class="badge">${esc(modelID)}</span>` : ""}
      ${m.time && m.time.created ? `<span class="muted">${fmtTime(m.time.created)}</span>` : ""}
      ${m.pending ? `<span class="tag run">发送中</span>` : ""}
      ${streaming ? `<span class="tag run">生成中</span>` : ""}
    </div>
    <div class="bubble">${parts}${files}</div>
  </div>`;
}

/** 内容指纹：只有真的变了才重建 DOM（避免流式期间反复重建大块） */
function signatureOf(items) {
  return items.map((m) => {
    let len = (m.text || "").length;
    if (Array.isArray(m.content)) {
      for (const p of m.content) {
        len += (p && p.text ? p.text.length : 0);
        const o = p && p.state && (p.state.output || p.state.error);
        len += o ? String(o).length : 0;
      }
    }
    return `${m.id}:${m.type}:${len}:${(m.time && m.time.completed) || 0}`;
  }).join("|");
}

/* ---------------- 实时流：增量缓冲 → 打字机 ---------------- */

function joinBucket(bucket, limit) {
  const s = Object.keys(bucket).sort((a, b) => a - b).map((k) => bucket[k]).join("");
  return limit == null ? s : s.slice(0, limit);
}

function liveTotal(e) {
  const sum = (o) => Object.values(o).reduce((a, s) => a + s.length, 0);
  return { text: sum(e.text), reasoning: sum(e.reasoning) };
}

/** 把增量缓冲拼成「真实助手消息」的结构；revealed 控制打字机显示到第几个字。
 *  不带 time.completed → 渲染层自动标「生成中」。 */
function liveToMessage(id, e, revealed) {
  const content = [];
  const rt = joinBucket(e.reasoning, revealed ? revealed.reasoning : null);
  if (rt) content.push({ type: "reasoning", text: rt, live: true });
  const tt = joinBucket(e.text, revealed ? revealed.text : null);
  if (tt) content.push({ type: "text", text: tt, live: true });
  for (const t of e.tools) {
    content.push({ type: "tool", name: t.name, state: { status: t.status, input: t.input } });
  }
  return {
    id, type: "assistant", agent: e.agent, model: { id: e.model },
    time: { created: e.started || Date.now() },
    content,
  };
}

function pendingLiveIds() {
  const known = new Set(state.messages.map((m) => m.id));
  return Object.keys(state.live).filter((id) => !known.has(id));
}

const HERO_HTML = `<div class="empty">
    <img class="hero-card" src="/assets/hero.jpg" alt="絵梨衣">
    <div class="duck-line1">这个会话还是一张白纸。</div>
    <div class="duck-line2">Sakura ＆ 絵梨衣 のDuck</div>
    <div class="stickers">
      <img src="/assets/sticker-duck.jpg" alt="">
      <img src="/assets/sticker-couple.jpg" alt="">
    </div>
    <div class="credit">同人图 · 仅本地自用，版权归原作者所有</div>
  </div>`;

let staticSig = null;
let staticHtml = "";
let heroShown = false;
let liveShellSig = null;

function swapHtml(el, html) {
  const openKeys = new Set();
  el.querySelectorAll("details[data-k]").forEach((d) => { if (d.open) openKeys.add(d.dataset.k); });
  el.innerHTML = html;
  if (openKeys.size) el.querySelectorAll("details[data-k]").forEach((d) => {
    if (openKeys.has(d.dataset.k)) d.open = true;
  });
}

/** 已完成的消息只在内容变化时重建；生成中的部分交给 paintLiveSlot */
function renderMessages() {
  const pending = pendingLiveIds();
  const empty = !state.messages.length && !pending.length;

  const sSig = signatureOf(state.messages);
  if (sSig !== staticSig) {
    staticSig = sSig;
    staticHtml = state.messages.map(renderMessage).filter(Boolean).join("");
    heroShown = false;
    swapHtml($("msg-static"), staticHtml);
  }
  if (empty && !heroShown) { swapHtml($("msg-static"), HERO_HTML); heroShown = true; }
  else if (!empty && heroShown) { swapHtml($("msg-static"), staticHtml); heroShown = false; }

  paintLiveSlot();
}

/** 只重建「生成中」那一小块：我发的消息（乐观回显）→ 思考 → 回答，一路向下生长 */
function paintLiveSlot() {
  const box = $("messages");
  const slot = $("live-slot");
  const ids = pendingLiveIds();

  const shellSig = state.pending.map((p) => `u:${p.id}`).join(",") + "||" + ids.map((id) => {
    const e = state.live[id];
    return `${id}|${e.tools.length}|${e.agent}|${e.model}`;
  }).join(";");

  if (shellSig !== liveShellSig) {
    liveShellSig = shellSig;
    const pendingHtml = state.pending
      .map((p) => renderMessage({
        id: p.id, type: "user", text: p.text, time: { created: p.time }, pending: true,
      }))
      .join("");
    const liveHtml = ids
      .map((id) => renderMessage(liveToMessage(id, state.live[id], state.reveal[id])))
      .join("");
    slot.innerHTML = pendingHtml + liveHtml;
    slot.querySelectorAll("details.part.auto-open").forEach((d) => { d.open = true; });
  }

  // 内容按打字机进度就位（只改内容、不动结构，光标闪烁不会被打断）
  slot.querySelectorAll("[data-live-text]").forEach((el) => {
    const e = state.live[el.dataset.msgid];
    if (!e) return;
    const r = state.reveal[el.dataset.msgid];
    el.innerHTML = renderText(joinBucket(e.text, r ? r.text : null));
  });
  slot.querySelectorAll("[data-live-reasoning]").forEach((el) => {
    const e = state.live[el.dataset.msgid];
    if (!e) return;
    const r = state.reveal[el.dataset.msgid];
    el.textContent = joinBucket(e.reasoning, r ? r.reasoning : null);   // 向下生长，不内部滚动
  });

  if (state.follow) box.scrollTop = box.scrollHeight;
}

let rafId = null;

function startRevealLoop() {
  if (rafId) return;
  rafId = requestAnimationFrame(revealStep);
}

function revealStep() {
  rafId = null;
  let more = false;
  for (const [id, e] of Object.entries(state.live)) {
    const tgt = liveTotal(e);
    const r = state.reveal[id] || (state.reveal[id] = { text: 0, reasoning: 0 });
    for (const kind of ["text", "reasoning"]) {
      if (r[kind] < tgt[kind]) {
        const gap = tgt[kind] - r[kind];
        r[kind] = Math.min(tgt[kind], r[kind] + Math.max(2, Math.ceil(gap / 4)));
        more = true;
      }
    }
  }
  paintLiveSlot();
  if (more) rafId = requestAnimationFrame(revealStep);
}

/** 事件到达时先立刻画一次（工具/结构变化可见），再启动打字机推进 */
function scheduleLiveRender() {
  paintLiveSlot();
  startRevealLoop();
}

/* ---------------- 会话操作 ---------------- */

async function openSession(id) {
  try {
    state.current = unwrap(await api(`/api/session/${encodeURIComponent(id)}`));
  } catch {
    state.current = state.sessions.find((s) => s.id === id) || { id };
  }
  staticSig = null;
  liveShellSig = null;
  state.live = {};
  state.reveal = {};
  state.pending = [];
  state.attachments = [];
  state.follow = true;
  renderAttachRow();
  $("session-title").textContent = state.current.title || "(无标题)";
  const d = state.current.location && state.current.location.directory;
  $("session-meta").textContent = [state.current.id, d].filter(Boolean).join("  ·  ");
  updateModelBtn();                                  // 顶栏模型按钮跟着当前会话走
  $("input").disabled = false;
  $("btn-send").disabled = false;
  $("btn-stop").disabled = false;
  renderSessions();
  await refreshMessages();
  pollAsks();
}

async function refreshMessages() {
  if (!state.current) return;
  if (state.refreshing) { state.pendingRefresh = true; return; }
  state.refreshing = true;
  try {
    const body = await api(`/api/session/${encodeURIComponent(state.current.id)}/message?limit=80`);
    // ⚠ 接口返回是「新 → 旧」（最新在索引 0），必须按 time.created 升序重排；
    //   少了这一步，界面就会变成「最新在顶、最旧在底」。
    state.messages = (unwrap(body) || []).slice().sort((a, b) =>
      (((a.time || {}).created) || 0) - (((b.time || {}).created) || 0));
    // 对账：权威数据里已有的消息，就丢掉对应的生成缓冲
    const known = new Set(state.messages.map((m) => m.id));
    for (const id of Object.keys(state.live)) if (known.has(id)) delete state.live[id];
    for (const id of Object.keys(state.reveal)) if (known.has(id)) delete state.reveal[id];
    // 我发的消息：服务端已收录就撤掉本地回显（按出现次数配对，避免同文重复误删）
    if (state.pending.length) {
      const left = new Map();
      for (const m of state.messages) {
        if (m.type !== "user") continue;
        const t = (m.text || "").trim();
        left.set(t, (left.get(t) || 0) + 1);
      }
      state.pending = state.pending.filter((p) => {
        const n = left.get(p.text.trim()) || 0;
        if (n > 0) { left.set(p.text.trim(), n - 1); return false; }
        return true;
      });
      liveShellSig = null;
    }
    renderMessages();
  } catch (err) {
    // ⚠ 不要动 $("messages") 的 innerHTML —— 那会把 #msg-static / #live-slot 一起抹掉，
    //   之后每次渲染都拿不到容器，界面永久卡死。只覆盖内容区。
    const box = $("msg-static");
    if (box) box.innerHTML = `<div class="error-box">加载消息失败：${esc(err.message)}</div>`;
    staticSig = null;
  } finally {
    state.refreshing = false;
    if (state.pendingRefresh) { state.pendingRefresh = false; refreshMessages(); }
  }
}

/** 会话的"还没起名"默认标题（这些才算没名字，用户自己改过的一律不碰） */
const DEFAULT_TITLES = ["", "新会话", "(无标题)", "untitled", "new session", "新对话"];
const TITLE_MAX = 30;

/** 第一句提问 → 会话名：压平空白、去掉 markdown 前缀、截断 */
function titleFromQuestion(text) {
  let t = String(text || "").replace(/\s+/g, " ").trim();
  t = t.replace(/^[#>\-*・\s]+/, "");
  if (!t) return "";
  if (t.length > TITLE_MAX) t = t.slice(0, TITLE_MAX).trimEnd() + "…";
  return t;
}

/** 这个会话是否还"没名字"：标题是默认值，且里面还没有用户消息。
 *  两个条件都满足才自动命名 —— 已经聊过的会话不会被改名。 */
function shouldAutoName() {
  if (!state.current) return false;
  if (state.named[state.current.id]) return false;      // 本页已经给它命名过一次了
  const t = String(state.current.title || "").trim().toLowerCase();
  if (!DEFAULT_TITLES.includes(t)) return false;
  return !state.messages.some((m) => m.type === "user") && !state.pending.length;
}

/** 用第一句提问命名：PATCH /api/session/{id} {title} → 204。
 *  失败就算了（不影响发消息）—— 后端还有自己的命名逻辑，SSE 的 session.renamed 会再同步一次。 */
async function autoNameSession(text) {
  const title = titleFromQuestion(text);
  if (!title || !state.current) return;
  const id = state.current.id;
  try {
    await api(`/api/session/${encodeURIComponent(id)}`, {
      method: "PATCH", body: JSON.stringify({ title }),
    });
  } catch { return; }
  if (state.current && state.current.id === id) {
    state.current.title = title;
    $("session-title").textContent = title;
  }
  const s = state.sessions.find((x) => x.id === id);
  if (s) { s.title = title; renderSessions(); }
}

async function send() {
  const input = $("input");
  const typed = input.value.trim();
  const files = outgoingFiles();
  if ((!typed && !files.length) || !state.current) return;

  const atts = state.attachments.map((a) => ({
    name: a.name, mime: a.mime, size: a.size, dataUrl: a.dataUrl,
  }));
  const text = typed || `（附件：${atts.map((a) => a.name).join("、")}）`;
  // 这个会话还没名字的话，就用这句提问命名（用户改过名 / 已经聊过的，绝不覆盖）
  const firstAsk = shouldAutoName() ? text : "";
  if (firstAsk) state.named[state.current.id] = true;   // 记账：本页只命名一次，别依赖消息是否已回传

  input.value = "";
  input.style.height = "auto";
  state.attachments = [];
  renderAttachRow();

  // 先本地回显 —— 让「我的问题」（含附件缩略图）立刻出现，服务端确认后对账撤掉
  const localId = "local_" + Date.now().toString(36);
  state.pending.push({ id: localId, text, time: Date.now(), files: atts });
  state.follow = true;
  paintLiveSlot();

  try {
    await api(`/api/session/${encodeURIComponent(state.current.id)}/prompt`, {
      method: "POST",
      body: JSON.stringify(files.length ? { text, files } : { text }),
    });
    if (firstAsk) await autoNameSession(firstAsk);
    await refreshMessages();
  } catch (err) {
    // 失败就把附件还回输入区，别让用户白选一次
    state.attachments = atts.map((a, i) => ({ id: `att_r${Date.now()}_${i}`, ...a }));
    renderAttachRow();
    state.pending = state.pending.filter((p) => p.id !== localId);
    liveShellSig = null;
    paintLiveSlot();
    const box = $("messages");
    box.insertAdjacentHTML("beforeend", `<div class="error-box">发送失败：${esc(err.message)}</div>`);
    box.scrollTop = box.scrollHeight;
  }
}

async function newSession() {
  try {
    const dir = state.current && state.current.location && state.current.location.directory;
    const payload = { title: "新会话" };
    if (dir) payload.location = { directory: dir };
    const def = defaultModel();
    if (def) payload.model = def;                    // 带上前一次选的"新会话默认模型"
    let body;
    try {
      body = await api("/api/session", { method: "POST", body: JSON.stringify(payload) });
    } catch {
      body = await api("/api/session", { method: "POST", body: JSON.stringify({}) });
    }
    const created = unwrap(body);
    await loadSessions();
    if (created && created.id) await openSession(created.id);
  } catch (err) {
    alert("新建会话失败：" + err.message);
  }
}

async function interrupt() {
  if (!state.current) return;
  try {
    await api(`/api/session/${encodeURIComponent(state.current.id)}/interrupt`,
      { method: "POST", body: "{}" });
  } catch (err) {
    alert("中断失败：" + err.message);
  }
}

/* ---------------- 实时事件流（增量驱动）---------------- */

let es = null;
let refreshTimer = null;
let sessionsTimer = null;

function scheduleRefresh() {
  if (refreshTimer) return;
  refreshTimer = setTimeout(() => { refreshTimer = null; refreshMessages(); }, 300);
}

function scheduleSessions() {
  if (sessionsTimer) return;
  sessionsTimer = setTimeout(() => { sessionsTimer = null; loadSessions(); }, 1200);
}

/** 终态：这些事件之后才值得去取一次权威数据 */
const TERMINAL = /^session\.(text\.ended|step\.ended|step\.streamed|tool\.success|tool\.error|execution\.succeeded|execution\.failed|inbox\.delivered|inbox\.enqueued|created|renamed)$/;

function handleEvent(p) {
  const t = p.type || "";
  const d = p.data || {};
  const sid = d.sessionID || (d.session && d.session.id) || null;
  const mine = !sid || !state.current || sid === state.current.id;

  if (t === "server.connected") { setConn("online", "已连接"); return; }

  // 需要你选择 / 权限请求：SSE 一到就立刻去看，不用等轮询
  if (t.startsWith("form.") || t.startsWith("permission.")) { pollAsks(); return; }

  // ① 文本 / 思考增量 —— 直接追加，绝不重取
  if (t === "session.text.delta" || t === "session.reasoning.delta") {
    if (!mine || !d.assistantMessageID) return;
    const e = liveEnsure(d.assistantMessageID);
    const bucket = t === "session.text.delta" ? e.text : e.reasoning;
    const k = d.ordinal || 0;
    bucket[k] = (bucket[k] || "") + (d.delta || "");
    scheduleLiveRender();
    return;
  }

  // ② 步骤开始：记录 agent / model，让「生成中」气泡有身份
  if (t === "session.step.started" && mine && d.assistantMessageID) {
    const e = liveEnsure(d.assistantMessageID);
    e.agent = d.agent || e.agent;
    e.model = (d.model && d.model.id) || e.model;
    e.started = d.started || Date.now();
    scheduleLiveRender();
    return;
  }

  // ③ 工具调用
  if (t.startsWith("session.tool.") && mine && d.assistantMessageID) {
    const e = liveEnsure(d.assistantMessageID);
    if (t === "session.tool.called") {
      e.tools.push({ name: d.name || d.tool || "tool", input: d.input, status: "running" });
    } else if (t === "session.tool.success" || t === "session.tool.error") {
      const last = e.tools[e.tools.length - 1];
      if (last) last.status = t === "session.tool.success" ? "completed" : "error";
    }
    scheduleLiveRender();
    return;
  }

  // ④ 终态：取权威数据对账
  if (TERMINAL.test(t)) {
    if (mine) scheduleRefresh();
    if (t === "session.created" || t === "session.renamed"
        || t === "session.execution.succeeded" || t === "session.inbox.enqueued") {
      scheduleSessions();
    }
    return;
  }

  // ⑤ 其它 session.* 事件：只刷会话列表（不重取消息，避免洪泛）
  if (t.startsWith("session.") && !t.startsWith("shell.")) scheduleSessions();
}

function liveEnsure(id) {
  if (!state.live[id]) {
    state.live[id] = { text: {}, reasoning: {}, tools: [], agent: "", model: "", started: 0 };
  }
  return state.live[id];
}

function connectEvents() {
  if (es) es.close();
  es = new EventSource("/api/event");
  es.onopen = () => setConn("online", "已连接");
  es.onerror = () => setConn("error", "连接断开，重连中…");
  es.onmessage = (ev) => {
    let p = null;
    try { p = JSON.parse(ev.data); } catch { return; }
    try { handleEvent(p); } catch (err) { console.warn("事件处理异常", err, p); }
  };
}

function setConn(cls, text) {
  $("conn").className = "dot " + cls;
  $("conn-text").textContent = text;
}

/* ---------------- 连接 OpenCode：自动重试，永不要求手动刷新 ---------------- */

let connected = false;
let reloading = false;          // 正在自动刷新（刷新期间不发自家的关窗信号）

/** 服务端报告的前端版本和本页加载的不一致 → 自动刷新一次。
 *  用 sessionStorage 记下已刷过的版本，保证每个版本最多自动刷一次，绝不会无限刷新。 */
function syncUiVersion(served) {
  if (!served || !UI_VER || served === UI_VER || reloading) return false;
  let done = "";
  try { done = sessionStorage.getItem("ui-reloaded") || ""; } catch { /* 隐私模式 */ }
  if (done === served) return false;
  try { sessionStorage.setItem("ui-reloaded", served); } catch { /* ignore */ }
  reloading = true;
  setBanner(`界面已更新到 ${served}，正在自动刷新…`);
  setTimeout(() => { try { location.reload(); } catch { reloading = false; } }, 500);
  return true;
}

function setBanner(text) {
  const el = $("banner");
  if (!text) { el.hidden = true; el.textContent = ""; return; }
  if (el.textContent !== text) el.textContent = text;
  el.hidden = false;
}

async function checkHealth() {
  try {
    const body = await api("/healthz");
    $("upstream").textContent = `OpenCode ${body.version || ""}`;
    syncUiVersion(body.ui);            // 服务端的前端版本变了就自动刷新
    return true;
  } catch {
    $("upstream").textContent = "";
    return false;
  }
}

async function ensureConnected() {
  const ok = await checkHealth();
  if (ok) {
    if (connected) return;
    connected = true;
    setConn("online", "已连接");
    setBanner("");
    await loadSessions();
    connectEvents();
    if (state.sessions.length) {
      if (state.current) await refreshMessages();
      else await openSession(state.sessions[0].id);
    }
    pollAsks();
    return;
  }
  if (connected) {
    connected = false;
    if (es) { es.close(); es = null; }
    setConn("error", "已断开");
    setBanner("与 OpenCode 的连接已断开，正在重连…（不需要手动刷新）");
  } else {
    setConn("error", "连接中");
    setBanner("正在连接 OpenCode…（它可能还在启动，本页面会自动重连）");
  }
}

async function connectionLoop() {
  await ensureConnected();
  setTimeout(connectionLoop, connected ? 5000 : 1500);
}

/** 心跳 + 关窗信号：守护进程靠这个判断面板是否还开着。不要删。 */
function startHeartbeat() {
  const beat = () => fetch("/heartbeat", { method: "POST", cache: "no-store" }).catch(() => {});
  beat();
  setInterval(beat, 4000);

  const bye = () => {
    if (reloading) return;          // 自动刷新不是"关窗"，别发关窗信号（否则守护进程可能杀掉 OpenCode）
    try {
      if (navigator.sendBeacon) { navigator.sendBeacon("/bye"); return; }
    } catch { /* 落到下面的 fetch */ }
    fetch("/bye", { method: "POST", keepalive: true, cache: "no-store" }).catch(() => {});
  };
  window.addEventListener("pagehide", bye);
  window.addEventListener("beforeunload", bye);
}

/* ---------------- 昼 / 夜 主题 ---------------- */

const THEME_KEY = "opencode-ui.theme";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("btn-theme").textContent = theme === "yoru" ? "昼" : "夜";
  $("btn-theme").title = theme === "yoru"
    ? "切回：昼（白衣绯袴）"
    : "切到：夜（黑纹付羽织 · 黄金瞳）";
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* 隐私模式忽略 */ }
}

function initTheme() {
  let theme = "hiru";
  try { theme = localStorage.getItem(THEME_KEY) || "hiru"; } catch { /* ignore */ }
  applyTheme(theme);
  $("btn-theme").addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "yoru" ? "hiru" : "yoru");
  });
}

/* ---------------- 落樱 + 落水涟漪 ----------------
 * 一片花瓣与它激起的涟漪同属一个 unit：计时与漂移由 unit 下发，天然严格同步。 */

function initPetals() {
  const box = $("petals");
  for (let i = 0; i < 16; i++) {
    const unit = document.createElement("span");
    unit.className = "petal-unit";
    unit.style.left = (Math.random() * 100).toFixed(1) + "vw";
    unit.style.setProperty("--dur", (11 + Math.random() * 12).toFixed(1) + "s");
    unit.style.setProperty("--delay", (-Math.random() * 22).toFixed(1) + "s");
    unit.style.setProperty("--drift", (Math.random() * 170 - 85).toFixed(0) + "px");

    const size = 7 + Math.random() * 8;
    const petal = document.createElement("span");
    petal.className = "petal";
    petal.style.width = size.toFixed(1) + "px";
    petal.style.height = size.toFixed(1) + "px";

    const ripple = document.createElement("span");
    ripple.className = "ripple";
    ripple.style.width = (size * 2.4).toFixed(1) + "px";
    ripple.style.height = (size * 0.85).toFixed(1) + "px";

    const ripple2 = document.createElement("span");
    ripple2.className = "ripple r2";
    ripple2.style.width = (size * 2.4).toFixed(1) + "px";
    ripple2.style.height = (size * 0.85).toFixed(1) + "px";

    unit.appendChild(petal);
    unit.appendChild(ripple);
    unit.appendChild(ripple2);

    if (Math.random() < 0.75) {                     // 不是每滴都溅起水花，随机更像真的
      const spike = document.createElement("span");
      spike.className = "spike";
      spike.style.width = "2.5px";
      spike.style.height = (size * 0.9).toFixed(1) + "px";
      unit.appendChild(spike);
    }
    box.appendChild(unit);
  }
  // 落樱 / 壁纸 已并入设置弹窗（顶栏不再单独放按钮）
}

/* ---------------- 侧栏收起 / 展开 ----------------
 * 收起 = 纯听歌模式：侧栏 + 对话消息都隐藏，只留歌词浮层 + 播放条 + 输入框。 */

const SIDEBAR_KEY = "opencode-ui.sidebar";

function applySidebar(mode) {
  document.documentElement.dataset.sidebar = mode;
  $("btn-sidebar").title = (mode === "closed" ? "展开（Ctrl+B）" : "收起（Ctrl+B）");
  try { localStorage.setItem(SIDEBAR_KEY, mode); } catch { /* 隐私模式忽略 */ }
  syncLyricsVisibility();
}

function initSidebar() {
  let mode = "open";
  try { mode = localStorage.getItem(SIDEBAR_KEY) || "open"; } catch { /* ignore */ }
  applySidebar(mode);
  const flip = () => applySidebar(
    document.documentElement.dataset.sidebar === "closed" ? "open" : "closed");
  $("btn-sidebar").addEventListener("click", flip);
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "b" || e.key === "B")) {
      e.preventDefault();
      flip();
    }
  });
}

/* ---------------- 左上角名字（点击可改） ---------------- */

const BRAND_KEY = "opencode-ui.brandName";
const BRAND_DEFAULT = "絵梨衣";

function applyBrandName(name) {
  $("brand-name").textContent = name;
  document.title = `${name} · OpenCode`;
  try { localStorage.setItem(BRAND_KEY, name); } catch { /* 隐私模式忽略 */ }
}

function initBrandName() {
  const el = $("brand-name");
  let saved = "";
  try { saved = localStorage.getItem(BRAND_KEY) || ""; } catch { /* ignore */ }
  if (saved) applyBrandName(saved);

  el.addEventListener("click", () => {
    if (el.isContentEditable) return;
    el.contentEditable = "plaintext-only";            // 只收纯文本，不会粘进 HTML
    el.focus();
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  });
  el.addEventListener("blur", () => {
    el.contentEditable = "false";
    const v = (el.textContent || "").trim().slice(0, 24);
    applyBrandName(v || BRAND_DEFAULT);
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); el.blur(); }
    if (e.key === "Escape") {
      e.preventDefault();
      let cur = BRAND_DEFAULT;
      try { cur = localStorage.getItem(BRAND_KEY) || BRAND_DEFAULT; } catch { /* ignore */ }
      el.textContent = cur;
      el.blur();
    }
  });
}

/* ---------------- 附件：粘贴 / 拖拽 / 选择文件 ----------------
 * prompt 接口的 files[].uri 接受 data: URL，服务端会规范化成 inline 附件。 */

const MAX_ATTACH_BYTES = 12 * 1024 * 1024;
const DOWNSCALE_OVER = 1.2 * 1024 * 1024;
const MAX_EDGE = 1600;
const MAX_ATTACH_COUNT = 8;

let attachSeq = 0;

function humanSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

const readAsDataURL = (blob) => new Promise((res, rej) => {
  const fr = new FileReader();
  fr.onload = () => res(String(fr.result));
  fr.onerror = () => rej(new Error("读取失败"));
  fr.readAsDataURL(blob);
});

/** 大图先缩到 MAX_EDGE 内再编码 —— 否则粘贴一张全屏截图就是十几 MB 的 base64 */
async function shrinkImage(file) {
  const needDownscale = file.size > DOWNSCALE_OVER;
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
  if (!needDownscale && scale === 1) return file;
  const w = Math.max(1, Math.round(bmp.width * scale));
  const h = Math.max(1, Math.round(bmp.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  canvas.getContext("2d").drawImage(bmp, 0, 0, w, h);
  const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.86));
  if (!blob) return file;
  const base = (file.name || "image").replace(/\.[^.]+$/, "");
  return new File([blob], `${base}.jpg`, { type: "image/jpeg" });
}

async function addFiles(fileList) {
  const problems = [];
  for (const original of Array.from(fileList || [])) {
    if (state.attachments.length >= MAX_ATTACH_COUNT) {
      problems.push(`最多 ${MAX_ATTACH_COUNT} 个附件`);
      break;
    }
    let file = original;
    if ((file.type || "").startsWith("image/")) {
      try { file = await shrinkImage(file); } catch { /* 压缩失败就用原图 */ }
    }
    if (file.size > MAX_ATTACH_BYTES) {
      problems.push(`${original.name} 超过 ${humanSize(MAX_ATTACH_BYTES)}`);
      continue;
    }
    let dataUrl = "";
    try {
      dataUrl = await readAsDataURL(file);
    } catch {
      problems.push(`${original.name} 读取失败`);
      continue;
    }
    state.attachments.push({
      id: `att_${++attachSeq}`,
      name: file.name || "粘贴的图片.jpg",
      mime: file.type || "application/octet-stream",
      size: file.size,
      dataUrl,
    });
  }
  renderAttachRow();
  if (problems.length) alert(problems.join("\n"));
}

function renderAttachRow() {
  const row = $("attach-row");
  const list = state.attachments;
  row.hidden = !list.length;
  row.innerHTML = list.map((a) => {
    const isImg = /^image\//.test(a.mime);
    const thumb = isImg
      ? `<img class="thumb" src="${esc(a.dataUrl)}" alt="">`
      : `<span class="thumb">📄</span>`;
    return `<span class="attach" data-id="${esc(a.id)}">${thumb}`
      + `<span class="meta"><b>${esc(a.name)}</b><i>${humanSize(a.size)}</i></span>`
      + `<button type="button" class="rm" title="移除">×</button></span>`;
  }).join("");
}

function initAttachments() {
  $("btn-attach").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (e) => {
    addFiles(e.target.files);
    e.target.value = "";
  });

  $("attach-row").addEventListener("click", (e) => {
    const btn = e.target.closest(".rm");
    if (!btn) return;
    const id = btn.closest(".attach").dataset.id;
    state.attachments = state.attachments.filter((a) => a.id !== id);
    renderAttachRow();
  });

  // 粘贴图片（截图后直接 Ctrl+V）
  $("input").addEventListener("paste", (e) => {
    const cd = e.clipboardData;
    if (!cd) return;
    const files = [];
    for (const it of cd.items || []) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (!files.length) return;
    const hasText = (cd.getData("text/plain") || "").length > 0;
    if (!hasText) e.preventDefault();
    addFiles(files);
  });

  // 拖拽到窗口任意位置
  const isFileDrag = (e) => Array.from((e.dataTransfer && e.dataTransfer.types) || []).includes("Files");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    depth += 1;
    $("drop-veil").hidden = false;
  });
  window.addEventListener("dragover", (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  window.addEventListener("dragleave", (e) => {
    if (!isFileDrag(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) $("drop-veil").hidden = true;
  });
  window.addEventListener("drop", (e) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    depth = 0;
    $("drop-veil").hidden = true;
    addFiles(e.dataTransfer.files);
  });
}

/** 附件 → prompt 接口需要的 files[] */
function outgoingFiles() {
  return state.attachments.map((a) => ({ uri: a.dataUrl, name: a.name }));
}

/* ---------------- QQ音乐联动 ----------------
 * 后端：/qq/state（曲目/状态/音量/频谱可用性）、/qq/control、/qq/volume、/qq/lyrics、
 *       /qq/search、/qq/app（启停客户端）、/qq/spectrum（真频谱 12 个频段）
 * 侧栏收起时，画面中央显示滚动歌词。 */

const MUSIC = {
  on: false, qqRunning: false, real: false, folded: false, panel: null, lyrWanted: false,
  seekDragging: false, cur: 0, dur: 0,
  state: {}, lyrics: [], lyrKey: "", lyIdx: -1,
  posAt: 0, vol: 100, volDragging: false, volTimer: null,
};

/** QQ音乐 的 SMTC 状态不一定报 Playing（实测见过 Opened / Paused），
 *  所以「跳动」的判定放宽：只要不是在暂停/停止，就认为在放。 */
/** 当前在放什么：面板内播放（网易云/QQ 搜索）优先，否则 QQ音乐 SMTC */
function currentTrack() {
  if (MUSIC.panel) return { title: MUSIC.panel.name || "", artist: MUSIC.panel.artists || "" };
  const st = MUSIC.state || {};
  return { title: st.title || "", artist: st.artist || "" };
}

const musicPlaying = () => {
  const a = $("mus-audio");
  if (MUSIC.panel) return !!(a && !a.paused && !a.ended);
  const s = String((MUSIC.state || {}).status || "");
  return MUSIC.on && s !== "Paused" && s !== "Stopped" && s !== "Closed";
};

/** 播放位置：面板内用 <audio>.currentTime；QQ SMTC 用服务端位置 + 两次轮询之间本地补时 */
function musicPos() {
  const a = $("mus-audio");
  if (MUSIC.panel && a) return a.currentTime || 0;
  const base = Number((MUSIC.state || {}).pos || 0);
  return base + (musicPlaying() ? (Date.now() - MUSIC.posAt) / 1000 : 0);
}

function renderPlayer() {
  const st = MUSIC.state || {};
  const bar = $("player");
  const audio = $("mus-audio");
  const panel = MUSIC.panel;                       // 面板内播放（网易云/QQ 搜索）优先显示
  const playing = panel ? !!(audio && !audio.paused) : musicPlaying();
  bar.classList.add("on");                     // 播放条常驻（折叠时只剩 ▾ 箭头）
  bar.classList.toggle("folded", !!MUSIC.folded);
  bar.classList.toggle("playing", playing);
  if (panel) {
    $("p-title").textContent = panel.name || "（未知曲目）";
    $("p-artist").textContent = (panel.artists ? panel.artists + " · " : "") + "面板播放";
  } else {
    $("p-title").textContent = MUSIC.on ? (st.title || "（未知曲目）") : "未在播放";
    $("p-artist").textContent = MUSIC.on ? (st.artist || "")
      : (MUSIC.qqRunning ? "QQ音乐已启动" : "");
  }
  $("p-toggle").classList.toggle("playing", playing);
  $("p-toggle").title = playing ? "暂停" : "播放";
  $("p-prev").disabled = !!panel || !MUSIC.on || st.canPrev === false;
  $("p-next").disabled = !!panel || !MUSIC.on || st.canNext === false;
  updateSeek();
}

/** 进度条：面板内播放可拖动跳转；QQ音乐(SMTC) 只显示进度（客户端没有跳转接口） */
function updateSeek() {
  const seek = $("p-seek");
  if (!seek) return;
  const a = $("mus-audio");
  const panel = !!MUSIC.panel;
  let cur = 0, dur = 0;
  if (panel && a) { cur = a.currentTime || 0; dur = a.duration || 0; }
  else { cur = musicPos(); dur = Number((MUSIC.state || {}).end || 0); }
  MUSIC.cur = cur; MUSIC.dur = dur;
  if (!MUSIC.seekDragging) {
    seek.value = dur ? String(Math.round(cur / dur * Number(seek.max))) : "0";
  }
  const ec = $("p-cur"); if (ec) ec.textContent = musTime(cur);
  const ed = $("p-dur"); if (ed) ed.textContent = dur ? musTime(dur) : "0:00";
  seek.disabled = !(panel && dur);
  seek.title = panel ? "拖动跳转到任意位置" : "QQ音乐客户端不支持拖动";
}

function renderLyrics() {
  const box = $("ly-scroll");
  MUSIC.lyIdx = -1;
  if (!MUSIC.lyrics.length) {
    box.innerHTML = `<p class="noly">${MUSIC.lyrKey ? "这首歌没找到歌词" : "正在找歌词…"}</p>`;
    return;
  }
  highlightLyrics(true);
}

/** 只显示三行：上一句 / 当前句 / 下一句 */
function highlightLyrics(force) {
  if (!MUSIC.lyrics.length) return;
  const pos = musicPos();
  let idx = 0;
  for (let i = 0; i < MUSIC.lyrics.length; i++) {
    if (MUSIC.lyrics[i].t <= pos) idx = i; else break;
  }
  if (!force && idx === MUSIC.lyIdx) return;             // 没换句就不动 DOM
  MUSIC.lyIdx = idx;
  // 三行：上一句 / 当前句 / 下一句；有中文翻译时在原文下面加一行小字（设置里可关）
  const line = (k, cls) => {
    const l = (k >= 0 && k < MUSIC.lyrics.length) ? MUSIC.lyrics[k] : null;
    if (!l) return `<p class="${cls}"></p>`;
    const zh = l.zh ? `<span class="zh">${esc(l.zh)}</span>` : "";
    return `<p class="${cls}">${esc(l.s)}${zh}</p>`;
  };
  $("ly-scroll").innerHTML = line(idx - 1, "prev") + line(idx, "cur") + line(idx + 1, "next");
}

async function ensureLyrics() {
  const { title, artist } = currentTrack();
  const key = `${title}|${artist}`;
  if (!title || key === MUSIC.lyrKey) return;
  MUSIC.lyrKey = key;
  MUSIC.lyrics = [];
  renderLyrics();
  try {
    const r = await api(`/qq/lyrics?title=${encodeURIComponent(title)}`
      + `&artist=${encodeURIComponent(artist || "")}`
      + `&tr=${SET.zh ? 1 : 0}`);
    if (MUSIC.lyrKey !== key) return;                    // 期间换歌了，丢弃这次结果
    MUSIC.lyrics = (r.lines || []).filter((l) => l && l.s);
  } catch { /* 拿不到歌词就不显示，不影响播放 */ }
  renderLyrics();
}

/** 收起侧栏 = 纯听歌模式：歌词浮层显示；没有歌词时给一行提示，别留一片空白 */
function syncLyricsVisibility() {
  const collapsed = document.documentElement.dataset.sidebar === "closed";
  const playing = !!(MUSIC.on || MUSIC.panel);
  const want = MUSIC.lyrWanted || collapsed;            // 「词」按钮 或 收起侧栏 都显示歌词
  const withLyrics = want && playing && MUSIC.lyrics.length > 0;
  document.documentElement.classList.toggle("lyrics-on", withLyrics);
  document.documentElement.classList.toggle("collapsed-focus", collapsed);
  const showHint = want && playing && !MUSIC.lyrics.length;
  if (showHint) {
    if (!MUSIC.hintShown) {
      $("ly-scroll").innerHTML = `<p class="noly">${MUSIC.lyrKey ? "这首歌没找到歌词" : "正在找歌词…"}</p>`;
      MUSIC.hintShown = true;
    }
  } else {
    MUSIC.hintShown = false;
  }
}

async function pollMusic() {
  try {
    const r = await api("/qq/state");
    MUSIC.on = !!(r.music && Number(r.music.sessions || 0) > 0);
    MUSIC.state = r.music || {};
    MUSIC.qqRunning = !!r.qqRunning;
    MUSIC.real = !!r.spectrum;                           // 真频谱采样是否可用
    MUSIC.posAt = Date.now();
    if (typeof r.volume === "number" && r.volume >= 0 && !MUSIC.volDragging) {
      MUSIC.vol = r.volume;
      $("p-vol").value = String(r.volume);
      $("p-vol-num").textContent = String(r.volume);
    }
  } catch {
    MUSIC.on = false;
    MUSIC.state = {};
  }
  renderPlayer();
  await ensureLyrics();
  syncLyricsVisibility();
  setTimeout(pollMusic, 1000);
}

/* ---- 音频条：优先用真频谱；拿不到就退回合成动画 ---- */

let spectrumBusy = false;

function buildVis(box, n) {
  if (!box || box.childElementCount === n) return;
  box.innerHTML = Array.from({ length: n }, () => "<span></span>").join("");
  [...box.children].forEach((el, i) => {
    el.style.animationDuration = (0.5 + (i % 5) * 0.13).toFixed(2) + "s";
    el.style.animationDelay = (-(i * 0.07)).toFixed(2) + "s";
  });
}

const VIS_WEIGHT = 48;               // 柱子数，与 spectrum.py 的 BANDS 对齐（没数据时先摆这些）
let visIdleAt = 0;

/* 频谱值(0~1) → 柱高百分比。
   不做压缩的话等于把 0~1 直接当 0~100%，中等音量就顶到天（"一点声音就拉满"）；
   所以先按 gamma 把小声压矮、低于 gate 的直接当静音，再封顶留出余量 ——
   结果是"峰更高、谷更空"，起伏更明显。想更灵敏/更钝就调这三个常数。 */
const VIS_TOP = 88;                  // 上限百分比：再响也留一截，不会拉满
const VIS_GAMMA = 1.7;               // >1 → 小声音压得更矮（起伏更大）
const VIS_GATE = 0.08;               // 低于此值当静音，让"谷"落到底
function visHeight(v, min) {
  const floor = min || 4;
  const x = Math.max(0, Math.min(1, Number(v) || 0));
  if (x < VIS_GATE) return floor;
  return Math.max(floor, Math.round(Math.pow(x, VIS_GAMMA) * VIS_TOP));
}

/** 播放条里那条比较窄，柱子太多会糊成一片 —— 等间隔抽稀 */
function pickEven(arr, n) {
  if (!arr || arr.length <= n) return arr || [];
  const out = [];
  for (let i = 0; i < n; i++) out.push(arr[Math.round(i * (arr.length - 1) / (n - 1))]);
  return out;
}

/** 音频条：展开时在播放条里（#p-vis），收起侧栏时在输入框上方（#vis-bar）。
 *  数据优先用真频谱（系统回环，任何声音都会跳）；拿不到就退回 CSS 合成动画。 */
async function drawSpectrum() {
  const bar = $("player");
  const vis = $("vis-bar");
  const collapsed = document.documentElement.dataset.sidebar === "closed";
  const box = collapsed ? vis : $("p-vis");
  if (!box) return;
  const playing = musicPlaying();
  // 收起后播放条不显示，采样改由上面那条承担 → 收起时也持续采（静音时自然落到近零）
  const active = MUSIC.real && (collapsed || (playing && bar.classList.contains("on")));
  bar.classList.toggle("real", active && !collapsed);
  vis.classList.toggle("real", active && collapsed);
  vis.classList.toggle("playing", collapsed && playing);
  if (!active || spectrumBusy) return;
  if (!playing) {                                  // 没在放歌时降频，别 70ms 打一次接口
    const now = Date.now();
    if (now - visIdleAt < 600) return;
    visIdleAt = now;
  }
  spectrumBusy = true;
  try {
    const r = await api("/qq/spectrum");
    const bars = (r && r.bars) || [];
    if (!bars.length) return;
    const series = box === vis ? bars : pickEven(bars, 12);   // 播放条那条抽稀，别糊成一片
    buildVis(box, series.length);
    const min = box === vis ? 5 : 8;   // 播放条里那条更矮，抬头给足一点
    [...box.children].forEach((el, i) => {
      el.style.height = visHeight(series[i], min) + "%";
    });
  } catch { /* 拿不到就继续用合成动画 */ } finally {
    spectrumBusy = false;
  }
}

function initMusic() {
  const ctl = (action) => api("/qq/control", {
    method: "POST", body: JSON.stringify({ action }),
  }).catch(() => { /* 失败无所谓，下一轮轮询会刷新状态 */ });

  $("p-prev").addEventListener("click", () => ctl("prev"));
  $("p-next").addEventListener("click", () => ctl("next"));
  $("p-toggle").addEventListener("click", () => {
    const audio = $("mus-audio");
    if (MUSIC.panel && audio) {                   // 面板内播放：直接控制 <audio>
      if (audio.paused) audio.play().catch(() => {}); else audio.pause();
      setTimeout(renderPlayer, 60);
      return;
    }
    ctl("playpause");
  });
  $("p-lyric-btn").addEventListener("click", () => {
    MUSIC.lyrWanted = !MUSIC.lyrWanted;
    if (MUSIC.lyrWanted && !MUSIC.lyrics.length) ensureLyrics();
    syncLyricsVisibility();
  });

  // 播放条自身：收起 / 展开（像会话列表那样，状态存本地）
  // 默认折叠（只留 ▾ 箭头）；用户展开过才记住（换新 key：旧的 "0"=展开 不再算数）
  try { MUSIC.folded = localStorage.getItem("opencode-ui.player.folded2") !== "0"; } catch { MUSIC.folded = true; }
  const applyFold = () => {
    $("player").classList.toggle("folded", !!MUSIC.folded);
    try { localStorage.setItem("opencode-ui.player.folded2", MUSIC.folded ? "1" : "0"); } catch { /* 隐私模式 */ }
  };
  $("p-fold").addEventListener("click", () => { MUSIC.folded = !MUSIC.folded; applyFold(); });
  applyFold();

  // 进度条：拖动即跳（面板内播放）；QQ SMTC 只显示
  const seek = $("p-seek");
  seek.addEventListener("input", () => {
    const ratio = Number(seek.value) / Number(seek.max || 1000);
    const a = $("mus-audio");
    MUSIC.seekDragging = true;
    if (MUSIC.panel && a && a.duration) { try { a.currentTime = ratio * a.duration; } catch { /* 忽略 */ } }
    const ec = $("p-cur"); if (ec) ec.textContent = musTime(ratio * (MUSIC.dur || 0));
  });
  seek.addEventListener("change", () => { MUSIC.seekDragging = false; updateSeek(); });

  // 条内搜索 = 和「乐」弹窗同一套（搜到即点播，走面板内播放）
  $("p-search-btn").addEventListener("click", () => {
    const box = $("p-search");
    const res = $("p-results");
    const show = box.hidden;
    box.hidden = !show;
    res.hidden = true;                     // 结果区跟着一起收起（重开时是干净的）
    if (show) $("p-q").focus();
  });
  $("p-q-go").addEventListener("click", () => musicSearch($("p-q").value));
  $("p-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); musicSearch($("p-q").value); }
  });
  $("p-results").addEventListener("click", (e) => {
    const it = e.target.closest(".it[data-play]");
    if (it) musicPlay(it.dataset.play);
  });

  // 平台切换（条内 + 弹窗共用同一状态）
  document.querySelectorAll(".wp-pb").forEach((b) => {
    b.addEventListener("click", () => setProvider(b.dataset.p || "netease"));
  });
  renderProvider();

  $("p-vol").addEventListener("input", (e) => {
    MUSIC.vol = Number(e.target.value);
    $("p-vol-num").textContent = String(MUSIC.vol);
    MUSIC.volDragging = true;
    clearTimeout(MUSIC.volTimer);
    MUSIC.volTimer = setTimeout(() => {
      api("/qq/volume", { method: "POST", body: JSON.stringify({ value: MUSIC.vol }) })
        .catch(() => {})
        .finally(() => { MUSIC.volDragging = false; });
    }, 120);
  });

  setInterval(() => {
    updateSeek();
    if (document.documentElement.classList.contains("lyrics-on")) highlightLyrics();
  }, 250);
  buildVis($("vis-bar"), VIS_WEIGHT);                    // 收起侧栏时输入框上方那条先摆好柱子
  setInterval(drawSpectrum, 70);                         // 真频谱：70ms 一帧

  pollMusic();
}

/* ---------------- 弹窗：需要你选择（表单 / 权限请求）----------------
 * 这两类交互原本只由官方客户端弹窗 —— 自建界面不实现的话 agent 会被静默卡住，
 * 而本项目还会最小化官方窗口，用户根本看不到。 */

const ASK = { form: null, perm: null, picks: {}, sig: "" };

function askHide() {
  $("ask").hidden = true;
  ASK.form = null;
  ASK.perm = null;
  ASK.picks = {};
  ASK.sig = "";
}

function fieldHtml(f) {
  const head = `<div class="t">${esc(f.title || f.key)}${f.required ? " *" : ""}</div>`
    + (f.description ? `<div class="d">${esc(f.description)}</div>` : "");
  const k = esc(f.key);
  if (Array.isArray(f.options) && f.options.length) {
    const multi = f.type === "multiselect";
    const opts = f.options.map((o) => `<button type="button" class="ask-opt"`
      + ` data-key="${k}" data-value="${esc(o.value)}"${multi ? ' data-multi="1"' : ""}`
      + ` title="${esc(o.description || "")}">${esc(o.label)}</button>`).join("");
    return `<div class="ask-field">${head}<div class="ask-opts">${opts}</div></div>`;
  }
  if (f.type === "boolean") {
    return `<div class="ask-field">${head}<div class="ask-opts">`
      + `<button type="button" class="ask-opt" data-key="${k}" data-value="true">是</button>`
      + `<button type="button" class="ask-opt" data-key="${k}" data-value="false">否</button>`
      + `</div></div>`;
  }
  if (f.type === "external") {
    return `<div class="ask-field">${head}<a href="${esc(f.url)}" target="_blank" rel="noreferrer">打开链接</a></div>`;
  }
  const it = (f.type === "number" || f.type === "integer") ? "number" : "text";
  return `<div class="ask-field">${head}<input type="${it}" data-key="${k}"`
    + (f.placeholder ? ` placeholder="${esc(f.placeholder)}"` : "")
    + (f.default !== undefined && f.default !== null ? ` value="${esc(String(f.default))}"` : "")
    + `></div>`;
}

/** 把已选中的项重新标出来（重建 DOM 后恢复选择） */
function restorePicks() {
  const body = $("ask-body");
  for (const [key, val] of Object.entries(ASK.picks || {})) {
    const vals = Array.isArray(val) ? val : [val];
    for (const v of vals) {
      const el = body.querySelector(`.ask-opt[data-key="${CSS.escape(key)}"][data-value="${CSS.escape(String(v))}"]`);
      if (el) el.classList.add("sel");
    }
  }
}

function renderAsk(forms, perms) {
  const perm = perms[0] || null;
  const form = forms[0] || null;
  if (!perm && !form) { askHide(); return; }

  // ★ 同一条待处理项不重建 DOM：否则每次轮询（1.2s）都会重写 innerHTML，
  //   用户刚点的选择、输入的文字全被清掉，表现就是「能看见弹窗但选不中」。
  const sig = perm ? `p:${perm.id}` : `f:${form.id}`;
  if (sig === ASK.sig && !$("ask").hidden) {
    restorePicks();
    return;
  }
  ASK.sig = sig;
  ASK.picks = {};

  if (perm) {
    ASK.perm = perm;
    ASK.form = null;
    $("ask-kind").textContent = "权限请求";
    $("ask-title").textContent = perm.action || "需要授权";
    const res = (perm.resources || []).map((r) => `<li>${esc(r)}</li>`).join("");
    $("ask-body").innerHTML =
      (perm.message ? `<div class="d">${esc(perm.message)}</div>` : "")
      + (res ? `<ul class="ask-res">${res}</ul>` : "");
    $("ask-actions").innerHTML =
      `<span id="ask-msg" class="ask-msg"></span>`
      + `<button data-dec="reject" class="danger">拒绝</button>`
      + `<button data-dec="once">允许一次</button>`
      + `<button data-dec="always" class="primary">始终允许</button>`;
  } else {
    ASK.form = form;
    ASK.perm = null;
    $("ask-kind").textContent = "需要你选择";
    $("ask-title").textContent = form.title || "请选择";
    $("ask-body").innerHTML = (form.fields || []).filter((f) => !f.hidden).map(fieldHtml).join("");
    $("ask-actions").innerHTML = `<span id="ask-msg" class="ask-msg"></span>`
      + `<button id="ask-submit" class="primary">确定</button>`;
  }
  $("ask").hidden = false;
}

function coerce(f, v) {
  if (f.type === "boolean") return v === true || v === "true";
  if (f.type === "number" || f.type === "integer") return Number(Array.isArray(v) ? v[0] : v);
  if (f.type === "multiselect") return Array.isArray(v) ? v : [v];
  return Array.isArray(v) ? v[0] : v;
}

/** 按字段类型把界面上的选择收成 answer 对象；优先用 ASK.picks（DOM 重建也不丢） */
function collectAnswer(form) {
  const body = $("ask-body");
  const answer = {};
  for (const f of (form.fields || [])) {
    if (f.hidden) continue;
    const picked = ASK.picks[f.key];
    const hasPicked = Array.isArray(picked) ? picked.length > 0
      : (picked !== undefined && picked !== "");
    if (hasPicked) { answer[f.key] = coerce(f, picked); continue; }

    const inp = body.querySelector(`input[data-key="${CSS.escape(f.key)}"]`);
    if (inp) {
      const raw = inp.value;
      if (raw !== "") { answer[f.key] = coerce(f, raw); continue; }
    }
    if (f.default !== undefined && f.default !== null) answer[f.key] = coerce(f, f.default);
  }
  return answer;
}

/** 提交表单。失败原因直接写在弹窗里（不用 alert，方便复制排查） */
async function submitAsk() {
  const msg = $("ask-msg");
  const say = (t) => { if (msg) msg.textContent = t; };
  if (!ASK.form) { say("内部错误：没有待提交的表单"); return; }
  const form = ASK.form;
  const answer = collectAnswer(form);
  const missing = (form.fields || []).filter((f) => !f.hidden && f.required
    && (answer[f.key] === undefined || answer[f.key] === ""));
  if (missing.length) {
    say(`还差：${missing.map((f) => f.title || f.key).join("、")}`);
    return;
  }
  say("提交中…");
  const url = `/api/session/${encodeURIComponent(form.sessionID)}`
    + `/form/${encodeURIComponent(form.id)}/reply`;
  try {
    const res = await fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    });
    if (!res.ok) {
      const t = await res.text();
      say(`提交失败 ${res.status}：${t.slice(0, 200)}`);
      return;
    }
    askHide();
    pollAsks();
  } catch (err) {
    say(`提交异常：${err.message}`);
  }
}

async function pollAsks() {
  try {
    if (state.current) {
      const sid = encodeURIComponent(state.current.id);
      const [f, p] = await Promise.all([
        api(`/api/session/${sid}/form`).catch(() => null),
        api(`/api/session/${sid}/permission`).catch(() => null),
      ]);
      renderAsk((f && f.data) || [], (p && p.data) || []);
    }
  } catch { /* 轮询失败就下一轮再说 */ }
  setTimeout(pollAsks, 1200);
}

function initAsk() {
  window.__askSubmit = submitAsk;                       // 内联 onclick 兜底用

  $("ask-body").addEventListener("click", (e) => {
    const b = e.target.closest(".ask-opt");
    if (!b) return;
    const key = b.dataset.key;
    const val = b.dataset.value;
    if (b.dataset.multi) {                              // 多选：切换并记账
      b.classList.toggle("sel");
      const cur = Array.isArray(ASK.picks[key]) ? ASK.picks[key].slice() : [];
      const i = cur.indexOf(val);
      if (b.classList.contains("sel")) { if (i < 0) cur.push(val); }
      else if (i >= 0) cur.splice(i, 1);
      ASK.picks[key] = cur;
    } else {
      $("ask-body").querySelectorAll(`.ask-opt[data-key="${CSS.escape(key)}"]`)
        .forEach((x) => x.classList.remove("sel"));
      b.classList.add("sel");
      ASK.picks[key] = val;                             // 单选：记账
    }
    if (e.detail >= 2) submitAsk();                      // 选项上双击 = 直接提交
  });

  $("ask-actions").addEventListener("click", async (e) => {
    const dec = e.target.closest("[data-dec]");
    if (dec && ASK.perm) {
      const perm = ASK.perm;
      const say = (t) => { const m = $("ask-msg"); if (m) m.textContent = t; };
      try {
        const res = await fetch(`/api/session/${encodeURIComponent(perm.sessionID)}`
          + `/permission/${encodeURIComponent(perm.id)}/reply`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: dec.dataset.dec }),
        });
        if (!res.ok) { say(`回复失败 ${res.status}`); return; }
      } catch (err) { say(`回复异常：${err.message}`); return; }
      askHide();
      pollAsks();
      return;
    }
    if (e.target.closest("#ask-submit")) submitAsk();
  });
}

/* ---------------- 动态壁纸：Wallpaper Engine 的 mp4 原片 ----------------
 * 收起侧栏 → **缓慢启动**：转速由静止慢慢加上去；
 * 展开会话 → **缓慢停止**：转速慢慢减到停 —— **停住的那一帧就是会话界面的静态背景**
 *            （直接把视频暂停在那里，不再抓帧、不再有 #bg-freeze 层）。
 * 没有可用原片（没装 WE / 没订阅 / 系统要求减少动态）→ 两侧都用主题自带的静态壁纸，不做动画。
 * 素材与选择由 server.py 提供：/live/wallpapers（生效清单）、/live/list + /live/pick（选择器）。 */

let liveRate = 0.8;             // 正常播放速度（1 = 原速）。可在设置面板里调（SET.rate）
const LIVE_RAMP_IN = 600;       // 缓慢启动：从静止加到 liveRate 用多久（ms）
const LIVE_RAMP_OUT = 1100;     // 缓慢停止：从当前速度减到静止用多久（ms）
// 起步速率：⚠ Chromium 的倍速**下限是 0.0625**（1/16），给更小会抛 NotSupportedError，
// 而且那个异常会把整个流程打断（踩过：0.06 导致"收起后完全没反应"）。所以取 0.3。
const LIVE_START_RATE = 0.3;
let liveReload = null;          // initLiveBg 里赋值，供壁纸选择器选完立刻换片
let liveApply = null;           // initLiveBg 里赋值：设置一变就立刻作用到动态壁纸

/* ---------------- 界面设置（顶栏「设」按钮）----------------
 * 存在 localStorage，改完立刻生效。以后新增的可调项也往这里放。
 * ⚠ SET 必须在 initLiveBg 之前声明（sync 里会读 SET.live），而 boot() 在文件末尾才跑，所以没问题。 */

const SET_KEY = "opencode-ui.settings";
const SET_DEFAULT = {
  dimHiru: 100, dimYoru: 80, blur: 18, rate: 80,
  live: true, taskbar: true, petals: true, zh: true,
};
const SET = Object.assign({}, SET_DEFAULT);

function loadSettings() {
  try { Object.assign(SET, JSON.parse(localStorage.getItem(SET_KEY) || "{}")); } catch { /* 隐私模式 */ }
}

function saveSettings() {
  try { localStorage.setItem(SET_KEY, JSON.stringify(SET)); } catch { /* 隐私模式 */ }
}

// 任务栏开关要告诉守护进程（它每轮读 /alive）；相同值不重复发。
let taskbarPrefSent = null;
function syncTaskbarPref() {
  const enabled = !!SET.taskbar;
  if (taskbarPrefSent === enabled) return;
  taskbarPrefSent = enabled;
  fetch("/panel/taskbar", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
    cache: "no-store",
  }).catch(() => { taskbarPrefSent = null; });   // 失败就允许下次重试
}

/** 把设置写进页面：亮度变量 / 播放速度 / 落樱 / 动态壁纸 / 任务栏开关 */
function applySettings() {
  const root = document.documentElement;
  root.style.setProperty("--bg-dim-hiru", (SET.dimHiru / 100).toFixed(2));
  root.style.setProperty("--bg-dim-yoru", (SET.dimYoru / 100).toFixed(2));
  root.style.setProperty("--bg-blur", Math.max(0, SET.blur) + "px");
  root.classList.toggle("bg-blurred", SET.blur > 0);   // 开模糊时把背景层撑出视口，避免四边糊边
  liveRate = Math.min(1.3, Math.max(0.5, SET.rate / 100));
  document.body.classList.toggle("no-petals", !SET.petals);
  document.body.classList.toggle("no-lyrics-zh", !SET.zh);   // 歌词翻译：关掉就不显示小字译文
  if (liveApply) liveApply();
  syncTaskbarPref();                                         // 通知守护进程：任务栏是否隐藏
}

/** 改一项设置：落盘 → 生效 → 刷新设置面板 */
function setSetting(key, value) {
  SET[key] = value;
  saveSettings();
  applySettings();
  renderSettings();
}

function renderSettings() {
  const put = (id, val, text) => {
    const el = $(id);
    if (el) el.value = String(val);
    const out = $(id + "-out");
    if (out) out.textContent = text;
  };
  put("cfg-dim-hiru", SET.dimHiru, SET.dimHiru + "%");
  put("cfg-dim-yoru", SET.dimYoru, SET.dimYoru + "%");
  put("cfg-blur", SET.blur, SET.blur + "px");
  put("cfg-rate", SET.rate, (SET.rate / 100).toFixed(2) + "×");
  [["cfg-live", SET.live], ["cfg-taskbar", SET.taskbar],
   ["cfg-petals", SET.petals], ["cfg-zh", SET.zh]].forEach(([id, on]) => {
    const b = $(id);
    if (!b) return;
    b.classList.toggle("on", !!on);
    b.textContent = on ? "开" : "关";
  });
}

function initSettings() {
  const close = () => { $("cfg").hidden = true; };
  const open = () => { $("cfg").hidden = false; renderSettings(); musicStatus(); };
  $("btn-cfg").addEventListener("click", () => ($("cfg").hidden ? open() : close()));
  $("cfg-close").addEventListener("click", close);
  $("cfg").addEventListener("click", (e) => { if (e.target === $("cfg")) close(); });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("cfg").hidden) close(); });

  $("cfg-dim-hiru").addEventListener("input", (e) => setSetting("dimHiru", Number(e.target.value)));
  $("cfg-dim-yoru").addEventListener("input", (e) => setSetting("dimYoru", Number(e.target.value)));
  $("cfg-blur").addEventListener("input", (e) => setSetting("blur", Number(e.target.value)));
  $("cfg-rate").addEventListener("input", (e) => setSetting("rate", Number(e.target.value)));
  $("cfg-live").addEventListener("click", () => setSetting("live", !SET.live));
  $("cfg-taskbar").addEventListener("click", () => setSetting("taskbar", !SET.taskbar));
  $("cfg-petals").addEventListener("click", () => setSetting("petals", !SET.petals));
  $("cfg-zh").addEventListener("click", () => {
    setSetting("zh", !SET.zh);
    MUSIC.lyrKey = "";                       // 开关变了要重新取一次歌词（tr=0/1 不同）
    ensureLyrics();
  });
  $("cfg-wall").addEventListener("click", () => { close(); openWallpapers(); });
  $("cfg-ck-netease").addEventListener("click", () => pasteCookie("netease"));
  $("cfg-ck-qq").addEventListener("click", () => pasteCookie("qq"));
  $("cfg-reset").addEventListener("click", () => {
    Object.assign(SET, SET_DEFAULT);
    saveSettings();
    applySettings();
    renderSettings();
  });
}

async function initLiveBg() {
  const root = document.documentElement;
  const box = $("live-bg");
  const A = $("live-video"), B = $("live-video2");
  if (!box || !A || !B) return;
  const vids = [A, B];
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  let urls = null;
  try { urls = await api("/live/wallpapers"); } catch { urls = null; }

  let seq = 0;                 // 切换序号：快速反复切时让上一轮流程自己作废
  let active = A;              // 当前显示的那一层（正在播 / 暂停着当背景）
  let curTheme = null;
  let rampTimer = null;
  let fadeTimer = null;

  const themeNow = () => (root.dataset.theme === "yoru" ? "yoru" : "hiru");
  const urlOf = (t) => ((urls && urls[t]) || "");
  const other = (v) => (v === A ? B : A);

  /** 量当前帧的平均亮度（只在判"是不是片头黑场"时才算：**不截图、不编码**） */
  const DARK = 12;                 // 平均亮度低于它就当黑场（0-255）
  const frameMean = (v) => {
    if (!v.videoWidth || v.readyState < 2) return -1;
    try {
      const c = document.createElement("canvas");
      c.width = 64; c.height = 36;
      const ctx = c.getContext("2d");
      ctx.drawImage(v, 0, 0, c.width, c.height);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let sum = 0;
      for (let i = 0; i < d.length; i += 4) sum += (d[i] + d[i + 1] + d[i + 2]) / 3;
      return sum / (d.length / 4);
    } catch { return -1; }
  };

  const seekTo = (v, s) => new Promise((done) => {
    if (!v.seekable || !v.seekable.length || !v.paused) return done();
    if (Math.abs(v.currentTime - s) < 0.2) return done();
    const h = () => { v.removeEventListener("seeked", h); done(); };
    v.addEventListener("seeked", h);
    try { v.currentTime = s; } catch { v.removeEventListener("seeked", h); done(); }
    setTimeout(h, 1500);             // 兜底：seek 没回音也别卡住
  });

  /** 设倍速（带保护：超出浏览器支持范围时别把整个流程炸掉） */
  const setRate = (r) => {
    try { active.playbackRate = r; } catch { /* NotSupportedError：忽略 */ }
  };

  /** 把 playbackRate 从当前值线性推到 to */
  const ramp = (to, ms) => new Promise((done) => {
    clearInterval(rampTimer);
    const from = active.playbackRate;
    const t0 = performance.now();
    rampTimer = setInterval(() => {
      const k = Math.min(1, (performance.now() - t0) / ms);
      setRate(from + (to - from) * k);
      if (k >= 1) { clearInterval(rampTimer); rampTimer = null; done(); }
    }, 40);
  });

  /** 暂停时若停在**片头黑场**（很多动态壁纸开头是淡入黑场），往前挪一点再停。
   *  实在全黑：藏起视频，让主题自带的静态壁纸顶着（任何情况下都不会出现黑底）。 */
  const ensureVisibleFrame = async (t) => {
    const v = active;
    if (v.dataset.src !== urlOf(t)) return;
    const isDark = () => { const m = frameMean(v); return m >= 0 && m < DARK; };
    if (!isDark()) return;
    for (const at of [1.0, 2.5, 4.0]) {
      await seekTo(v, at);
      if (v.dataset.src !== urlOf(t)) return;
      if (!isDark()) return;
    }
    v.classList.remove("ready");
  };

  const play = (t, collapsed, mine) => {
    if (collapsed) {                     // 收起：从静止慢慢加速
      setRate(LIVE_START_RATE);
      active.play().then(() => ramp(liveRate, LIVE_RAMP_IN)).catch(() => {});
    } else {                             // 展开：慢慢减速 → **暂停**（暂停那帧就是静态背景）
      (async () => {
        if (!active.paused) await ramp(LIVE_START_RATE, LIVE_RAMP_OUT);
        if (mine !== seq) return;        // 期间又切了，这一轮作废
        try { active.pause(); } catch { /* 忽略 */ }
        setRate(liveRate);
        ensureVisibleFrame(t);
      })();
    }
  };

  const hideAll = () => {
    root.classList.remove("has-live");
    clearInterval(rampTimer); rampTimer = null;
    clearTimeout(fadeTimer); fadeTimer = null;
    vids.forEach((v) => { try { v.pause(); } catch { /* 忽略 */ } v.classList.remove("ready"); });
    box.style.backgroundImage = "";      // 交回 CSS 里那张主题静态图
  };

  /** 把**另一层**装上新主题的片子；一出第一帧就交叉淡入（旧层在此之前一直可见 → 不露静态底） */
  const swap = (t, url, collapsed, mine) => {
    const next = other(active);
    const prev = active;
    if (next.dataset.src !== url) {
      next.dataset.src = url;
      next.src = url;
      next.classList.remove("ready");
      next.load();
    }
    next.style.zIndex = "2"; prev.style.zIndex = "1";
    const ready = () => {
      if (mine !== seq) return;
      next.classList.add("ready");       // 新层淡入（盖在旧层上）
      active = next; curTheme = t;
      play(t, collapsed, mine);
      clearTimeout(fadeTimer);
      fadeTimer = setTimeout(() => {     // 淡完再撤掉旧层、释放解码
        prev.classList.remove("ready");
        try { prev.pause(); } catch { /* 忽略 */ }
        prev.dataset.src = "";
        prev.removeAttribute("src");
      }, 340);
    };
    if (next.readyState >= 2) ready();
    else {
      next.addEventListener("loadeddata", ready, { once: true });
      setTimeout(() => { if (mine === seq && next.readyState >= 2) ready(); }, 1600);
    }
  };

  const sync = () => {
    const t = themeNow();
    const url = urlOf(t);
    const collapsed = root.dataset.sidebar === "closed";
    const mine = ++seq;

    if (!url || reduce.matches || !SET.live) { hideAll(); return; }
    box.style.backgroundImage = "";
    root.classList.add("has-live");

    if (active.dataset.src === url) {    // 已经是这张：只调播放状态
      if (active.readyState >= 2) active.classList.add("ready");
      play(t, collapsed, mine);
      return;
    }
    swap(t, url, collapsed, mine);
  };

  // 加载失败（文件被删 / Steam 换盘）：撤掉那一层；若它是当前层就退回静态壁纸
  vids.forEach((v) => v.addEventListener("error", () => {
    if (v.dataset.src) { v.dataset.src = ""; v.classList.remove("ready"); }
    if (v === active) root.classList.remove("has-live");
  }));
  new MutationObserver(sync).observe(root,
    { attributes: true, attributeFilter: ["data-theme", "data-sidebar"] });

  const reload = async () => {           // 重拉清单：新订阅的壁纸 / 面板里改选
    try { urls = await api("/live/wallpapers"); } catch { /* 拉不到就继续用旧的 */ }
    sync();
  };
  liveReload = reload;
  liveApply = sync;                      // 设置面板改了（速度/开关）立刻生效
  setInterval(reload, 5 * 60 * 1000);
  sync();
}

/** 静态背景：两层图按主题交叉淡入（换主题只切 opacity，避免大图重解码/光栅化卡顿） */
function initStaticBg() {
  const root = document.documentElement;
  const apply = () => {
    const t = root.dataset.theme === "yoru" ? "yoru" : "hiru";
    document.querySelectorAll("#static-bg .sb").forEach((el) => el.classList.toggle("on", el.dataset.bg === t));
  };
  new MutationObserver(apply).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
  apply();
}

/* ---------------- 动态壁纸选择器（面板里自己挑）----------------
 * 数据全部来自 server.py：/live/list 列可用壁纸、/live/pick 落盘保存（_wallpapers.json）。 */

const WALL = { data: null, q: "" };

async function loadWallpaperList() {
  try { WALL.data = await api("/live/list"); } catch { WALL.data = null; }
  return WALL.data;
}

function renderWallpapers() {
  const d = WALL.data || {};
  const pick = d.pick || {};
  const all = d.items || [];
  const q = (WALL.q || "").trim().toLowerCase();
  const items = all.filter((it) => {
    if (!q) return true;
    return [it.title, it.file, it.id].some((x) => String(x || "").toLowerCase().includes(q));
  });
  const box = $("wp-list");
  const nameOf = (id) => {
    const it = items.find((x) => x.id === id);
    return it ? (it.title || it.id) : id;
  };
  $("wp-cur").textContent = "当前：白昼 " + (pick.hiru ? nameOf(pick.hiru) : "（默认）")
    + "　｜　夜晚 " + (pick.yoru ? nameOf(pick.yoru) : "（默认）")
    + "　｜　共 " + all.length + " 张"
    + (q ? "（筛出 " + items.length + " 张）" : "");
  if (!items.length) {
    box.innerHTML = `<div class="muted" style="padding:10px">没有匹配的壁纸`
      + `（换个筛选，或清空上面的搜索框）。</div>`;
    return;
  }
  box.innerHTML = items.map((it) => {
    const on = (t) => (pick[t] === it.id ? " on-" + t : "");
    const sel = (t) => (pick[t] === it.id ? ' class="sel"' : "");
    // 缩略图优先用壁纸自带的 preview（gif/jpg，稳）；没有才退回 <video> 取第 1 秒那帧
    const thumb = it.preview
      ? `<img class="wp-thumb" src="${esc(it.preview)}" alt="" loading="lazy">`
      : `<video class="wp-thumb" muted preload="metadata" playsinline src="${esc(it.url)}#t=1"></video>`;
    return `<div class="wp-it${on("hiru")}${on("yoru")}">` + thumb
      + `<div class="wp-meta"><b>${esc(it.title || it.id)}</b>`
      + `<i>${it.size_mb} MB · ${esc(String(it.file).slice(0, 26))}</i></div>`
      + `<div class="wp-act">`
      + `<button data-slot="hiru" data-id="${esc(it.id)}"${sel("hiru")}>白昼</button>`
      + `<button data-slot="yoru" data-id="${esc(it.id)}"${sel("yoru")}>夜晚</button>`
      + `</div></div>`;
  }).join("");
}

async function openWallpapers() {
  $("wp").hidden = false;
  renderWallpapers();
  await loadWallpaperList();
  renderWallpapers();
}

async function pickWallpaper(theme, id) {
  const r = await api("/live/pick", { method: "POST", body: JSON.stringify({ theme, id }) });
  if (WALL.data) WALL.data.pick = (r && r.pick) || {};
  renderWallpapers();
  if (liveReload) await liveReload();            // 立刻换片，不用等 5 分钟
}

function initWallpaperPicker() {
  const close = () => { $("wp").hidden = true; };
  // 入口改到设置弹窗里的「选壁纸…」（cfg-wall）；顶栏不再单独放按钮
  $("wp-close").addEventListener("click", close);
  $("wp").addEventListener("click", (e) => { if (e.target === $("wp")) close(); });
  $("wp-q").addEventListener("input", (e) => { WALL.q = e.target.value; renderWallpapers(); });
  $("wp-list").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-slot]");    // ⚠ 别用 data-theme：那会套上主题变量
    if (!b) return;
    pickWallpaper(b.dataset.slot, b.dataset.id)
      .catch((err) => alert("设置壁纸失败：" + err.message));
  });
  $("wp-reset").addEventListener("click", () => {
    Promise.all([pickWallpaper("hiru", null), pickWallpaper("yoru", null)])
      .catch((err) => alert("恢复默认失败：" + err.message));
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("wp").hidden) close();
  });
}

/* ---------------- 面板内搜歌/放歌（网易云 · 非官方接口，走 /music/* 代理）---------------- */

const MUS = { list: [], playing: null, provider: "netease" };

function setMusStatus(t) { /* 兼容旧调用：状态改在设置/结果区显示 */ }

async function pasteCookie(provider) {
  const hint = provider === "qq"
    ? "粘贴 y.qq.com 的【完整】Cookie（含 uin 与 qm_keyst，别截断）："
    : "粘贴 music.163.com 的 Cookie（含 MUSIC_U）：";
  const ck = prompt(hint, "");
  if (ck === null) return;
  try {
    const r = await api("/music/cookie", { method: "POST", body: JSON.stringify({ provider, cookie: ck.trim() }) });
    const keys = (r && r.keys) || [];
    const miss = (r && r.missing) || [];
    alert(miss.length
      ? "已保存，但缺少关键字段：" + miss.join("、") + "；检测到：" + (keys.join("、") || "（没有 key=value，像不是 Cookie）")
      : "已保存；检测到：" + keys.join("、"));
    musicStatus();
  } catch (e) { alert("保存失败：" + (e && e.message ? e.message : e)); }
}

function setProvider(p) {
  MUS.provider = p === "qq" ? "qq" : "netease";
  renderProvider();
  MUS.list = []; MUS.playing = null;
  renderBarResults();
  musicStatus();
}

function renderProvider() {
  document.querySelectorAll(".wp-pb").forEach((b) => b.classList.toggle("on", b.dataset.p === MUS.provider));
}

/** 条内结果列表（紧凑 .it 行；点一下即在面板里播） */
function renderBarResults() {
  const box = $("p-results");
  if (!box) return;
  if (!MUS.list.length) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  box.innerHTML = `<div class="hint">点一条即在面板里播放（`
    + (MUS.provider === "qq" ? "QQ音乐" : "网易云") + `）：</div>`
    + MUS.list.map((it) => `<div class="it" data-play="${esc(it.id)}">`
      + `<b>${esc(it.name)}</b><i>${esc(it.artists)}</i>`
      + `<i>${esc(it.album || "")}</i></div>`).join("");
}

function musTime(s) {
  s = Math.max(0, Math.floor(s || 0));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

async function musicSearch(qRaw) {
  const q = String(qRaw || "").trim();
  if (!q) return;
  const box = $("p-results");
  if (box) { box.hidden = false; box.innerHTML = '<div class="hint">搜索中…</div>'; }
  try {
    const r = await api("/music/search?p=" + MUS.provider + "&q=" + encodeURIComponent(q) + "&limit=20");
    MUS.list = (r && r.songs) || [];
    renderBarResults();
    if (!MUS.list.length && box) box.innerHTML = '<div class="hint">没搜到「' + esc(q) + '」</div>';
  } catch (e) {
    if (box) box.innerHTML = '<div class="hint">搜索失败：' + esc(e && e.message ? e.message : e) + '</div>';
  }
}

async function musicPlay(id) {
  const a = $("mus-audio");
  const box = $("p-results");
  MUS.playing = String(id);
  renderBarResults();
  const it = (MUS.list || []).find((x) => String(x.id) === String(id)) || {};
  const mid2 = it.mediaMid ? "&mid2=" + encodeURIComponent(it.mediaMid) : "";
  if (box) box.innerHTML = '<div class="hint">解析音源…（' + esc(it.name || "") + '）</div>';
  try {
    const r = await api("/music/url?p=" + MUS.provider + "&id=" + encodeURIComponent(id) + mid2);
    if (!r || !r.ok) {
      if (box) box.innerHTML = '<div class="hint">' + esc((r && r.error) || "该曲目无可用音源") + '</div>';
      MUS.playing = null; MUSIC.panel = null;
      renderBarResults(); renderPlayer();
      return;
    }
  } catch (e) {
    if (box) box.innerHTML = '<div class="hint">解析失败：' + esc(e && e.message ? e.message : e) + '</div>';
    MUS.playing = null; renderBarResults();
    return;
  }
  a.src = "/music/stream?p=" + MUS.provider + "&id=" + encodeURIComponent(id) + mid2;
  a.play().catch(() => { /* 由 audio 的 error/ended 处理 */ });
  MUSIC.panel = { name: it.name || "（未知曲目）", artists: it.artists || "", id: MUS.playing };
  if (box) box.hidden = true;                     // 播起来就把结果区收起来
  renderPlayer();
  ensureLyrics();
}

async function musicStatus() {
  try {
    const r = await api("/music/status");
    const p = (r && r.providers) || {};
    const put = (id, st) => { const b = $(id); if (b) b.textContent = (st && st.loggedIn) ? "已登录 · 重贴" : "贴 Cookie"; };
    put("cfg-ck-netease", p.netease);
    put("cfg-ck-qq", p.qq);
  } catch { /* 服务没起来就保持原样 */ }
}

/** 音频事件：播放 / 暂停 / 结束 都刷新播放条与歌词 */
function initMusicAudio() {
  const audio = $("mus-audio");
  ["play", "pause", "ended", "error"].forEach((ev) => audio.addEventListener(ev, () => {
    if (ev === "ended") MUSIC.panel = null;
    renderPlayer();
    syncLyricsVisibility();
  }));
}

/* ---------------- 模型切换（顶栏「模型」按钮）----------------
 * 模型是「会话属性」：POST /api/session/{id}/model { model:{id,providerID,variant?} } → 204。
 * 没选中会话时，选择存成"新会话默认模型"（localStorage），新建会话时带上。
 * 清单来自 GET /api/model（{location,data:[...]}）；variants[] 也一并做成可点的变体按钮。
 * ⚠ 切模型后 GET /api/session/{id} 的 model 会更新，但"列表"更可靠（详情也是 {data:...} 包着的）。 */

const MODEL = { list: null, loading: false };
const MODEL_KEY = "opencode-ui.model";

/* 模型官方图标（本地素材，由 tools/fetch_model_icons.py 下载；OpenCode 接口本身没有图标字段）。
   解析顺序：模型 id → family → family(去掉 -free) → providerID；都没有就用「青铜字母徽章」。
   muse-spark / big-pickle 没有公开品牌标 → 走字母徽章。 */
const MODEL_ICONS = {
  "deepseek": "deepseek", "deepseek-flash": "deepseek", "deepseek-thinking": "deepseek",
  "mimo": "xiaomi", "ling": "antgroup", "nemotron": "nvidia", "nemotron-free": "nvidia",
};
const MODEL_ICON_DIR = "/assets/models/";

/** 把会话里的轻量 model ref 与模型清单里的完整条目合并（拿 name / family） */
function modelFull(ref) {
  if (!ref) return null;
  const hit = (MODEL.list || []).find((m) => m.id === ref.id && m.providerID === ref.providerID);
  return hit ? Object.assign({}, hit, ref) : ref;
}

function iconKeyFor(ref) {
  if (!ref) return "";
  const fam = String(ref.family || "");
  const tries = [ref.id, fam, fam.replace(/-free$/, ""), ref.providerID];
  for (const k of tries) if (k && MODEL_ICONS[k]) return MODEL_ICONS[k];
  return "";
}

/** 图标 HTML：有官方图标用图标；没有就用字母徽章（都不显示模型名，名字在 tooltip / 列表里） */
function modelIconHtml(ref, small) {
  const cls = "micon" + (small ? " sm" : "");
  if (!ref) {   // 还没选模型 → 用龙族校徽占位
    return `<svg class="${cls} micon-tree" aria-hidden="true"><use href="#mk-tree"></use></svg>`;
  }
  const key = iconKeyFor(modelFull(ref));
  if (key) {
    return `<img class="${cls}" src="${MODEL_ICON_DIR}${encodeURIComponent(key)}.svg" alt="" loading="lazy">`;
  }
  const f = modelFull(ref);
  const letter = String((f && (f.name || f.id)) || "?").trim().charAt(0).toUpperCase() || "?";
  return `<span class="${cls} micon-mono" aria-hidden="true">${esc(letter)}</span>`;
}

function defaultModel() {
  try { return JSON.parse(localStorage.getItem(MODEL_KEY) || "null"); } catch { return null; }
}

function saveDefaultModel(ref) {
  try { localStorage.setItem(MODEL_KEY, JSON.stringify(ref)); } catch { /* 隐私模式 */ }
}

/** 当前生效的模型：优先当前会话，其次"新会话默认" */
function currentModel() {
  return (state.current && state.current.model) || defaultModel() || null;
}

const sameModel = (a, b) => !!a && !!b && a.id === b.id && a.providerID === b.providerID;

function modelText(ref, withVariant) {
  if (!ref) return "（跟随 OpenCode 默认）";
  const base = `${ref.providerID}/${ref.id}`;
  return (withVariant && ref.variant && ref.variant !== "default") ? `${base} · ${ref.variant}` : base;
}

function updateModelBtn() {
  const b = $("btn-model");
  if (!b) return;
  const cur = currentModel();
  b.innerHTML = modelIconHtml(cur);          // 用官方图标代替模型名（名字放 tooltip）
  b.title = cur ? `当前模型：${modelText(cur, true)}（点一下切换）` : "切换模型（对当前会话生效）";
}

async function loadModels() {
  if (MODEL.loading) return MODEL.list || [];
  MODEL.loading = true;
  try { MODEL.list = unwrap(await api("/api/model")) || []; }
  catch { MODEL.list = MODEL.list || []; }
  finally { MODEL.loading = false; }
  return MODEL.list;
}

/** 按 provider 分组渲染；标出当前；带 variants 的给一排变体小按钮 */
function renderModels() {
  const box = $("mdl-list");
  if (!box) return;
  const cur = currentModel();
  const q = ($("mdl-q").value || "").trim().toLowerCase();
  const all = MODEL.list || [];
  const items = all.filter((m) => !q || [m.name, m.id, m.providerID, m.family]
    .some((x) => String(x || "").toLowerCase().includes(q)));
  $("mdl-cur").textContent = state.current
    ? "当前会话：" + modelText(state.current.model, true)
    : (cur ? "未选会话 · 新会话默认：" + modelText(cur, true) : "未选会话 · 跟随 OpenCode 默认");
  if (!items.length) {
    box.innerHTML = `<div class="muted" style="padding:10px">`
      + (all.length ? "没有匹配的模型" : "拿不到模型清单") + `</div>`;
    return;
  }
  const byProv = {};
  items.forEach((m) => { (byProv[m.providerID] = byProv[m.providerID] || []).push(m); });
  box.innerHTML = Object.keys(byProv).sort().map((prov) => {
    const rows = byProv[prov].map((m) => {
      const on = sameModel(cur, m);
      const c = (m.cost && m.cost[0]) || {};
      const paid = (c.input || 0) > 0 || (c.output || 0) > 0;
      const meta = [m.id, paid ? `付费 in ${c.input}/out ${c.output}` : "免费",
        (m.capabilities && m.capabilities.tools) ? "工具" : ""].filter(Boolean).join(" · ");
      const vars = (m.variants || []).map((v) => {
        const von = on && (cur.variant || "default") === v.id;
        return `<button type="button" data-id="${esc(m.id)}" data-prov="${esc(m.providerID)}"`
          + ` data-var="${esc(v.id)}"${von ? ' class="sel"' : ""}>${esc(v.id)}</button>`;
      }).join("");
      return `<div class="wp-it mdl-it${on ? " on" : ""}">`
        + `<button type="button" class="mdl-main" data-id="${esc(m.id)}" data-prov="${esc(m.providerID)}">`
        + modelIconHtml(m)
        + `<span class="mdl-txt"><b>${esc(m.name || m.id)}</b><i>${esc(meta)}</i></span>`
        + `</button>`
        + (vars ? `<div class="mdl-vars">${vars}</div>` : "")
        + `</div>`;
    }).join("");
    return `<div class="mdl-group">${esc(prov)}</div>` + rows;
  }).join("");
}

/** 切换：有会话 → 写进会话；没会话 → 存成"新会话默认" */
async function pickModel(ref) {
  const sid = state.current && state.current.id;
  if (!sid) {
    saveDefaultModel(ref);
    setBanner(`已设为新会话默认模型：${modelText(ref, true)}`);
    renderModels();
    updateModelBtn();
    return;
  }
  await api(`/api/session/${encodeURIComponent(sid)}/model`,
    { method: "POST", body: JSON.stringify({ model: ref }) });
  // 服务端在没给 variant 时会填 "default"，这里保持一致
  state.current.model = { id: ref.id, providerID: ref.providerID, variant: ref.variant || "default" };
  const s = state.sessions.find((x) => x.id === sid);
  if (s) s.model = state.current.model;
  renderModels();
  updateModelBtn();
  setBanner(`已切换模型：${modelText(state.current.model, true)}`);
}

async function openModels() {
  $("mdl").hidden = false;
  renderModels();
  const q = $("mdl-q");
  if (q) { q.value = ""; q.focus(); }
  await loadModels();
  renderModels();
}

function initModelPicker() {
  const close = () => { $("mdl").hidden = true; };
  $("btn-model").addEventListener("click", () => ($("mdl").hidden ? openModels() : close()));
  $("mdl-close").addEventListener("click", close);
  $("mdl").addEventListener("click", (e) => { if (e.target === $("mdl")) close(); });
  $("mdl-q").addEventListener("input", renderModels);
  $("mdl-list").addEventListener("click", (e) => {
    // ⚠ 用 data-prov / data-id / data-var；别用 data-theme（会被主题变量规则命中）
    const b = e.target.closest("button[data-id]");
    if (!b) return;
    const ref = { id: b.dataset.id, providerID: b.dataset.prov };
    if (b.dataset.var) ref.variant = b.dataset.var;
    pickModel(ref).then(close).catch((err) => alert("切换模型失败：" + err.message));
  });
  $("mdl-default").addEventListener("click", async () => {
    try {
      const d = unwrap(await api("/api/model/default"));
      if (!d || !d.id) throw new Error("没拿到默认模型");
      await pickModel({ id: d.id, providerID: d.providerID });
      close();
    } catch (err) { alert("用默认模型失败：" + err.message); }
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("mdl").hidden) close();
  });
}

/* ---------------- 事件绑定 / 启动 ---------------- */

$("session-list").addEventListener("click", (e) => {
  const del = e.target.closest(".del");            // 删除按钮：先判断，别让它同时触发"打开会话"
  if (del) {
    e.preventDefault();
    e.stopPropagation();
    const item = del.closest(".session");
    const t = item && item.querySelector(".t");
    deleteSession(del.dataset.del, t ? t.textContent : "");
    return;
  }
  const el = e.target.closest(".session");
  if (el) openSession(el.dataset.id);
});
$("search").addEventListener("input", renderSessions);
$("btn-new").addEventListener("click", newSession);
$("btn-refresh").addEventListener("click", () => { staticSig = null; refreshMessages(); });
$("btn-stop").addEventListener("click", interrupt);
$("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
$("input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  if (e.key === "Escape") {
    if (state.attachments.length) {                      // 先清附件，再考虑中断生成
      state.attachments = [];
      renderAttachRow();
      return;
    }
    if (state.current) interrupt();
  }
});
$("input").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
});

// 跟随策略：默认一路往下跟着最新内容；用户手动往上滚就暂停跟随，滚回底部再恢复
$("messages").addEventListener("wheel", (e) => {
  if (e.deltaY < 0) state.follow = false;
});
$("messages").addEventListener("scroll", () => {
  const b = $("messages");
  if (b.scrollHeight - b.scrollTop - b.clientHeight < 60) state.follow = true;
});

(async function boot() {
  // 版本号：自动取本文件被加载时带的 ?v=（与服务端 /healthz 报告的版本比对，用来触发自动刷新）
  $("ui-ver").textContent = "ui " + (UI_VER || "?");

  loadSettings();                        // 先应用设置（亮度/速度/落樱），再初始化各部件
  applySettings();

  initTheme();
  initPetals();
  initSidebar();
  initBrandName();
  initAttachments();
  initMusic();
  initLiveBg();
  initStaticBg();
  initWallpaperPicker();
  initMusicAudio();
  initSettings();
  initModelPicker();
  initAsk();
  pollAsks();
  loadModels().then(updateModelBtn);      // 预取模型清单：顶栏按钮才能立刻显示官方图标
  startHeartbeat();
  setBanner("正在连接 OpenCode…");
  connectionLoop();
  setInterval(loadSessions, 20000);
})();

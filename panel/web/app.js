/* 芙宁娜控制面板 v2 前端（离线原生 JS，无外部依赖） */
"use strict";

// ================= 基础工具 =================
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const fmtNum = (n) => Number(n || 0).toLocaleString("zh-CN");

async function api(path, { method = "GET", body, raw, headers = {} } = {}) {
  const opt = { method, headers: { "X-Panel-Token": PANEL.token, ...headers } };
  if (raw !== undefined) opt.body = raw;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let res;
  try { res = await fetch(path, opt); } catch (e) { throw new Error("面板服务无响应"); }
  let data = {};
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok || data.ok === false) throw new Error(data.error || `请求失败（${res.status}）`);
  return data;
}
const post = (p, body = {}) => api(p, { method: "POST", body });

const ICONS = {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 01-1 1h-5v-6H9v6H4a1 1 0 01-1-1z"/>',
  features: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  games: '<rect x="2" y="6" width="20" height="13" rx="4"/><path d="M6 12h6M9 9v6M16 10h.01M18 14h.01"/>',
  commands: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M7 9l3 2.5L7 14M11.5 14h5M7 17h10"/>',
  perms: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  logs: '<path d="M4 5h16M4 10h16M4 15h10M4 20h7"/>',
  roles: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0114 0"/><path d="M17 4a4 4 0 010 8M22 21a7 7 0 00-4-6.3"/>',
  limits: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  stats: '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
  about: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
  play: '<path d="M7 4.5v15l13-7.5z" fill="currentColor"/>',
  stop: '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
  loader: '<path d="M12 3a9 9 0 109 9"/>',
  check: '<path d="M5 12l5 5 9-10"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 00-1-1H5a1 1 0 00-1 1v10a1 1 0 001 1h3"/>',
  refresh: '<path d="M20 11a8 8 0 10-2.3 5.7M20 4v7h-7"/>',
  save: '<path d="M5 3h11l5 5v11a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2z"/><path d="M7 3v6h8M7 21v-7h10v7"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  upload: '<path d="M12 16V4M6 10l6-6 6 6M4 20h16"/>',
  folder: '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
  send: '<path d="M4 12l16-8-6 16-3-7z"/>',
  chat: '<path d="M21 12a8 8 0 01-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1121 12z"/>',
  bili: '<rect x="3" y="6" width="18" height="13" rx="3"/><path d="M8 3l3 3M16 3l-3 3M9 11.5v2M15 11.5v2"/>',
};
const icon = (name, cls = "") => `<svg viewBox="0 0 24 24" class="${cls}">${ICONS[name] || ""}</svg>`;

// ================= 宿主窗口（Qt WebChannel；浏览器模式下为空操作） =================
const host = { minimize() {}, close() { window.close(); }, startMove() {}, startResize() {}, toggleMaximize() {} };
if (window.qt && window.QWebChannel) {
  new QWebChannel(qt.webChannelTransport, (ch) => Object.assign(host, ch.objects.host));
}
document.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const r = e.target.closest("[data-resize]");
  if (r) { host.startResize(r.dataset.resize); e.preventDefault(); return; }
  if (e.target.closest("[data-drag]") && !e.target.closest("button")) { host.startMove(); }
});
document.addEventListener("dblclick", (e) => { if (e.target.closest("[data-drag]") && !e.target.closest("button")) host.toggleMaximize(); });
$$("[data-host]").forEach((b) => b.addEventListener("click", () => host[b.dataset.host]()));

// ================= 提示 / 对话框 =================
function toast(text, level = "info", ms = 3800) {
  const el = document.createElement("div");
  el.className = `toast ${level}`;
  el.innerHTML = `<i></i><span>${esc(text)}</span>`;
  $("#toasts").appendChild(el);
  while ($("#toasts").children.length > 4) $("#toasts").firstChild.remove();
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 320); }, ms);
}
const toastErr = (e) => toast(e.message || String(e), "error", 5200);

const modal = {
  open(html, { wide = false, onClose } = {}) {
    const root = $("#modalRoot");
    root.innerHTML = `<div class="modal ${wide ? "wide" : ""}">${html}</div>`;
    root.classList.add("open");
    this._onClose = onClose;
    root.onmousedown = (e) => { if (e.target === root) this.close(); };
    return root.firstElementChild;
  },
  close() {
    const root = $("#modalRoot");
    if (!root.classList.contains("open")) return;
    root.classList.remove("open");
    root.innerHTML = "";
    const cb = this._onClose; this._onClose = null;
    if (cb) cb();
  },
  get isOpen() { return $("#modalRoot").classList.contains("open"); },
};
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { modal.close(); closeMenu(); } });

function confirmBox(title, text, { ok = "确定", danger = false, cancel = "取消", third } = {}) {
  return new Promise((resolve) => {
    const m = modal.open(`
      <div class="modal-head"><h2>${esc(title)}</h2></div>
      <div class="modal-body"><div class="muted" style="white-space:pre-wrap">${esc(text)}</div></div>
      <div class="modal-foot">
        ${third ? `<button class="btn ghost" data-r="third">${esc(third)}</button><span class="grow"></span>` : ""}
        <button class="btn" data-r="no">${esc(cancel)}</button>
        <button class="btn ${danger ? "danger" : "primary"}" data-r="yes">${esc(ok)}</button>
      </div>`, { onClose: () => resolve(null) });
    m.addEventListener("click", (e) => {
      const r = e.target.closest("[data-r]");
      if (!r) return;
      const v = r.dataset.r === "yes" ? true : r.dataset.r === "third" ? "third" : false;
      modal._onClose = null; modal.close(); resolve(v);
    });
  });
}

// ================= 导航 =================
const NAV = [
  { key: "home", label: "主页", c: "#ffd166" },
  { key: "features", label: "功能清单", c: "#3b82f6" },
  { key: "commands", label: "指令大全", c: "#38bdf8" },
  { key: "games", label: "游戏管理", c: "#82d6cf" },
  { key: "bili", label: "B站生活", c: "#fb7299" },
  { key: "perms", label: "功能权限", c: "#3ecf8e" },
  { key: "logs", label: "详情日志", c: "#f59e0b" },
  { key: "roles", label: "角色管理", c: "#ec4899" },
  { key: "limits", label: "回复次数", c: "#a78bfa" },
  { sep: true },
  { key: "settings", label: "设置", c: "#94a3b8" },
  { key: "about", label: "关于", c: "#22d3ee" },
];
const pages = {};           // key -> { mount(el), show(), hide(), leave() -> Promise<bool> }
let current = "home";

function buildRail() {
  $("#railItems").innerHTML = NAV.map((n) => n.sep ? '<div class="rail-sep"></div>' :
    `<button class="rail-item ${n.key === "home" ? "active" : ""}" data-go="${n.key}" style="--c:${n.c}" title="${n.label}">
       <span class="ico">${icon(n.key)}</span><span class="rail-label">${n.label}</span></button>`).join("");
  $("#railToggle").onclick = () => {
    $("#app").classList.toggle("collapsed");
    localStorage.setItem("rail-collapsed", $("#app").classList.contains("collapsed") ? "1" : "");
  };
  if (localStorage.getItem("rail-collapsed")) $("#app").classList.add("collapsed");
}
document.addEventListener("click", (e) => {
  const g = e.target.closest("[data-go]");
  if (g) { go(g.dataset.go, g.dataset.arg); }
});

async function go(key, arg) {
  if (key === current && !arg) return;
  const cur = pages[current];
  if (cur && cur.leave && !(await cur.leave())) return;
  if (cur && cur.hide) cur.hide();
  $$(".page").forEach((p) => p.classList.toggle("active", p.dataset.page === key));
  $$(".rail-item").forEach((b) => b.classList.toggle("active", b.dataset.go === key || (key === "stats" && b.dataset.go === "home")));
  $("#app").classList.toggle("inner", key !== "home");
  current = key;
  bg.pageChanged(key);
  const pg = pages[key];
  if (pg) {
    const el = $(`.page[data-page="${key}"]`);
    if (!pg._mounted) { pg._mounted = true; pg.mount && pg.mount(el); }
    pg.show && pg.show(arg);
  }
}

// ================= 背景 / 壁纸 =================
const bg = {
  defaults: [], index: 1, playlist: [], items: {}, interval: 30, timer: null, pos: 0, front: "A", curKey: "",
  init(backgrounds) {
    this.defaults = backgrounds || [];
    const saved = Number(localStorage.getItem("bg-index") || 0);
    this.index = this.defaults.includes(saved) ? saved : (this.defaults[0] || 0);
    this.renderDots();
  },
  async loadLibrary() {
    try {
      const d = await api("/api/wallpapers");
      this.items = Object.fromEntries(d.items.map((x) => [x.id, x]));
      this.playlist = d.playlist.filter((id) => this.items[id]);
      this.interval = d.interval_seconds;
    } catch (e) { this.playlist = []; }
    this.pos = 0;
    this.apply();
  },
  get custom() { return this.playlist.length > 0; },
  renderDots() {
    const box = $("#bgDots");
    if (this.custom || this.defaults.length < 2) { box.innerHTML = ""; return; }
    box.innerHTML = this.defaults.map((i) => `<button class="bg-dot ${i === this.index ? "on" : ""}" data-bg="${i}" title="背景 ${i}"></button>`).join("");
    box.onclick = (e) => { const b = e.target.closest("[data-bg]"); if (b) this.pick(Number(b.dataset.bg)); };
  },
  pick(i) { this.index = i; localStorage.setItem("bg-index", i); this.renderDots(); this.apply(); },
  apply() {
    clearTimeout(this.timer);
    this.renderDots();
    if (this.custom) {
      const id = this.playlist[this.pos % this.playlist.length];
      this.show(`wp:${id}`, `/media/wp/${id}`, this.items[id].kind);
      $("#bgSplash").classList.remove("show");
      if (this.playlist.length > 1) this.timer = setTimeout(() => { this.pos++; this.apply(); }, this.interval * 1000);
    } else if (this.index) {
      this.show(`bg:${this.index}`, `/media/bg/backdrop/${this.index}`, "image");
      const sp = $("#bgSplash");
      const url = `/media/bg/splash/${this.index}`;
      const img = new Image();
      img.onload = () => { sp.style.backgroundImage = `url("${url}")`; this.pageChanged(current); };
      img.src = url;
    } else {
      this.show("none", "", "none");
    }
  },
  show(key, url, kind) {
    if (key === this.curKey) return;
    this.curKey = key;
    const next = this.front === "A" ? $("#bgB") : $("#bgA");
    const prev = this.front === "A" ? $("#bgA") : $("#bgB");
    const swap = () => {
      next.classList.add("show"); prev.classList.remove("show");
      this.front = this.front === "A" ? "B" : "A";
      setTimeout(() => { if (!prev.classList.contains("show")) { prev.innerHTML = ""; } }, 1000);
    };
    next.innerHTML = ""; next.style.backgroundImage = "";
    if (kind === "video") {
      const v = document.createElement("video");
      Object.assign(v, { src: url, muted: true, loop: true, autoplay: true, playsInline: true });
      v.oncanplay = () => { v.oncanplay = null; swap(); this.updateLogo(null); };
      next.appendChild(v);
      v.play().catch(() => {});
    } else if (kind === "image") {
      const img = new Image();
      img.onload = () => { next.style.backgroundImage = `url("${url}")`; swap(); this.updateLogo(img); };
      img.onerror = () => { if (this.custom) { this.pos++; } };
      img.src = url;
    } else { prev.classList.remove("show"); }
  },
  pageChanged(key) {
    const sp = $("#bgSplash");
    sp.classList.toggle("show", key === "home" && !this.custom && !!this.index && !!sp.style.backgroundImage);
  },
  updateLogo(img) {
    // 左上角偏亮时换深色 Logo
    let bright = false;
    if (img && img.naturalWidth) {
      try {
        const c = document.createElement("canvas"); c.width = 48; c.height = 16;
        const g = c.getContext("2d");
        const sw = img.naturalWidth * 0.22, sh = img.naturalHeight * 0.08;
        g.drawImage(img, 0, 0, sw, sh, 0, 0, 48, 16);
        const d = g.getImageData(0, 0, 48, 16).data;
        let sum = 0;
        for (let i = 0; i < d.length; i += 4) sum += 0.2126 * d[i] + 0.7152 * d[i + 1] + 0.0722 * d[i + 2];
        bright = sum / (d.length / 4) > 170;
      } catch (e) { bright = false; }
    }
    $("#brandLogo").src = bright ? "/media/asset/logo_dark" : "/media/asset/logo";
  },
};

// ================= 状态轮询 / 主页 =================
const S = { overview: null, eventSince: 0, logSink: null };
const RAIL_STATES = {
  stopped: ["机器人未运行", ""], online: ["QQ 在线", "ok"], offline: ["⚠ QQ 离线", "err"],
  logged_out: ["⚠ QQ 未登录", "warn"], mismatch: ["⚠ 账号不一致", "err"], unknown: ["状态待确认", "warn"],
};

function fmtUptime(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  const hms = [h, m, s].map((x) => String(x).padStart(2, "0")).join(":");
  return d ? `${d}天 ${hms}` : hms;
}

function renderOverview(o) {
  S.overview = o;
  const st = o.state, h = st.health || {};
  const running = st.running || st.nonebot;
  const key = !running && !st.napcat ? "stopped" : (h.state || "unknown");
  const [txt, cls] = RAIL_STATES[key] || RAIL_STATES.unknown;
  const rs = $("#railStatus");
  rs.className = `rail-status ${cls}`;
  $("#railStatusText").textContent = o.busy ? `正在${o.busy_label}…` : txt;
  rs.title = h.detail || "打开登录助手";

  $("#homeName").textContent = o.nickname || "芙宁娜";
  $("#homeSub").textContent = o.bot_qq ? `机器人 QQ ${o.bot_qq}${h.detail && running ? " · " + h.detail : ""}` : "还没有设置机器人 QQ —— 设置 → 账号与角色";
  const statusText = o.busy ? `${o.busy_label}中` : !running ? "休息中" : h.state === "online" ? "在线" : h.state === "logged_out" ? "待登录" : h.state === "offline" ? "离线" : h.state === "mismatch" ? "账号不符" : "启动中";
  $("#cStatus").textContent = statusText;
  $("#cStatusHint").textContent = !running ? "点击右下角启动" : h.state === "online" ? "一切正常" : "点击查看登录助手";
  $("#cGroups").textContent = o.groups == null ? "—" : `${o.groups} 个`;
  const models = Object.entries(o.models || {});
  const mTotal = models.reduce((a, [, v]) => a + v, 0);
  $("#cModels").textContent = fmtNum(mTotal);
  $("#cModelsHint").textContent = models.length ? models.map(([k, v]) => `${k} ${v}`).join(" · ") : "今日暂无调用";
  $("#cChat").textContent = fmtNum(o.today_chat);
  if (st.started_at && running) {
    S.startedOffset = st.started_at - o.now + Date.now() / 1000;
  } else S.startedOffset = null;
  tickUptime();

  const btn = $("#startBtn");
  btn.classList.toggle("running", !!running && !o.busy);
  btn.classList.toggle("busy", !!o.busy);
  $("#startIcon").innerHTML = o.busy ? icon("loader", "spin") : running ? icon("stop") : icon("play");
  $("#startText").textContent = o.busy ? `正在${o.busy_label}` : running ? "停止机器人" : "启动机器人";
  const note = st.port_conflict ? "⚠ 机器人端口被其它程序占用（旧版机器人可能还在运行）" : "";
  $("#launchNote").textContent = note;
  if (!bg.defaults.length && o.backgrounds) { bg.init(o.backgrounds); bg.apply(); }
}
function tickUptime() {
  $("#uptime").textContent = S.startedOffset ? fmtUptime(Date.now() / 1000 - S.startedOffset) : "--:--:--";
}
setInterval(tickUptime, 1000);

async function pollOverview() {
  try { renderOverview(await api("/api/overview")); } catch (e) { /* 忽略一次失败 */ }
}
async function pollEvents() {
  try {
    const d = await api(`/api/events?since=${S.eventSince}`);
    for (const ev of d.events) {
      S.eventSince = Math.max(S.eventSince, ev.id);
      if (ev.toast) toast(ev.text.replace(/^\[[^\]]+\]\s*/, ""), ev.level === "info" ? "info" : ev.level);
      if (S.logSink) S.logSink(ev);
      S.recentEvents = (S.recentEvents || []).concat(ev).slice(-200);
    }
  } catch (e) { /* ignore */ }
}
function schedule(fn, ms) { (async function loop() { await fn(); setTimeout(loop, document.hidden ? ms * 3 : ms); })(); }

// 启动 / 停止
$("#startBtn").onclick = async () => {
  const o = S.overview;
  if (!o || o.busy) return;
  const running = o.state.running || o.state.nonebot;
  if (running) {
    const ok = await confirmBox("停止机器人", "会停止 NoneBot 与机器人用的 NapCat/QQ。\n你自己开的 QQ 不受影响。", { ok: "停止", danger: true });
    if (!ok) return;
  }
  try { await post(`/api/control/${running ? "stop" : "start"}`); pollOverview(); } catch (e) { toastErr(e); }
};
function closeMenu() { $("#startMenu").classList.remove("open"); }
$("#startMore").onclick = (e) => { e.stopPropagation(); $("#startMenu").classList.toggle("open"); };
document.addEventListener("click", (e) => { if (!e.target.closest("#startMenu")) closeMenu(); });
$("#startMenu").onclick = async (e) => {
  const b = e.target.closest("[data-ctl]"); if (!b) return;
  closeMenu();
  const a = b.dataset.ctl;
  if (a === "login") return openLogin();
  try {
    if (a === "test") {
      const r = await post("/api/control/test");
      return confirmBox(r.headline, r.detail, { ok: "知道了", cancel: "关闭" });
    }
    if (a === "restart_all" && !(await confirmBox("完全重启", "会同时重启 NapCat（机器人用的 QQ）。一般只有换号或 QQ 卡住时才需要。", { ok: "重启" }))) return;
    await post(`/api/control/${a}`);
    pollOverview();
  } catch (err) { toastErr(err); }
};

// 主页卡片与底部按钮
document.addEventListener("click", async (e) => {
  const c = e.target.closest("[data-card]");
  if (c) {
    const k = c.dataset.card;
    if (k === "status") return (S.overview && (S.overview.state.running || S.overview.state.nonebot)) ? openLogin() : $("#startBtn").click();
    if (k === "groups") return go("perms");
    if (k === "models") return showModels();
    if (k === "chat") return go("stats");
  }
  const o = e.target.closest("[data-open]");
  if (o) { try { await post(`/api/open/${o.dataset.open}`); } catch (err) { toastErr(err); } }
  const lg = e.target.closest("[data-login]");
  if (lg) loginSite(lg.dataset.login);
  const a = e.target.closest("[data-action]");
  if (a && a.dataset.action === "paths") showPaths();
  if (a && a.dataset.action === "help") showHelp();
});

async function loginSite(site) {
  const name = site === "google" ? "Google" : "X";
  try {
    let r = await api(`/api/open/${site}`, { method: "POST", body: {} }).catch((e) => e);
    if (r instanceof Error) {
      if (!/停止机器人/.test(r.message)) throw r;
      const ok = await confirmBox(`登录 ${name} 账号`, `搜图浏览器正被机器人使用，需要先停止机器人（只停 NoneBot，QQ 登录保留）。\n在打开的 Edge 里登录 ${name}，关闭浏览器后回到主页点「启动机器人」。`, { ok: "停止并打开" });
      if (!ok) return;
      await post(`/api/open/${site}`, { stop_bot: true });
    }
    toast(`已打开 Edge，请登录 ${name}；完成后关闭浏览器${r instanceof Error ? "并启动机器人" : ""}`, "success", 6000);
  } catch (e) { toastErr(e); }
}

function showModels() {
  const m = Object.entries((S.overview && S.overview.models) || {});
  modal.open(`<div class="modal-head"><h2>今日模型调用</h2></div>
    <div class="modal-body">${m.length ? `<div class="dist">${m.map(([k, v]) => `<div class="dist-row" style="grid-template-columns:1fr 60px"><span class="mono">${esc(k)}</span><b style="text-align:right">${fmtNum(v)}</b></div>`).join("")}</div>` : '<div class="empty">今天还没有调用模型</div>'}</div>
    <div class="modal-foot"><button class="btn" onclick="modal.close()">关闭</button></div>`);
}
async function showPaths() {
  try {
    const d = await api("/api/paths");
    const m = modal.open(`<div class="modal-head"><h2>路径</h2></div>
      <div class="modal-body"><div class="list">${d.items.map((x) => `
        <div class="list-item" data-folder="${x.key}"><span class="letter">${icon("folder")}</span>
          <div class="li-main"><div class="li-title">${esc(x.label)}</div><div class="li-sub mono selectable">${esc(x.path)}</div></div>
          <span class="btn sm">打开</span></div>`).join("")}</div></div>
      <div class="modal-foot"><button class="btn" onclick="modal.close()">关闭</button></div>`, { wide: true });
    m.onclick = async (e) => { const f = e.target.closest("[data-folder]"); if (f) { try { await post("/api/open/folder", { key: f.dataset.folder }); } catch (err) { toastErr(err); } } };
  } catch (e) { toastErr(e); }
}
function showHelp() {
  modal.open(`<div class="modal-head"><h2>使用帮助</h2></div>
    <div class="modal-body"><ol class="steps" style="margin-left:18px;color:var(--text)">
      <li>右下角金色按钮启动机器人；已登录过的 QQ 会自动快速登录。</li>
      <li>左下角状态显示 QQ 是否在线，点击打开「登录助手」查看二维码与处理办法。</li>
      <li>「功能权限」按群开关各项功能，保存后机器人立即生效。</li>
      <li>「回复次数」可改统一上限或给某位群友单独设置；「角色管理」编辑人格与共享设定。</li>
      <li>「设置」里管理账号、AI 模型、Pixiv 推送、定时提醒与壁纸。</li>
      <li>右下角 ≡ 菜单可只重启 NoneBot（不掉 QQ 登录）或检查连接状态。</li>
    </ol></div>
    <div class="modal-foot"><button class="btn primary" onclick="modal.close()">知道了</button></div>`);
}

// ================= 登录助手 =================
const LOGIN_TEXT = {
  restarting: ["正在重启 NapCat…", "请稍候，新的二维码生成后会自动显示。"],
  online: ["QQ 已在线", "机器人账号已登录，一切正常。"],
  no_napcat: ["NapCat 未运行", "点「启动」会先启动 NapCat，已登录过的账号会自动快速登录。"],
  offline: ["QQ 已离线", "通常是在别的设备登录或被风控。点「重启 NapCat」重新扫码即可。"],
  mismatch: ["账号不一致", "NapCat 登录的不是配置的机器人账号。请在 WebUI 退出后用正确账号扫码，或到「设置 → 账号」切换。"],
  waiting: ["等待扫码登录", "用手机 QQ 扫描二维码，扫码账号必须是机器人账号。"],
};
let loginTimer = null;
function openLogin() {
  const m = modal.open(`<div class="modal-head"><h2>登录助手</h2><span class="tag" id="lgQQ"></span></div>
    <div class="modal-body"><div class="row" style="align-items:flex-start;gap:18px">
      <div class="qr" id="lgQr">暂无二维码</div>
      <div class="grow"><div class="login-state" id="lgState">检查中…</div><div class="muted" id="lgText" style="margin-top:6px"></div>
      <ol class="steps"><li>手机 QQ 扫码时，确认是机器人账号</li><li>二维码约 2 分钟过期，过期后点「重启 NapCat」或在 WebUI 重新获取</li><li>扫码成功后状态会自动变为「QQ 在线」</li></ol>
      <div id="lgWarn"></div></div></div></div>
    <div class="modal-foot"><button class="btn" data-l="webui">打开 WebUI</button><span class="grow"></span>
      <button class="btn" data-l="restart">重启 NapCat</button><button class="btn primary" data-l="start">启动</button></div>`,
    { wide: true, onClose: () => clearTimeout(loginTimer) });
  m.onclick = async (e) => {
    const b = e.target.closest("[data-l]"); if (!b) return;
    try {
      if (b.dataset.l === "webui") await post("/api/open/webui");
      if (b.dataset.l === "start") { await post("/api/control/start"); toast("正在启动…"); }
      if (b.dataset.l === "restart") {
        modal._onClose = null; clearTimeout(loginTimer);
        const ok = await confirmBox("重启 NapCat", "只重启机器人用的 NapCat/QQ 并生成新二维码，你自己开的 QQ 不受影响。\n重启期间机器人会短暂离线。", { ok: "重启" });
        if (ok) await post("/api/control/restart_napcat");
        return openLogin();
      }
    } catch (err) { toastErr(err); }
  };
  (async function loop() {
    if (!modal.isOpen || !$("#lgState")) return;
    try {
      const d = await api("/api/login");
      const [t, tx] = LOGIN_TEXT[d.state] || LOGIN_TEXT.waiting;
      $("#lgState").textContent = t;
      $("#lgText").textContent = d.state === "online" ? tx : (d.detail && d.state !== "no_napcat" ? d.detail + "。" : "") + tx;
      $("#lgQQ").textContent = d.qq ? `机器人 QQ ${d.qq}` : "未设置机器人 QQ";
      const qr = $("#lgQr");
      if (d.qr && d.state !== "online") { qr.style.backgroundImage = `url("/media/qrcode?t=${Math.floor(Date.now() / 4000)}")`; qr.textContent = ""; qr.title = `约 ${d.qr_left} 秒后过期`; }
      else { qr.style.backgroundImage = ""; qr.textContent = d.state === "online" ? "✓ 已登录" : "暂无二维码"; }
      $("#lgWarn").innerHTML = d.warning ? `<div class="warn-box">${esc(d.warning)}</div>` : "";
    } catch (e) { /* ignore */ }
    loginTimer = setTimeout(loop, 3000);
  })();
}
$("#railStatus").onclick = openLogin;

// ================= 功能清单 =================
const FEATURE_GROUPS = [
  { name: "AI 智能", c: "#ffd166", items: [
    ["AI 聊天", "群聊接话 · 艾特回复 · 情绪、好感与记忆系统", "聊天"],
    ["看图聊天", "发图或引用图片追问，结合最近的图片理解上下文", "视觉"],
    ["群聊总结", "引用消息或自然语言触发，汇总最近群聊", "总结"],
    ["模糊指令", "艾特 / 发图 / 精华 / 搜图 / 强化，自然语言识别", "智能"],
    ["表情回应", "偶尔给群消息贴表情；被艾特只说谢谢、晚安时贴个表情代替回复", "互动"],
    ["记得近况", "记住群友说过的考试、面试、生病等，事后顺口问一句结果", "记忆"],
    ["冷场开话题", "群里安静太久时主动冒个泡（一两条，像随手发的）· 按群在「功能权限 → 冷场主动开话题」开启（默认关）", "主动"],
    ["惦记常聊的人", "常找她聊的人好久没出现，她会在群里艾特 TA 随口说一两句；连着几次没回就不再找，等 TA 自己出现 · 按群在「功能权限 → 惦记常聊的人」开关（默认开），设置 → 活人感", "主动"],
    ["收图筛选", "打开的群里群友发的图（含合并转发里的图，表情包跳过）和搜图原图、Pixiv 点图和榜单推送的图自动收进筛图库；小于 1MB 的在电脑空闲时自动强化；在面板「筛图」页（单张左右滑，或总览多选、拖进垃圾桶）或手机 App 里筛，留下的自动进 Eagle · 按群在「功能权限 → 收图筛选」开启（默认关），设置 → 搜图与强化", "图片"],
    ["一句一条", "像真人用手机聊天：一条消息一个意思，想多说就连发几条，不再限制总字数和条数 · 设置 → 活人感", "活人感"],
    ["活着的痕迹", "记得自己「睡」了多久（离线后醒来被问会说睡着了）、哪几句没发出去；隔了很久才回来的人她会察觉 · 设置 → 活人感", "活人感"],
    ["有自己的生活", "知道自己此刻在干嘛、最近在 B 站刷到了什么，会参考群友的说话方式，聊天时自然提起", "活人感"],
    ["热梗与群梗", "看懂群友说的网络热梗（内置 100+ 常用梗 + 刷 B 站学会的新梗），会跟着用本群大家常说的梗，偶尔知道今天的 B 站热搜", "活人感"],
    ["去 AI 味", "删掉「希望对你有帮助」这类客服腔；自称 AI、用 markdown 标题加粗时让她重说 · 设置 → 活人感", "活人感"],
    ["语音消息", "用芙宁娜的声音（本机 GPT-SoVITS）发语音：神气 / 日常 / 傲娇 / 平静四种语气按情境自动换；只在要用时开引擎，打游戏或内存紧张时自动让路 · 按群在「功能权限 → 语音消息」开关", "语音"],
    ["联网搜索", "不知道、拿不准或要最新消息时悄悄上网查（必应 + DuckDuckGo，自动过滤不相关结果），看完像本来就知道一样说；群友说「搜一下 XX」也会查 · 设置 → 联网搜索，按群在「功能权限 → 联网搜索」开关（默认开）", "搜索"],
    ["说话手感", "参考社区大肥鱼：句中停顿、结巴、改口、先蹦反应字，认真时收起玩笑，记得自己贴过的表情、戳过谁 · 设置 → 活人感", "活人感"],
  ] },
  { name: "B站生活", c: "#fb7299", items: [
    ["自己刷 B 站", "用小号定时刷推荐/搜索/热门，读简介、字幕、弹幕和热评，记下自己的感受 · 左侧「B站生活」能实时看她在刷哪条、叫她去刷", "冲浪"],
    ["点赞 / 弹幕 / 评论 / 动态", "做不做、说什么全看她自己的意愿，默认不限次数（设置 → B站生活 可加上限或关掉）；遇到风控立刻暂停", "互动"],
    ["学新梗", "从弹幕和热评里学会新梗，聊天时就能看懂、也会用", "梗"],
    ["B站刷到分享", "刷到特别好玩的视频分享到群里 · 按群在「功能权限 → B站刷到分享」开关（默认开）", "分享"],
  ] },
  { name: "图像处理", c: "#b48cf2", items: [
    ["以图搜图", "Google 相似图检索，引用图片即可触发", "搜索"],
    ["图片强化", "高清重绘与放大，单张 / 批量共享队列", "图像"],
    ["批量强化", "引用合并转发记录，一次强化多张", "图像"],
    ["指令发图", "发 N 张图，从图库随机发送", "图库"],
  ] },
  { name: "群聊与推送", c: "#6ad4ff", items: [
    ["群主发图转发", "源群图片自动转发到分园", "转发"],
    ["米游社签到", "GsCore 官方扫码与四游签到 · 各游戏订阅、账号和时间在官方控制台管理", "签到"],
    ["米游社资讯", "原神 / 星铁 / 崩坏3 / 绝区零 官方论坛新帖与前瞻兑换码自动推送", "资讯"],
    ["Pixiv 插画", "日/周/月榜前 150 名分批发送 · 角色/游戏点图", "画廊"],
    ["Pixiv ID 取图", "艾特机器人发「pid作品ID」，直接把那部作品的图发出来", "点图"],
    ["JM 下载", "群里发「/jm 车号」，下载整本并合成 PDF 发群文件", "下载"],
    ["定时提醒", "一次性 / 每天 / 每周 · 自然语言 · 离线 30 分钟内补发", "提醒"],
    ["封神榜", "每周三 / 日群聊趣味封神统计", "统计"],
    ["成员同步", "自动同步群成员与群名，供次数管理与统计使用", "同步"],
  ] },
];
pages.features = {
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>功能清单</h1><span class="sub">芙宁娜目前会做的事；每个群可在「功能权限」里单独开关</span></div>
      <div class="page-body scroll"><div class="feat-grid">${FEATURE_GROUPS.map((g) => `
        <div class="panel panel-pad feat-group" style="--c:${g.c}"><h3>${g.name}</h3>
          ${g.items.map(([n, d, t]) => `<div class="feat"><div class="grow"><div class="f-name">${n}</div><div class="f-desc">${d}</div></div><span class="tag">${t}</span></div>`).join("")}
        </div>`).join("")}</div></div>`;
  },
};

// ================= 指令大全 =================
const COMMAND_GROUPS = [
  { cat: "米游社账号", name: "米游社账号 · 扫码绑定与自动签到", c: "#38bdf8", hint: "所有指令都要先 @芙芙，不 @ 不会响应", items: [
    ["扫码绑定全部账号", "群里发米哈游通行证二维码，扫码后自动撤回；一次绑定通行证下原神/星铁/绝区零/崩坏3 全部账号，并开启每日自动签到", "需 @机器人", ["@芙芙 绑定账号"]],
    ["查看已绑定账号", "一张卡片列出全部游戏账号的昵称、UID、等级、服务器与签到状态", "需 @机器人", ["@芙芙 我的账号"]],
    ["指令帮助卡片", "发送一张全功能指令卡片（不含 JM）", "需 @机器人", ["@芙芙 指令帮助"]],
    ["更新展柜", "刷新全部游戏的角色面板数据；加游戏前缀只刷新单个游戏", "需绑定", ["@芙芙 更新展柜", "@芙芙 ys更新展柜"]],
    ["自动签到与战报", "每天 10:00 自动签到，10:05 在开启「米游社签到」权限的群发签到战报；也可以手动补签", "需绑定", ["@芙芙 米游社签到", "@芙芙 米游社战报"]],
    ["主人专用：官方完整指令", "四个官方插件的原始指令只对主人开放（不用 @），例如官方帮助图、抽卡记录", "仅主人", ["ys帮助", "bt帮助", "zzz帮助", "bbb帮助"]],
  ] },
  { cat: "原神", name: "原神 · GenshinUID", c: "#f2b544", hint: "前缀 ys 或 原神，先 @芙芙 再说指令", items: [
    ["体力与每日", "树脂、每日委托、探索派遣、参量质变仪等实时便笺", "需绑定", ["@芙芙 ys每日"]],
    ["角色面板", "查询自己账号里的角色面板，支持常用别名；展柜里没有会自动刷新一次", "需绑定", ["@芙芙 ys芙宁娜", "@芙芙 ys六命芙宁娜"]],
    ["一图流攻略", "角色养成一图流攻略", "无需绑定", ["@芙芙 ys芙宁娜攻略"]],
    ["深渊全套战绩", "一次发出深境螺旋、幻想真境剧诗、幽境危战", "需绑定", ["@芙芙 ys深渊"]],
    ["今日素材", "今天能刷的天赋书与武器突破材料", "无需绑定", ["@芙芙 ys今日素材"]],
  ] },
  { cat: "星铁", name: "崩坏：星穹铁道 · StarRailUID", c: "#8b7cf6", hint: "前缀 bt、星铁 或 崩铁，先 @芙芙 再说指令", items: [
    ["开拓力与每日", "开拓力、每日实训、委托派遣等实时便笺", "需绑定", ["@芙芙 bt每日"]],
    ["角色面板", "查询自己账号里的角色面板，支持常用别名", "需绑定", ["@芙芙 bt黄泉", "@芙芙 bt流萤"]],
    ["一图流攻略", "角色养成一图流攻略", "无需绑定", ["@芙芙 bt黄泉攻略"]],
    ["深渊全套战绩", "一次发出忘却之庭、虚构叙事、末日幻影、异相仲裁、货币战争", "需绑定", ["@芙芙 bt深渊"]],
  ] },
  { cat: "绝区零", name: "绝区零 · ZZZeroUID", c: "#f97316", hint: "前缀 zzz 或 绝区零，先 @芙芙 再说指令", items: [
    ["电量与每日", "电量、活跃度、录像店经营、刮刮卡等实时便笺", "需绑定", ["@芙芙 zzz每日"]],
    ["角色面板", "查询自己账号里的代理人面板，支持常用别名", "需绑定", ["@芙芙 zzz星见雅"]],
    ["一图流攻略", "代理人养成一图流攻略", "无需绑定", ["@芙芙 zzz星见雅攻略"]],
    ["深渊全套战绩", "一次发出式舆防卫战、危局强袭战、临界推演、零号空洞", "需绑定", ["@芙芙 zzz深渊"]],
  ] },
  { cat: "崩坏3", name: "崩坏3 · BBBUID", c: "#ec4899", hint: "前缀 bbb、崩坏3 或 崩三，先 @芙芙 再说指令", items: [
    ["体力与每日", "体力、每日任务等实时便笺", "需绑定", ["@芙芙 bbb每日"]],
    ["女武神总览", "账号总览与女武神练度（崩坏3 没有单角色面板）", "需绑定", ["@芙芙 bbb查询"]],
    ["往世乐土攻略", "按女武神查询往世乐土攻略", "无需绑定", ["@芙芙 bbb人律攻略"]],
    ["深渊全套战绩", "一次发出超弦空间/量子流形、记忆战场、往世乐土", "需绑定", ["@芙芙 bbb深渊"]],
  ] },
  { cat: "AI 聊天", name: "AI 聊天 · 视觉看图 · 群聊总结", c: "#ffd166", hint: "支持当前角色昵称唤醒或直接 @机器人", items: [
    ["日常艾特对话", "艾特机器人或在句子里带上角色称呼即可对话，自带好感度、心情与上下文记忆", "需 @ 或称呼", ["@芙芙 今天吃什么好", "芙芙 在吗"]],
    ["看图聊天与追问", "发送图片同时艾特机器人，或引用群内图片艾特提问；也可对最近 5 分钟的图直接问「这谁/这是啥」", "带图/引用 + @", ["[引用图片] @芙芙 这张图里是谁"]],
    ["群聊智能总结", "自动梳理并吐槽群内最近 100 条聊天记录的重点话题与群友名场面（同群冷却 120 秒）", "需 @机器人", ["@芙芙 总结", "@芙芙 总结一下"]],
    ["双击头像戳一戳", "在群内双击机器人头像触发「戳一戳」，随机掉落专属性格台词回应", "双击头像", ["双击机器人头像"]],
  ] },
  { cat: "搜图强化", name: "以图搜图 · AI 高清强化", c: "#b48cf2", hint: "先引用目标图片或合并转发，再 @机器人", items: [
    ["以图搜图找原图", "引用一张图片并艾特说「搜图」，自动检索推特(X)/Pixiv/Danbooru 画师出处并打包发回原画质大图", "引用图片 + @", ["[引用图片] @芙芙 搜图", "[引用图片] @芙芙 找原图"]],
    ["单张图片高清强化", "引用一张图片并艾特说「强化」或「超分」，自动进行 AI 降噪重绘与超分辨率放大", "引用图片 + @", ["[引用图片] @芙芙 强化", "[引用图片] @芙芙 超分"]],
    ["合并转发批量强化", "引用一条含多张图片的合并转发聊天记录并艾特说「强化」，按队列批量放大全部图片", "引用转发 + @", ["[引用合并转发] @芙芙 强化"]],
  ] },
  { cat: "插画图库", name: "Pixiv 点图 · PID 取图 · 本地图库", c: "#ec4899", hint: "需 @机器人 触发，可在功能权限中按群开关", items: [
    ["Pixiv 角色/作品点图", "指定角色名或作品名点图（单次 1~10 张），自动从 Pixiv 筛选高收藏插画发送", "需 @机器人", ["@芙芙 来张芙宁娜的图", "@芙芙 来3张星穹铁道插画"]],
    ["Pixiv 作品 ID 取图", "直接输入 Pixiv 作品数字 ID（PID），精准提取原图（多页插画自动打包合并转发）", "需 @机器人", ["@芙芙 pid113456789", "@芙芙 p站id 113456789"]],
    ["本地图库随机发图", "不带具体角色名时，从本地 Eagle 精选图库随机抽取 1~10 张美图或表情包发送", "需 @机器人", ["@芙芙 来两张图", "@芙芙 发张壁纸"]],
  ] },
  { cat: "JM 漫画", name: "JM 漫画整本下载", c: "#f59e0b", hint: "群聊直接发送或 @机器人 均可触发", items: [
    ["车号下载合成 PDF", "输入车号即可下载整本漫画并自动合成为 PDF 上传至 QQ 群文件（已下过的本子自动秒发缓存）", "直接发或 @", ["/jm 350234", "jm 350234"]],
  ] },
  { cat: "定时提醒", name: "群内个人定时提醒", c: "#14b8a6", hint: "需 @机器人 触发，创建前会先列出方案供确认", items: [
    ["创建一次性提醒", "用自然语言说出提醒时间与事项，机器人生成方案后回复「确认提醒」即可生效", "需 @机器人", ["@芙芙 20分钟后提醒我收菜", "@芙芙 明晚8点叫我打周本"]],
    ["创建周期重复提醒", "支持每天、每周、每月固定时间循环提醒；机器人短暂离线 30 分钟内会自动补发", "需 @机器人", ["@芙芙 每天上午9点提醒我喝水", "@芙芙 每周日15点提醒我周常"]],
    ["确认 / 查看 / 取消提醒", "确认刚才的提醒方案、查看自己在本群的生效提醒列表，或按编号取消提醒", "需 @机器人", ["@芙芙 确认提醒", "@芙芙 我的提醒", "@芙芙 取消提醒 1"]],
  ] },
  { cat: "群管设定", name: "群管辅助 · 记忆设定 · 定时榜单", c: "#22d3ee", hint: "日常群务管理与人设记忆互动", items: [
    ["引用消息设为群精华", "引用群友的精彩发言并艾特说「设精华」，即可加入群精华消息（需机器人为群管理员）", "引用消息 + @", ["[引用消息] @芙芙 设精华", "[引用消息] @芙芙 加精"]],
    ["代艾特群友传话", "按群名片或昵称匹配群成员并帮你艾特对方传话", "需 @机器人", ["@芙芙 艾特张三 出来玩"]],
    ["专属称呼与记忆设定", "用自然语言告诉机器人你的外号、喜好或群内设定，机器人会自动写入长期记忆", "需 @机器人", ["@芙芙 以后叫我旅行者", "@芙芙 记住我喜欢喝冰美式"]],
    ["自动资讯与封神榜", "开启对应权限后自动运行：米游社官方新帖与前瞻兑换码推送、Pixiv 日周月榜推送、每周三/日 21:30 封神榜", "后台自动定时", ["无需指令（自动推送）"]],
  ] },
  { cat: "B站生活", name: "B站生活 · 小号扫码与刷视频", c: "#fb7299", hint: "只有主人能用；群里先 @芙芙，私聊直接发", items: [
    ["扫码登录小号", "发 B 站登录二维码，用小号扫码，成功后自动撤回二维码", "主人 · 需 @机器人", ["@芙芙 B站登录"]],
    ["查看状态", "登录账号、今天点赞/弹幕/评论/动态次数、最近刷到的视频和感受（面板「B站生活」页更详细）", "主人 · 需 @机器人", ["@芙芙 B站状态"]],
    ["立刻刷一轮 / 发动态", "马上去刷一轮或按今天的经历想条动态（面板「B站生活」页也有按钮）", "主人 · 需 @机器人", ["@芙芙 B站刷一下", "@芙芙 B站发动态"]],
    ["暂停 / 恢复 / 退出", "暂停自动刷 B 站、恢复（也用于风控暂停后提前恢复）、删除小号登录信息", "主人 · 需 @机器人", ["@芙芙 B站暂停", "@芙芙 B站恢复", "@芙芙 B站退出登录"]],
  ] },
];

pages.commands = {
  cat: "全部", q: "",
  mount(el) {
    const cats = ["全部", ...COMMAND_GROUPS.map((g) => g.cat)];
    el.innerHTML = `<div class="page-head"><h1>指令大全</h1><span class="sub">群聊全功能触发口令速查手册 · 点击右侧口令胶囊即可一键复制</span><span class="grow"></span><span class="tag" id="cmdCountTag"></span></div>
      <div class="panel panel-pad" style="margin-bottom:12px;display:flex;flex-direction:column;gap:10px">
        <input class="input" id="cmdSearch" placeholder="搜索指令、功能名称或关键词（例如：签到、深渊、体力、搜图、强化、pid、提醒、jm、精华）…">
        <div class="row" style="flex-wrap:wrap;gap:6px" id="cmdCats">${cats.map((c, i) => `<button class="btn sm ${i === 0 ? "primary" : ""}" data-cmd-cat="${esc(c)}">${esc(c)}</button>`).join("")}</div>
      </div>
      <div class="page-body scroll"><div class="feat-grid" id="cmdGrid"></div></div>`;
    $("#cmdSearch").oninput = (e) => { this.q = e.target.value.trim().toLowerCase(); this.render(); };
    $("#cmdCats").onclick = (e) => {
      const b = e.target.closest("[data-cmd-cat]"); if (!b) return;
      this.cat = b.dataset.cmdCat;
      $$("#cmdCats button").forEach((x) => x.classList.toggle("primary", x === b));
      this.render();
    };
    $("#cmdGrid").onclick = async (e) => {
      const b = e.target.closest("[data-copy-cmd]"); if (!b) return;
      const raw = b.dataset.copyCmd.replace(/^\[(?:引用图片|引用合并转发|引用消息)\]\s*/, "");
      try {
        await navigator.clipboard.writeText(raw);
        toast(`已复制指令：${raw}`, "success", 2200);
      } catch (err) { toast(`指令：${raw}`, "info", 3000); }
    };
    this.render();
  },
  render() {
    const total = COMMAND_GROUPS.reduce((s, g) => s + g.items.length, 0);
    let shown = 0;
    const html = COMMAND_GROUPS.map((g) => {
      if (this.cat !== "全部" && this.cat !== g.cat) return "";
      const rows = g.items.filter(([n, d, tr, exs]) => {
        if (!this.q) return true;
        return [g.cat, g.name, n, d, tr, ...exs].join(" ").toLowerCase().includes(this.q);
      });
      if (!rows.length) return "";
      shown += rows.length;
      return `<div class="panel panel-pad feat-group" style="--c:${g.c}">
        <div class="row" style="margin-bottom:6px"><h3 style="margin:0">${esc(g.name)}</h3><span class="grow"></span><span class="muted" style="font-size:12px">${esc(g.hint)}</span></div>
        ${rows.map(([n, d, tr, exs]) => `<div class="feat" style="align-items:center;gap:12px">
          <div class="grow"><div class="row" style="gap:8px"><span class="f-name">${esc(n)}</span><span class="tag" style="font-size:10px;opacity:0.85">${esc(tr)}</span></div><div class="f-desc">${esc(d)}</div></div>
          <div class="row" style="gap:6px;flex-wrap:wrap;justify-content:flex-end">${exs.map((ex) => `<button class="btn sm mono" data-copy-cmd="${esc(ex)}" title="点击复制该指令">${esc(ex)}</button>`).join("")}</div>
        </div>`).join("")}
      </div>`;
    }).filter(Boolean).join("");
    $("#cmdGrid").innerHTML = html || '<div class="empty">没有找到匹配的指令，换个关键词试试吧~</div>';
    $("#cmdCountTag").textContent = (!this.q && this.cat === "全部") ? `共 ${COMMAND_GROUPS.length} 大类 · ${total} 项指令` : `筛选结果：${shown} / ${total} 项指令`;
  },
};

// ================= 功能权限 =================
pages.perms = {
  gid: null, groups: [], features: [], saved: null, draft: null,
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>功能权限</h1><span class="sub">按群开关功能，保存后机器人立即生效</span><span class="grow"></span>
        <button class="btn sm" id="pmRefresh">${icon("refresh")}刷新群列表</button></div>
      <div class="split"><div class="panel scroll" style="padding:8px"><div class="list" id="pmGroups"></div></div>
        <div class="panel panel-pad" style="display:flex;flex-direction:column;min-height:0">
          <div class="row" style="margin-bottom:12px"><div class="grow"><div class="section-title" id="pmTitle" style="margin:0">选择一个群</div><div class="li-sub" id="pmSub"></div></div>
            <label class="check"><input type="checkbox" id="pmAll">全选</label></div>
          <div class="scroll grow"><div class="perm-grid" id="pmList"></div></div>
          <div class="save-bar"><span class="muted grow" id="pmHint">未修改</span><button class="btn" id="pmReset">还原</button><button class="btn primary" id="pmSave">${icon("save")}保存</button></div>
        </div></div>`;
    $("#pmRefresh").onclick = () => this.load(true);
    $("#pmGroups").onclick = async (e) => { const it = e.target.closest("[data-gid]"); if (it && (await this.leave())) this.select(it.dataset.gid); };
    $("#pmList").onclick = (e) => { const p = e.target.closest("[data-f]"); if (p && e.target.tagName !== "INPUT") { const c = $("input", p); c.checked = !c.checked; } this.collect(); };
    $("#pmList").onchange = () => this.collect();
    $("#pmAll").onchange = (e) => { $$("#pmList input").forEach((c) => (c.checked = e.target.checked)); this.collect(); };
    $("#pmReset").onclick = () => this.renderFeatures(this.saved);
    $("#pmSave").onclick = () => this.save();
  },
  show() { this.load(false); },
  async load(refresh) {
    try {
      const d = await api(`/api/groups${refresh ? "?refresh=1" : ""}`);
      this.groups = d.groups; this.features = d.features;
      $("#pmGroups").innerHTML = d.groups.length ? d.groups.map((g) => `<div class="list-item ${g.gid === this.gid ? "on" : ""}" data-gid="${g.gid}">
        <span class="letter">${esc(g.name.slice(0, 1))}</span><div class="li-main"><div class="li-title">${esc(g.name)}</div><div class="li-sub">${g.gid}</div></div></div>`).join("")
        : '<div class="empty">暂无群聊<br><span class="dim">机器人上线后会自动获取</span></div>';
      if (refresh) toast("群列表已刷新", "success");
      if (!this.gid && d.groups.length) this.select(d.groups[0].gid);
      else if (this.gid) this.select(this.gid, true);
    } catch (e) { toastErr(e); }
  },
  async select(gid, keepDraft) {
    this.gid = gid;
    $$("#pmGroups .list-item").forEach((x) => x.classList.toggle("on", x.dataset.gid === gid));
    const g = this.groups.find((x) => x.gid === gid) || { name: `群 ${gid}` };
    $("#pmTitle").textContent = g.name; $("#pmSub").textContent = `群号 ${gid}`;
    if (keepDraft && this.draft && this.dirty()) return;
    try { const d = await api(`/api/perms/${gid}`); this.saved = d.features; this.renderFeatures(d.features); } catch (e) { toastErr(e); }
  },
  renderFeatures(state) {
    $("#pmList").innerHTML = this.features.map((f) => `<div class="perm ${state[f.key] ? "on" : ""}" data-f="${f.key}">
      <label class="switch"><input type="checkbox" ${state[f.key] ? "checked" : ""}><span></span></label>
      <div class="grow"><div class="p-name">${esc(f.label)}</div><div class="p-sub">${f.default ? "默认开启" : "默认关闭"}</div></div></div>`).join("");
    this.collect();
  },
  collect() {
    const d = {};
    $$("#pmList [data-f]").forEach((p) => { d[p.dataset.f] = $("input", p).checked; p.classList.toggle("on", d[p.dataset.f]); });
    this.draft = d;
    const all = Object.values(d);
    $("#pmAll").checked = all.length > 0 && all.every(Boolean);
    $("#pmAll").indeterminate = all.some(Boolean) && !all.every(Boolean);
    const dirty = this.dirty();
    $("#pmHint").textContent = dirty ? "有未保存的修改" : "未修改";
    $("#pmHint").style.color = dirty ? "var(--accent)" : "";
  },
  dirty() { return this.saved && this.draft && Object.keys(this.draft).some((k) => this.draft[k] !== this.saved[k]); },
  async save() {
    if (!this.gid) return;
    try {
      const r = await post(`/api/perms/${this.gid}`, { features: this.draft });
      this.saved = r.features; this.collect();
      toast(r.miyoushe_baseline != null ? "已保存；米游社推送已开启，之前的历史资讯不会补推" : "功能权限已保存，立即生效", "success");
    } catch (e) { toastErr(e); }
  },
  async leave() {
    if (!this.dirty()) return true;
    const r = await confirmBox("未保存的修改", "功能权限有修改还没保存。", { ok: "保存", cancel: "取消", third: "放弃修改" });
    if (r === true) { await this.save(); return true; }
    if (r === "third") { this.renderFeatures(this.saved); return true; }
    return false;
  },
};

// ================= 详情日志 =================
pages.logs = {
  pos: -1, npos: -1, nfile: "", source: "bot", timer: null, maxBot: 3000, maxNap: 1500, showNap: true,
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>详情日志</h1><span class="sub" id="lgFile"></span><span class="grow"></span>
        <div class="seg" id="lgSrc"><button data-s="bot" class="on">机器人日志</button><button data-s="console">控制台输出</button></div>
        <label class="check" style="margin-left:8px"><input type="checkbox" id="lgNap" checked>NapCat</label>
        <button class="btn sm" id="lgRestart">${icon("refresh")}重启机器人</button>
        <button class="btn sm" id="lgClear">${icon("trash")}清空</button></div>
      <div class="log-wrap with-nap" id="lgWrap">
        <div class="log-box" id="lgBot"></div>
        <div style="display:flex;flex-direction:column;min-height:0" id="lgNapWrap"><div class="log-title">NapCat 日志 <span class="dim" id="lgNapFile"></span></div><div class="log-box grow" id="lgNapBox"></div></div>
      </div>`;
    $("#lgSrc").onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; this.source = b.dataset.s; $$("#lgSrc button").forEach((x) => x.classList.toggle("on", x === b)); this.pos = -1; $("#lgBot").innerHTML = ""; this.fetch(); };
    $("#lgNap").onchange = (e) => { this.showNap = e.target.checked; $("#lgNapWrap").style.display = this.showNap ? "" : "none"; $("#lgWrap").classList.toggle("with-nap", this.showNap); };
    $("#lgClear").onclick = async () => {
      const d = await api("/api/logs/end");
      this.pos = d[this.source] || 0; this.npos = d.napcat.pos; this.nfile = d.napcat.file || "";
      $("#lgBot").innerHTML = ""; $("#lgNapBox").innerHTML = "";
    };
    $("#lgRestart").onclick = async () => {
      if (!(await confirmBox("重启机器人", "只重启 NoneBot，QQ 登录不受影响。", { ok: "重启" }))) return;
      try { await post("/api/control/restart"); } catch (e) { toastErr(e); }
    };
    S.logSink = (ev) => this.append($("#lgBot"), [{ t: "panel-ev " + ev.level, s: `${new Date(ev.t * 1000).toLocaleTimeString("zh-CN", { hour12: false })} [面板] ${ev.text}` }], this.maxBot);
  },
  show() { this.fetch(); },
  hide() { clearTimeout(this.timer); },
  append(box, lines, max) {
    if (!lines.length) return;
    const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
    const frag = document.createDocumentFragment();
    for (const l of lines) { const d = document.createElement("div"); d.className = l.t; d.textContent = l.s; frag.appendChild(d); }
    box.appendChild(frag);
    while (box.childElementCount > max) box.firstElementChild.remove();
    if (stick) box.scrollTop = box.scrollHeight;
  },
  async fetch() {
    clearTimeout(this.timer);
    try {
      const q = `source=${this.source}&pos=${this.pos}&npos=${this.showNap ? this.npos : -2}&nfile=${encodeURIComponent(this.nfile)}`;
      const d = await api(`/api/logs?${q}`);
      if (!d.bot.exists && this.pos < 0) $("#lgBot").innerHTML = `<div class="dim">${d.bot.file} 还不存在——机器人启动后会生成。</div>`;
      this.pos = d.bot.pos; $("#lgFile").textContent = `logs/${d.bot.file}`;
      this.append($("#lgBot"), d.bot.lines, this.maxBot);
      if (this.showNap) {
        if (d.napcat.file !== this.nfile && this.nfile) $("#lgNapBox").innerHTML = "";
        this.nfile = d.napcat.file || ""; this.npos = d.napcat.pos;
        $("#lgNapFile").textContent = d.napcat.file || "（没有找到 NapCat 日志）";
        this.append($("#lgNapBox"), d.napcat.lines, this.maxNap);
      }
    } catch (e) { /* ignore */ }
    if (current === "logs") this.timer = setTimeout(() => this.fetch(), 1500);
  },
};

// ================= 统计 =================
pages.stats = {
  days: 1,
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>聊天统计</h1><span class="sub">只统计当前 QQ 所在的群</span><span class="grow"></span>
        <div class="seg" id="stRange"><button data-d="1" class="on">今天</button><button data-d="7">近 7 天</button><button data-d="30">近 30 天</button></div>
        <button class="btn sm" data-go="home">返回主页</button></div>
      <div class="page-body scroll" id="stBody"></div>`;
    $("#stRange").onclick = (e) => { const b = e.target.closest("[data-d]"); if (!b) return; this.days = +b.dataset.d; $$("#stRange button").forEach((x) => x.classList.toggle("on", x === b)); this.load(); };
  },
  show() { this.load(); },
  async load() {
    try {
      const d = await api(`/api/stats?days=${this.days}`);
      const max = Math.max(1, ...d.trend.map((t) => t.value));
      const kmax = Math.max(1, ...d.kinds.map((k) => k.value));
      const label = { 1: "今天", 7: "近 7 天", 30: "近 30 天" }[this.days];
      $("#stBody").innerHTML = `
        <div class="stat-top">
          <div class="panel panel-pad"><div class="section-title">${this.days === 1 ? "近 7 天趋势" : "每日趋势"}<span class="hint">${label}共 <b style="color:var(--accent)">${fmtNum(d.total)}</b> 次</span></div>
            <div class="chart">${d.trend.map((t) => `<div class="col"><div class="b ${t.today ? "today" : ""}" style="height:${(t.value / max) * 100}%">${t.value ? `<span>${t.value}</span>` : ""}</div><div class="d">${t.today ? "今天" : t.date}</div></div>`).join("")}</div></div>
          <div class="panel panel-pad"><div class="section-title">功能分布</div>
            <div class="big-num">${fmtNum(d.total)}</div><div class="dist" style="margin-top:10px">${d.kinds.map((k) => `
              <div class="dist-row"><span style="color:${k.color}">${k.label}</span><div class="dist-bar"><i style="width:${(k.value / kmax) * 100}%;background:${k.color}"></i></div><span style="text-align:right">${fmtNum(k.value)}</span></div>`).join("")}</div></div>
        </div>
        <div class="panel panel-pad"><div class="section-title">各群明细</div>
          ${d.groups.length ? `<div class="list">${d.groups.map((g) => `<div class="list-item" style="cursor:default;flex-direction:column;align-items:stretch;gap:7px">
            <div class="row"><span class="grow li-title">${esc(g.name)} <span class="dim">${g.gid}</span></span><b>${fmtNum(g.total)}</b></div>
            <div class="stack">${g.items.map((i) => `<i style="width:${(i.value / g.total) * 100}%;background:${i.color}" title="${i.label} ${i.value}"></i>`).join("")}</div>
            <div class="row wrap" style="gap:12px">${g.items.map((i) => `<span class="li-sub" style="color:${i.color}">${i.label} ${i.value}</span>`).join("")}</div></div>`).join("")}</div>`
          : '<div class="empty">这段时间还没有互动记录</div>'}</div>
        <div class="panel panel-pad"><div class="section-title">群友活跃<span class="hint">按权重算占比，权重在桌面面板「群友活跃」页里调</span></div><div id="stWho"><div class="empty">读取中…</div></div></div>`;
      this.loadWho();
    } catch (e) { toastErr(e); }
  },
  async loadWho() {
    try {
      const w = await api(`/api/member_usage?days=${this.days}`);
      const box = $("#stWho"); if (!box) return;
      if (!w.rows.length) { box.innerHTML = '<div class="empty">这段时间还没有按人的记录</div>'; return; }
      const head = w.cols.map((c) => `<th style="color:${c.color};text-align:right;padding:4px 8px">${esc(c.label)}</th>`).join("");
      box.innerHTML = `<div class="scroll" style="max-height:420px"><table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr><th style="text-align:left;padding:4px 8px">#</th><th style="text-align:left;padding:4px 8px">群友</th>${head}<th style="text-align:right;padding:4px 8px">占比</th></tr>
        ${w.rows.slice(0, 50).map((r, i) => `<tr style="border-top:1px solid rgba(255,255,255,.06)"><td style="padding:4px 8px" class="dim">${i + 1}</td>
          <td style="padding:4px 8px">${esc(r.nick)} <span class="dim">${r.uid}</span></td>
          ${w.cols.map((c) => `<td style="text-align:right;padding:4px 8px">${fmtNum(r.counts[c.key] || 0)}${c.key === "upscale_img" && r.counts.upscale ? ` <span class="dim">/${r.counts.upscale}次</span>` : ""}</td>`).join("")}
          <td style="text-align:right;padding:4px 8px;color:var(--accent)">${(r.share * 100).toFixed(1)}%</td></tr>`).join("")}
      </table></div>`;
    } catch (e) { /* ignore */ }
  },
};

// ================= 关于 =================
pages.about = {
  mount(el) { this.el = el; },
  async show() {
    let groups = [];
    try { groups = (await api("/api/groups")).groups; } catch (e) { /* ignore */ }
    const o = S.overview || {};
    this.el.innerHTML = `<div class="page-head"><h1>关于</h1><span class="sub">芙宁娜控制面板与机器人运行环境信息</span></div>
      <div class="page-body scroll">
        <div class="panel about-hero"><img src="/media/asset/avatar" alt=""><div class="grow"><div style="font-size:22px;font-weight:800">芙宁娜控制面板</div>
          <div class="muted">把机器人配置、运行状态、权限和数据查看集中在一个清晰的工作台中。</div></div><span class="tag ok">v${esc(PANEL.version)}</span></div>
        <div class="info-grid">
          <div class="panel info-tile" style="--c:#ffd166"><div class="cap">版本</div><div class="val">v${esc(PANEL.version)}</div></div>
          <div class="panel info-tile" style="--c:#6ad4ff"><div class="cap">QQ 机器人</div><div class="val">NoneBot2 + NapCat · QQ ${esc(o.bot_qq || "未设置")}</div></div>
          <div class="panel info-tile" style="--c:#ec4899"><div class="cap">守护群聊</div><div class="val">${groups.length ? groups.map((g) => esc(g.name)).join(" / ") : "机器人上线后显示"}</div></div>
          <div class="panel info-tile" style="--c:#a78bfa"><div class="cap">AI 体系</div><div class="val">聊天 / 看图 / 搜图比对统一模型接口</div></div>
          <div class="panel info-tile" style="--c:#3ecf8e"><div class="cap">数据存储</div><div class="val">SQLite（WAL）· 面板修改即时生效</div></div>
          <div class="panel info-tile" style="--c:#f59e0b"><div class="cap">面板</div><div class="val">本机网页界面 + 桌面窗口，仅本机可访问</div></div>
        </div></div>`;
  },
};

// ================= 角色管理 / 共享设定 =================
pages.roles = {
  tab: "roles", roles: [], saved: "", sel: null, active: "", lore: [], loreRoles: [], loreSel: null, loreSaved: "",
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>角色管理</h1><span class="sub" id="rlSub"></span><span class="grow"></span>
        <div class="seg" id="rlTab"><button data-t="roles" class="on">角色人格</button><button data-t="lore">共享设定</button></div></div>
      <div class="split" id="rlRoles">
        <div class="panel" style="display:flex;flex-direction:column;min-height:0;padding:8px">
          <div class="scroll grow"><div class="list" id="rlList"></div></div>
          <div class="row" style="padding:8px 4px 2px"><button class="btn sm grow" id="rlNew">${icon("plus")}新建</button><button class="btn sm" id="rlCopy" title="复制">${icon("copy")}</button><button class="btn sm danger" id="rlDel" title="删除">${icon("trash")}</button></div>
        </div>
        <div class="panel panel-pad role-edit" id="rlEdit"></div>
      </div>
      <div class="split" id="rlLore" style="display:none">
        <div class="panel" style="display:flex;flex-direction:column;min-height:0;padding:8px">
          <div class="scroll grow"><div class="list" id="loList"></div></div>
          <div class="row" style="padding:8px 4px 2px"><button class="btn sm grow" id="loNew">${icon("plus")}新建设定</button><button class="btn sm danger" id="loDel">${icon("trash")}</button></div>
        </div>
        <div class="panel panel-pad role-edit" id="loEdit"></div>
      </div>`;
    $("#rlTab").onclick = async (e) => {
      const b = e.target.closest("[data-t]"); if (!b || b.dataset.t === this.tab) return;
      if (!(await this.leave())) return;
      this.tab = b.dataset.t; $$("#rlTab button").forEach((x) => x.classList.toggle("on", x === b));
      $("#rlRoles").style.display = this.tab === "roles" ? "" : "none"; $("#rlLore").style.display = this.tab === "lore" ? "" : "none";
      this.tab === "lore" ? this.loadLore() : this.load();
    };
    $("#rlList").onclick = (e) => { const it = e.target.closest("[data-i]"); if (it) { this.pull(); this.sel = +it.dataset.i; this.render(); } };
    $("#rlNew").onclick = () => { this.pull(); this.roles.push({ id: `role_${Date.now()}`, name: "新角色", prompt: "" }); this.sel = this.roles.length - 1; this.render(); $("#rlName").select(); };
    $("#rlCopy").onclick = () => { this.pull(); const r = this.roles[this.sel]; if (!r) return; this.roles.push({ ...r, id: `role_${Date.now()}`, name: `${r.name} 副本` }); this.sel = this.roles.length - 1; this.render(); };
    $("#rlDel").onclick = async () => {
      const r = this.roles[this.sel]; if (!r) return;
      if (r.id === this.active) return toast("这是当前使用的角色，先把别的角色设为当前再删除", "warn");
      if (!(await confirmBox("删除角色", `确定删除「${r.name}」？保存后生效。`, { ok: "删除", danger: true }))) return;
      this.roles.splice(this.sel, 1); this.sel = Math.max(0, this.sel - 1); this.render();
    };
    $("#loList").onclick = async (e) => { const it = e.target.closest("[data-i]"); if (it && (await this.leaveLore())) { this.loreSel = +it.dataset.i; this.renderLore(); } };
    $("#loNew").onclick = async () => { if (!(await this.leaveLore())) return; this.lore.push({ id: "", name: "新设定", keywords: "", content: "", role_ids: [] }); this.loreSel = this.lore.length - 1; this.renderLore(); };
    $("#loDel").onclick = async () => {
      const it = this.lore[this.loreSel]; if (!it) return;
      if (!(await confirmBox("删除设定", `确定删除「${it.name}」？`, { ok: "删除", danger: true }))) return;
      try { if (it.id) await api(`/api/lore/${encodeURIComponent(it.id)}`, { method: "DELETE" }); this.lore.splice(this.loreSel, 1); this.loreSel = Math.max(0, this.loreSel - 1); this.loreSaved = ""; this.renderLore(); toast("已删除", "success"); } catch (err) { toastErr(err); }
    };
  },
  show() { this.tab === "lore" ? this.loadLore() : this.load(); },
  async load() {
    try {
      const d = await api("/api/roles");
      this.roles = d.roles; this.active = d.active; this.saved = JSON.stringify(d.roles);
      $("#rlSub").textContent = d.bot_qq ? `当前机器人 QQ ${d.bot_qq}` : "";
      if (this.sel == null || this.sel >= this.roles.length) this.sel = Math.max(0, this.roles.findIndex((r) => r.id === this.active));
      this.render();
    } catch (e) { toastErr(e); }
  },
  render() {
    $("#rlList").innerHTML = this.roles.length ? this.roles.map((r, i) => `<div class="list-item ${i === this.sel ? "on" : ""}" data-i="${i}">
      <span class="letter">${esc((r.name || "?").slice(0, 1))}</span><div class="li-main"><div class="li-title">${esc(r.name)}${r.id === this.active ? '<span class="badge-cur">当前</span>' : ""}</div><div class="li-sub">${esc(r.id)}</div></div></div>`).join("")
      : '<div class="empty">还没有角色<br><span class="dim">不设置时使用内置芙宁娜人格</span></div>';
    const r = this.roles[this.sel];
    $("#rlEdit").innerHTML = r ? `
      <div class="form-grid"><div class="field"><label>角色名称</label><input class="input" id="rlName" value="${esc(r.name)}"></div>
        <div class="field"><label>角色 ID</label><input class="input mono" id="rlId" value="${esc(r.id)}"></div></div>
      <div class="field"><label>称呼 <span class="dim">群友怎么叫 TA，逗号分隔，第一个作为名字；留空取角色名「-」前面的部分</span></label><input class="input" id="rlAlias" placeholder="例如：胡桃, 胡堂主, 堂主" value="${esc(Array.isArray(r.aliases) ? r.aliases.join("，") : r.aliases || "")}"></div>
      <div class="field" style="flex:1;min-height:0"><label>人格设定 <span class="dim">只写性格与说话方式；通用规则和共享设定会自动追加</span></label><textarea class="textarea" id="rlPrompt">${esc(r.prompt)}</textarea></div>
      <div class="save-bar" style="margin-top:0"><span class="muted grow" id="rlHint"></span>
        <button class="btn" id="rlActivate" ${r.id === this.active ? "disabled" : ""}>${r.id === this.active ? "已是当前角色" : "设为当前角色"}</button>
        <button class="btn primary" id="rlSave">${icon("save")}保存</button></div>` : '<div class="empty">选择或新建一个角色</div>';
    if (!r) return;
    const upd = () => { this.pull(); const dirty = this.dirty(); $("#rlHint").textContent = dirty ? "有未保存的修改" : ""; $("#rlHint").style.color = "var(--accent)"; };
    ["rlName", "rlId", "rlAlias", "rlPrompt"].forEach((id) => ($("#" + id).oninput = upd));
    $("#rlSave").onclick = () => this.save();
    $("#rlActivate").onclick = async () => {
      try {
        if (this.dirty()) await this.save();
        await post("/api/roles/activate", { role_id: this.roles[this.sel].id });
        this.active = this.roles[this.sel].id; this.render(); toast("已设为当前角色，下一条消息起生效", "success");
      } catch (e) { toastErr(e); }
    };
    upd();
  },
  pull() {
    const r = this.roles[this.sel]; if (!r || !$("#rlName")) return;
    r.name = $("#rlName").value; r.id = $("#rlId").value.trim(); r.prompt = $("#rlPrompt").value;
    const al = $("#rlAlias").value.trim(); if (al || "aliases" in r) r.aliases = al;
  },
  dirty() { this.pull(); return JSON.stringify(this.roles) !== this.saved; },
  async save() {
    this.pull();
    try { await post("/api/roles", { roles: this.roles }); this.saved = JSON.stringify(this.roles); toast("角色已保存", "success"); this.render(); }
    catch (e) { toastErr(e); throw e; }
  },
  async leave() {
    if (this.tab === "lore") return this.leaveLore();
    if (!this.dirty()) return true;
    const r = await confirmBox("未保存的修改", "角色有修改还没保存。", { ok: "保存", third: "放弃修改" });
    if (r === true) { try { await this.save(); return true; } catch (e) { return false; } }
    if (r === "third") { this.roles = JSON.parse(this.saved); return true; }
    return false;
  },
  // ---- 共享设定 ----
  async loadLore() {
    try {
      const d = await api("/api/lore");
      this.lore = d.items; this.loreRoles = d.roles; this.seeded = d.seeded;
      if (this.loreSel == null || this.loreSel >= this.lore.length) this.loreSel = 0;
      $("#rlSub").textContent = "故事 / 背景类长设定：聊天内容命中关键词时才注入，可指定只对部分角色生效";
      this.renderLore();
    } catch (e) { toastErr(e); }
  },
  renderLore() {
    $("#loList").innerHTML = this.lore.length ? this.lore.map((it, i) => `<div class="list-item ${i === this.loreSel ? "on" : ""}" data-i="${i}">
      <div class="li-main"><div class="li-title">${esc(it.name)}${it.id ? "" : ' <span class="tag warn">未保存</span>'}</div><div class="li-sub">${esc(it.keywords || "无关键词")}</div></div></div>`).join("")
      : `<div class="empty">${this.seeded === false ? "启动一次机器人后即可编辑" : "还没有共享设定"}</div>`;
    const it = this.lore[this.loreSel];
    if (!it) { $("#loEdit").innerHTML = '<div class="empty">选择或新建一条设定</div>'; this.loreSaved = ""; return; }
    const all = !it.role_ids || !it.role_ids.length;
    $("#loEdit").innerHTML = `
      <div class="form-grid"><div class="field"><label>名称</label><input class="input" id="loName" value="${esc(it.name)}"></div>
        <div class="field"><label>关键词 <span class="dim">逗号分隔，命中任一即注入</span></label><input class="input" id="loKw" value="${esc(it.keywords)}"></div></div>
      <div class="field"><label>适用角色 <span class="dim">都不勾 = 全部角色</span></label><div class="row wrap" id="loRoles">${this.loreRoles.map((r) => `<label class="check"><input type="checkbox" value="${esc(r.id)}" ${!all && it.role_ids.includes(r.id) ? "checked" : ""}>${esc(r.name)}</label>`).join("") || '<span class="dim">暂无角色</span>'}</div></div>
      <div class="field" style="flex:1;min-height:0"><label>设定内容</label><textarea class="textarea" id="loContent">${esc(it.content)}</textarea></div>
      <div class="save-bar" style="margin-top:0"><span class="muted grow" id="loHint"></span><button class="btn primary" id="loSave">${icon("save")}保存设定</button></div>`;
    this.loreSaved = JSON.stringify(this.pullLore());
    $("#loEdit").oninput = $("#loEdit").onchange = () => { $("#loHint").textContent = this.loreDirty() ? "有未保存的修改" : ""; $("#loHint").style.color = "var(--accent)"; };
    $("#loSave").onclick = () => this.saveLore();
  },
  pullLore() {
    if (!$("#loName")) return null;
    return { name: $("#loName").value, keywords: $("#loKw").value, content: $("#loContent").value, role_ids: $$("#loRoles input:checked").map((x) => x.value) };
  },
  loreDirty() { const it = this.lore[this.loreSel]; return !!it && (!it.id || JSON.stringify(this.pullLore()) !== this.loreSaved); },
  async saveLore() {
    const it = this.lore[this.loreSel]; if (!it) return;
    try {
      const r = await post("/api/lore", { id: it.id, ...this.pullLore() });
      this.lore[this.loreSel] = r.item; this.renderLore(); toast("共享设定已保存", "success");
    } catch (e) { toastErr(e); throw e; }
  },
  async leaveLore() {
    if (!this.loreDirty()) return true;
    const r = await confirmBox("未保存的修改", "这条共享设定还没保存。", { ok: "保存", third: "放弃修改" });
    if (r === true) { try { await this.saveLore(); return true; } catch (e) { return false; } }
    if (r === "third") { if (!this.lore[this.loreSel].id) this.lore.splice(this.loreSel, 1); this.loreSaved = ""; return true; }
    return false;
  },
};

// ================= 回复次数 =================
pages.limits = {
  data: null, q: "", gid: "", sort: "name",
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>回复次数</h1><span class="sub">每人每天找芙宁娜聊天的次数；群主与语音号固定无限</span></div>
      <div class="panel panel-pad row wrap" style="flex:none;margin-bottom:12px;gap:14px">
        <span class="section-title" style="margin:0">统一上限</span>
        <input class="input" id="lmUni" type="number" min="0" max="999" style="width:110px">
        <span class="muted">次 / 人 / 天（0 = 不限）</span><button class="btn primary sm" id="lmUniSave">保存</button>
        <span class="grow"></span>
        <div class="row" style="width:220px;position:relative"><input class="input" id="lmQ" placeholder="搜索昵称 / QQ" style="padding-left:32px"><span style="position:absolute;left:10px;top:8px;color:var(--dim)">${icon("search")}</span></div>
        <select class="select" id="lmGroup" style="width:180px"></select>
        <div class="seg" id="lmSort"><button data-s="name" class="on">按拼音</button><button data-s="remain">按剩余</button><button data-s="used">按已用</button></div>
      </div>
      <div class="panel scroll grow" style="padding:0 6px 6px"><table class="limit-table"><thead><tr><th style="width:36px"></th><th>群友</th><th>群</th><th>今日已用</th><th>剩余</th><th>上限</th></tr></thead><tbody id="lmBody"></tbody></table></div>`;
    $("#lmUniSave").onclick = async () => {
      const v = parseInt($("#lmUni").value, 10);
      if (!(v >= 0 && v <= 999)) return toast("请输入 0–999 的整数", "warn");
      try { await post("/api/limit/unified", { value: v }); toast(v ? `统一上限已改为 ${v} 次/天` : "已设为不限次数", "success"); this.load(); } catch (e) { toastErr(e); }
    };
    $("#lmQ").oninput = (e) => { this.q = e.target.value.trim().toLowerCase(); this.render(); };
    $("#lmGroup").onchange = (e) => { this.gid = e.target.value; this.render(); };
    $("#lmSort").onclick = (e) => { const b = e.target.closest("[data-s]"); if (!b) return; this.sort = b.dataset.s; $$("#lmSort button").forEach((x) => x.classList.toggle("on", x === b)); this.render(); };
    $("#lmBody").onclick = (e) => { const tr = e.target.closest("[data-u]"); if (tr) this.edit(tr.dataset.g, tr.dataset.u); };
  },
  show() { this.load(); },
  async load() {
    try {
      this.data = await api("/api/members");
      if (document.activeElement !== $("#lmUni")) $("#lmUni").value = this.data.unified;
      $("#lmGroup").innerHTML = `<option value="">全部群</option>` + this.data.groups.map((g) => `<option value="${g.gid}" ${g.gid === this.gid ? "selected" : ""}>${esc(g.name)}</option>`).join("");
      this.render();
    } catch (e) { toastErr(e); }
  },
  render() {
    if (!this.data) return;
    const gname = Object.fromEntries(this.data.groups.map((g) => [g.gid, g.name]));
    let rows = this.data.rows.filter((r) => (!this.gid || r.gid === this.gid) && (!this.q || r.nick.toLowerCase().includes(this.q) || r.uid.includes(this.q)));
    const rem = (r) => (r.remain == null ? 1e9 : r.remain);
    const coll = new Intl.Collator("zh-CN");
    rows.sort(this.sort === "remain" ? (a, b) => rem(a) - rem(b) : this.sort === "used" ? (a, b) => b.used - a.used
      : (a, b) => (a.initial === "~") - (b.initial === "~") || a.initial.localeCompare(b.initial) || coll.compare(a.nick, b.nick));
    const shown = rows.slice(0, 600);
    $("#lmBody").innerHTML = shown.length ? shown.map((r) => {
      const pct = r.limit > 0 ? Math.min(100, (r.used / r.limit) * 100) : 0;
      const cls = r.limit > 0 && r.remain === 0 ? "out" : pct >= 70 ? "low" : "";
      return `<tr data-g="${r.gid}" data-u="${r.uid}"><td><span class="letter" style="width:26px;height:26px;font-size:12px">${esc(r.initial)}</span></td>
        <td><div class="li-title" style="max-width:220px">${esc(r.nick)}</div><div class="li-sub">${r.uid}</div></td>
        <td class="muted" style="max-width:160px;overflow:hidden;text-overflow:ellipsis">${esc(gname[r.gid] || r.gid)}</td>
        <td><div class="row"><div class="bar ${cls}"><i style="width:${r.limit > 0 ? pct : 0}%"></i></div><span>${r.used}</span></div></td>
        <td>${r.remain == null ? '<span class="tag ok">不限</span>' : r.remain === 0 ? '<span class="tag err">已用完</span>' : r.remain}</td>
        <td>${r.fixed ? '<span class="tag info">固定无限</span>' : r.limit > 0 ? r.limit : '<span class="tag ok">无限</span>'}</td></tr>`;
    }).join("") + (rows.length > shown.length ? `<tr><td colspan="6" class="dim" style="text-align:center">还有 ${rows.length - shown.length} 人，请用搜索缩小范围</td></tr>` : "")
      : `<tr><td colspan="6"><div class="empty">${this.data.rows.length ? "没有匹配的群友" : "还没有群友数据，机器人上线后会自动同步成员"}</div></td></tr>`;
  },
  async edit(gid, uid) {
    let d;
    try { d = await api(`/api/limit/${gid}/${uid}`); } catch (e) { return toastErr(e); }
    const row = this.data.rows.find((r) => r.gid === gid && r.uid === uid) || { nick: uid, used: 0 };
    const m = modal.open(`<div class="modal-head"><h2>${esc(row.nick)}</h2><span class="tag">${uid}</span></div>
      <div class="modal-body" style="display:flex;flex-direction:column;gap:14px">
        <div class="muted">${esc(d.group_name)} · 今日已用 ${row.used} 次 · 当前上限 ${d.effective > 0 ? d.effective + " 次" : "无限"}（来源：${d.source}）${d.favor ? `<br>好感：${esc(d.favor)}` : ""}</div>
        ${d.fixed ? '<div class="warn-box">这是群主 / 语音号，固定无限次数，无需设置。</div>' : `
        <div class="field"><label>次数</label><div class="seg" id="ueMode"><button data-m="follow">跟随统一</button><button data-m="custom">自定义</button><button data-m="unlimited">无限</button></div></div>
        <div class="field" id="ueValWrap"><label>每天次数（1–999）</label><input class="input" type="number" min="1" max="999" id="ueVal" value="${d.value}" style="width:140px"></div>
        <div class="field" id="ueScopeWrap"><label>生效范围</label><div class="seg" id="ueScope"><button data-s="group">仅本群</button><button data-s="all">所有群</button></div></div>`}
      </div>
      <div class="modal-foot"><button class="btn" data-x="cancel">取消</button>${d.fixed ? "" : '<button class="btn primary" data-x="save">保存</button>'}</div>`);
    let mode = d.mode, scope = d.scope;
    const sync = () => {
      $$("#ueMode button", m).forEach((b) => b.classList.toggle("on", b.dataset.m === mode));
      $$("#ueScope button", m).forEach((b) => b.classList.toggle("on", b.dataset.s === scope));
      if ($("#ueValWrap", m)) { $("#ueValWrap", m).style.display = mode === "custom" ? "" : "none"; $("#ueScopeWrap", m).style.display = mode === "follow" ? "none" : ""; }
    };
    sync();
    m.onclick = async (e) => {
      const mb = e.target.closest("[data-m]"); if (mb) { mode = mb.dataset.m; sync(); }
      const sb = e.target.closest("[data-s]"); if (sb) { scope = sb.dataset.s; sync(); }
      const x = e.target.closest("[data-x]"); if (!x) return;
      if (x.dataset.x === "cancel") return modal.close();
      const v = parseInt(($("#ueVal", m) || {}).value, 10);
      if (mode === "custom" && !(v >= 1 && v <= 999)) return toast("请输入 1–999 的整数", "warn");
      try { const r = await post(`/api/limit/${gid}/${uid}`, { mode, scope, value: v }); modal.close(); toast(`${row.nick}：${r.desc}`, "success"); this.load(); } catch (err) { toastErr(err); }
    };
  },
};

// ================= 设置 =================
const field = (label, inner, help = "", cls = "") => `<div class="field ${cls}"><label>${label}</label>${inner}${help ? `<div class="help">${help}</div>` : ""}</div>`;
const numInput = (id, min, max, w = 140) => `<input class="input" type="number" id="${id}" min="${min}" max="${max}" style="width:${w}px">`;
const sw = (id) => `<label class="switch"><input type="checkbox" id="${id}"><span></span></label>`;
const JOB_STATE = { completed: "ok", partial: "warn", running: "info", started: "info", waiting: "info", skipped: "", stopped: "err", interrupted: "err" };

const SETTINGS_TABS = {
  game_query: { label: "游戏管理", html: () => `<div class="panel panel-pad"><h2>游戏战绩与图鉴</h2><p class="muted">按群开关在功能权限；账号、游戏开关、签到和指令集中在独立页面。</p><button class="btn primary" data-go="games">打开游戏管理</button></div>` },
  accounts: {
    label: "账号与角色",
    html: () => `<div class="panel panel-pad"><div class="section-title">机器人账号<span class="hint">切换账号会写入 BOT_QQ、生成 NapCat 连接配置，并完全重启机器人</span></div>
        <div class="list" id="acList" style="gap:8px"></div></div>
      <div class="panel panel-pad"><div class="section-title">添加 / 修改账号</div>
        <div class="form-grid" style="grid-template-columns:1fr 1fr 1fr auto;align-items:end">
          ${field("QQ 号", '<input class="input" id="acQQ" placeholder="纯数字">')}
          ${field("昵称", '<div class="row"><input class="input" id="acNick" placeholder="可自动获取"><button class="btn sm" id="acFetch">获取</button></div>')}
          ${field("绑定角色", '<select class="select" id="acRole"></select>')}
          <button class="btn primary" id="acAdd">${icon("save")}保存</button></div></div>
      <div class="panel panel-pad row"><div class="grow"><div class="section-title" style="margin:0">搜图浏览器</div><div class="li-sub">以图搜图使用的 Edge 专用资料夹；Google 登录失效时在这里重新登录（关闭窗口后生效）</div></div>
        <button class="btn" data-login="google">登录 Google 账号</button><button class="btn" data-login="x">登录 X 账号</button></div>`,
    async load(el) {
      const d = await api("/api/accounts");
      const roleName = Object.fromEntries(d.roles.map((r) => [r.id, r.name]));
      $("#acRole", el).innerHTML = '<option value="">（内置人格）</option>' + d.roles.map((r) => `<option value="${esc(r.id)}">${esc(r.name)}</option>`).join("");
      $("#acList", el).innerHTML = d.accounts.length ? d.accounts.map((a) => `<div class="acct ${String(a.qq) === d.bot_qq ? "cur" : ""}">
          <span class="letter">${esc((a.nickname || "?").slice(0, 1))}</span>
          <div class="grow"><div class="li-title">${esc(a.nickname || "未命名")} <span class="dim mono">${a.qq}</span>${String(a.qq) === d.bot_qq ? '<span class="badge-cur">使用中</span>' : ""}</div>
          <div class="li-sub">角色：${esc(roleName[a.role_id] || "内置人格")}</div></div>
          <button class="btn sm ghost" data-ac="edit" data-qq="${a.qq}">编辑</button>
          ${String(a.qq) === d.bot_qq ? "" : `<button class="btn sm" data-ac="use" data-qq="${a.qq}">选用此号并重启</button><button class="btn sm danger" data-ac="del" data-qq="${a.qq}">${icon("trash")}</button>`}
        </div>`).join("") : `<div class="empty">还没有保存账号${d.bot_qq ? `（当前 BOT_QQ：${d.bot_qq}）` : ""}</div>`;
      el.onclick = async (e) => {
        const b = e.target.closest("[data-ac]");
        if (b) {
          const qq = b.dataset.qq, a = d.accounts.find((x) => String(x.qq) === qq);
          if (b.dataset.ac === "edit") { $("#acQQ", el).value = qq; $("#acNick", el).value = a.nickname || ""; $("#acRole", el).value = a.role_id || ""; }
          if (b.dataset.ac === "del" && (await confirmBox("删除账号", `从列表移除 ${a.nickname || qq}？不会影响 QQ 本身。`, { ok: "删除", danger: true }))) {
            try { await api(`/api/accounts/${qq}`, { method: "DELETE" }); toast("已删除", "success"); this.load(el); } catch (err) { toastErr(err); }
          }
          if (b.dataset.ac === "use" && (await confirmBox("切换账号", `切换到 ${a.nickname || qq} 并完全重启机器人？\n如果这个号还没在 NapCat 登录过，需要在登录助手里扫码。`, { ok: "切换并重启" }))) {
            try { await post("/api/accounts/use", { qq }); toast("已切换，正在重启…", "success"); this.load(el); } catch (err) { toastErr(err); }
          }
        }
        if (e.target.closest("#acFetch")) {
          const qq = $("#acQQ", el).value.trim(); if (!/^\d{5,12}$/.test(qq)) return toast("请先填写正确的 QQ 号", "warn");
          const r = await api(`/api/accounts/nickname/${qq}`); if (r.nickname) $("#acNick", el).value = r.nickname; else toast("获取失败（需要机器人在线）", "warn");
        }
        if (e.target.closest("#acAdd")) {
          try { await post("/api/accounts", { qq: $("#acQQ", el).value.trim(), nickname: $("#acNick", el).value, role_id: $("#acRole", el).value }); toast("账号已保存", "success"); this.load(el); }
          catch (err) { toastErr(err); }
        }
      };
    },
  },
  model: {
    label: "AI 模型",
    html: () => `<div class="panel panel-pad"><div class="section-title">接口配置<span class="hint">聊天、看图和搜图比对共用；保存后写入 .env，机器人自动热加载</span></div>
        <div class="form-grid">
          ${field("Base URL", '<input class="input mono" id="mdUrl" placeholder="https://…/v1">', "可以只填到 /v1，程序会自动补全", "full")}
          ${field("API Key", '<div class="row"><input class="input mono" id="mdKey" type="password" placeholder="sk-…"><button class="btn sm" id="mdEye" style="width:64px">显示</button></div>', "", "full")}
          ${field("模型", '<input class="input mono" id="mdModel" readonly>', "固定为 deepseek-flash：文字聊天、看图、搜图比对共用，已验证可用")}
        </div></div>
      <div class="panel panel-pad"><div class="section-title">连接测试<span class="hint">用当前填写的地址和密钥发一条最小请求，不会保存</span></div>
        <div class="row"><button class="btn" id="mdTest">测试连接</button><span class="muted" id="mdRes">尚未测试</span></div></div>`,
    async load(el) {
      const d = await api("/api/model");
      $("#mdUrl", el).value = d.base_url; $("#mdKey", el).value = d.api_key; $("#mdModel", el).value = d.model;
      $("#mdEye", el).onclick = () => { const k = $("#mdKey", el); const hid = k.type === "password"; k.type = hid ? "text" : "password"; $("#mdEye", el).textContent = hid ? "隐藏" : "显示"; };
      $("#mdTest", el).onclick = async () => {
        $("#mdTest", el).disabled = true; $("#mdRes", el).textContent = "正在测试…"; $("#mdRes", el).style.color = "";
        try { const r = await post("/api/model/test", this.value(el)); $("#mdRes", el).textContent = (r.ok ? "✓ " : "✗ ") + r.text; $("#mdRes", el).style.color = r.ok ? "var(--green)" : "var(--red)"; }
        catch (e) { $("#mdRes", el).textContent = e.message; }
        $("#mdTest", el).disabled = false;
      };
    },
    value: (el) => ({ base_url: $("#mdUrl", el).value.trim(), api_key: $("#mdKey", el).value.trim() }),
    save: (el, v) => post("/api/model", v),
  },
  pixiv: {
    label: "Pixiv 推送",
    html: () => `<div class="panel panel-pad"><div class="row" style="margin-bottom:12px"><div class="grow"><div class="section-title" style="margin:0">定时推送</div><div class="li-sub">每天 5 批发送日/周/月榜前 150 名（北京时间）；「点图」「ID 取图」还需在「功能权限」里按群开启</div></div>${sw("pxOn")}</div>
        ${field("五个批次时间", `<div class="times">${[0, 1, 2, 3, 4].map((i) => `<input class="input mono" id="pxT${i}" placeholder="HH:MM">`).join("")}</div>`)}
        <div class="form-grid" style="grid-template-columns:repeat(3,1fr);margin-top:14px">
          ${field("每张图间隔（秒）", numInput("pxInt", 2, 60), "2–60")}${field("原图缓存上限（MB）", numInput("pxCache", 64, 1024), '64–1024 · 当前占用 <b id="pxCacheNow">-</b> MB')}${field("点图冷却（秒）", numInput("pxCd", 10, 3600), "10–3600，每群；点图与 ID 取图共用")}
        </div></div>
      <div class="panel panel-pad"><div class="section-title">立即补推<span class="hint">漏推或想提前看时使用；机器人下一次检查时领取（避开定时批次前后）</span></div>
        <div class="row wrap"><span class="muted">批次</span><div class="row" id="pxBatches">${[1, 2, 3, 4, 5].map((i) => `<label class="check"><input type="checkbox" value="${i - 1}">第 ${i} 批</label>`).join("")}</div>
          <span class="grow"></span><span id="pxPending"></span><button class="btn" id="pxPush">${icon("send")}立即补推</button></div></div>
      <div class="panel panel-pad"><div class="section-title">标签映射<span class="hint">点图时把中文名换成 Pixiv 标签，JSON：{"中文名": ["日文标签", "英文标签"]}</span></div>
        <textarea class="textarea mono" id="pxAlias" rows="6" spellcheck="false"></textarea>
        <div class="row" style="margin-top:8px"><button class="btn sm" id="pxCheck">检查格式</button><span class="muted" id="pxCheckRes"></span></div></div>
      <div class="panel panel-pad"><div class="section-title">最近运行记录<span class="hint" id="pxErr"></span></div><div id="pxJobs"></div></div>`,
    async load(el) {
      const d = await api("/api/pixiv");
      const s = d.settings;
      $("#pxOn", el).checked = s.enabled;
      s.times.forEach((t, i) => ($(`#pxT${i}`, el).value = t));
      $("#pxInt", el).value = s.interval_seconds; $("#pxCache", el).value = s.cache_mb; $("#pxCd", el).value = s.search_cooldown_seconds;
      $("#pxAlias", el).value = JSON.stringify(s.aliases, null, 2);
      $("#pxCacheNow", el).textContent = d.cache_mb;
      $("#pxErr", el).textContent = d.error;
      $("#pxPending", el).innerHTML = d.pending ? `<span class="tag warn">有待领取的补推请求</span> <button class="btn sm ghost" id="pxCancel">撤销</button>` : "";
      $("#pxJobs", el).innerHTML = d.rows.length ? `<table class="job-table">${d.rows.slice(0, 40).map((r) => `<tr><td>${new Date(r.updated * 1000).toLocaleString("zh-CN", { hour12: false })}</td><td class="mono" style="color:var(--muted)">${esc(r.key)}</td><td><span class="tag ${JOB_STATE[r.status] || ""}">${esc(r.label)}</span></td><td class="dim">${esc(r.detail)}</td></tr>`).join("")}</table>` : '<div class="empty">还没有运行记录</div>';
      $("#pxCheck", el).onclick = async () => {
        try { const r = await post("/api/pixiv/aliases/check", { aliases: JSON.parse($("#pxAlias", el).value || "{}") }); $("#pxCheckRes", el).textContent = "✓ " + r.text; $("#pxCheckRes", el).style.color = "var(--green)"; }
        catch (e) { $("#pxCheckRes", el).textContent = "✗ " + (e instanceof SyntaxError ? "JSON 格式错误：" + e.message : e.message); $("#pxCheckRes", el).style.color = "var(--red)"; }
      };
      $("#pxPush", el).onclick = async () => {
        const batches = $$("#pxBatches input:checked", el).map((x) => +x.value);
        if (!batches.length) return toast("先勾选要补推的批次", "warn");
        if (!(await confirmBox("立即补推", `补推今天第 ${batches.map((b) => b + 1).join("、")} 批到所有已开启 Pixiv 推送的群？\n已发过的作品不会重复发送。`, { ok: "补推" }))) return;
        try { await post("/api/pixiv/push", { batches }); toast("补推请求已提交，机器人稍后开始发送", "success"); this.load(el); } catch (e) { toastErr(e); }
      };
      const c = $("#pxCancel", el);
      if (c) c.onclick = async () => { await api("/api/pixiv/push", { method: "DELETE" }); toast("已撤销补推请求"); this.load(el); };
    },
    value(el) {
      let aliases;
      try { aliases = JSON.parse($("#pxAlias", el).value || "{}"); } catch (e) { aliases = { __invalid__: $("#pxAlias", el).value }; }
      return { enabled: $("#pxOn", el).checked, times: [0, 1, 2, 3, 4].map((i) => $(`#pxT${i}`, el).value.trim()),
        interval_seconds: +$("#pxInt", el).value, cache_mb: +$("#pxCache", el).value, search_cooldown_seconds: +$("#pxCd", el).value, aliases };
    },
    save(el, v) {
      if (v.aliases && v.aliases.__invalid__ !== undefined) throw new Error("标签映射不是合法的 JSON");
      return post("/api/pixiv", v);
    },
  },
  reminder: {
    label: "定时提醒",
    html: () => `<div class="panel panel-pad"><div class="row" style="margin-bottom:6px"><div class="grow"><div class="section-title" style="margin:0">定时提醒</div><div class="li-sub">群友可设置一次性 / 每天 / 每周提醒；各群还需在「功能权限」开启</div></div>${sw("rmOn")}</div>
        <div class="row" style="margin:10px 0 16px"><div class="grow"><div>自然语言识别</div><div class="li-sub">「明天早上八点提醒我…」这类说法交给模型理解</div></div>${sw("rmNl")}</div>
        <div class="form-grid" style="grid-template-columns:repeat(3,1fr)">
          ${field("每人最多进行中", numInput("rmMax", 1, 50), "1–50")}${field("创建冷却（秒）", numInput("rmCd", 5, 300), "5–300")}${field("离线补发窗口（分钟）", numInput("rmGrace", 1, 30), "1–30，超过则跳过")}
        </div></div>
      <div class="panel panel-pad"><div class="section-title">提醒统计</div><div class="row wrap" id="rmCounts" style="gap:18px"></div></div>`,
    async load(el) {
      const d = await api("/api/reminder");
      const s = d.settings;
      $("#rmOn", el).checked = s.enabled; $("#rmNl", el).checked = s.natural_language;
      $("#rmMax", el).value = s.max_active; $("#rmCd", el).value = s.cooldown_seconds; $("#rmGrace", el).value = s.grace_minutes;
      const L = { active: ["进行中", "var(--green)"], sending: ["发送中", "var(--accent)"], done: ["已完成（近 30 天）", "var(--blue)"], cancelled: ["已取消", "var(--muted)"] };
      $("#rmCounts", el).innerHTML = d.counts._missing ? '<span class="dim">机器人首次运行后显示</span>' : Object.entries(L).map(([k, [n, c]]) => `<div><div class="c-cap">${n}</div><div style="font-size:22px;font-weight:700;color:${c}">${d.counts[k] || 0}</div></div>`).join("");
      this._orig = s;
    },
    value(el) {
      return { ...(this._orig || {}), enabled: $("#rmOn", el).checked, natural_language: $("#rmNl", el).checked, max_active: +$("#rmMax", el).value, cooldown_seconds: +$("#rmCd", el).value, grace_minutes: +$("#rmGrace", el).value };
    },
    save: (el, v) => post("/api/reminder", v),
  },
  jm: {
    label: "JM 下载",
    html: () => `<div class="panel panel-pad"><div class="row" style="margin-bottom:6px"><div class="grow"><div class="section-title" style="margin:0">JM 下载</div><div class="li-sub">群友发「/jm 车号」，机器人下载整本并合成 PDF，以群文件形式发到群里；各群还需在「功能权限」开启 JM 下载</div></div>${sw("jmOn")}</div>
        <div class="form-grid" style="margin-top:14px">
          ${field("缓存目录", '<input class="input mono" id="jmDir" placeholder="留空用 data/jm">', "下载图片与 PDF 的临时存放位置；群文件发出去后即自动删除", "full")}
          ${field("代理", '<input class="input mono" id="jmProxy" placeholder="127.0.0.1:7890（留空走系统代理）">')}
          ${field("单本超时（秒）", numInput("jmTimeout", 30, 7200), "30–7200，下载 + 合成 PDF 的总时限")}
          ${field("页数上限", numInput("jmMax", 0, 100000), "0 = 不限制；超过直接拒绝")}
        </div>
        <div class="form-grid" style="grid-template-columns:repeat(3,1fr);margin-top:14px">
          ${field("每人每日上限", numInput("jmUser", 0, 1000), "0 = 不限制")}
          ${field("每群每日上限", numInput("jmGroup", 0, 10000), "0 = 不限制")}
          ${field("进度提示间隔（秒）", numInput("jmInt", 5, 3600), "下载中每隔多久报一次页数")}
        </div></div>
      <div class="panel panel-pad"><div class="row" style="margin-bottom:10px"><div class="grow"><div class="section-title" style="margin:0">缓存概况<span class="hint">每本发完即删，这里通常应该是空的；有残留时可手动清掉</span></div><div class="li-sub" id="jmStat">-</div></div>
          <button class="btn sm" id="jmReload">${icon("refresh")}刷新</button><button class="btn sm danger" id="jmWipe">${icon("trash")}清空缓存</button></div>
        <div id="jmRows"></div></div>`,
    _cache: null,
    async load(el) {
      const d = await api("/api/jm");
      const s = d.settings;
      $("#jmOn", el).checked = String(s.JM_ENABLED).toLowerCase() === "true";
      $("#jmDir", el).value = s.JM_CACHE_DIR || "";
      $("#jmProxy", el).value = s.JM_PROXY || "";
      $("#jmTimeout", el).value = s.JM_TIMEOUT; $("#jmMax", el).value = s.JM_MAX_IMAGES;
      $("#jmUser", el).value = s.JM_DAILY_USER_LIMIT;
      $("#jmGroup", el).value = s.JM_DAILY_GROUP_LIMIT; $("#jmInt", el).value = s.JM_PROGRESS_INTERVAL;
      this.render(el, d.cache);
      $("#jmReload", el).onclick = async () => { try { this.render(el, (await api("/api/jm")).cache); toast("已刷新", "success"); } catch (e) { toastErr(e); } };
      $("#jmWipe", el).onclick = async () => {
        if (!(await confirmBox("清空 JM 缓存", "删除缓存目录下所有已下载的本子与 PDF？\n下次点播同一本会重新下载。", { ok: "清空", danger: true }))) return;
        try { const r = await api("/api/jm/cache", { method: "DELETE" }); this.render(el, r.cache); toast(`已清空 ${r.removed} 本缓存`, "success"); } catch (e) { toastErr(e); }
      };
    },
    render(el, cache) {
      this._cache = cache || { albums: [], count: 0, with_pdf: 0, used_mb: 0, dir: "" };
      const c = this._cache;
      $("#jmStat", el).innerHTML = `${c.count} 本（${c.with_pdf} 本有 PDF），占用 <b>${c.used_mb}</b> MB<span class="dim mono" style="margin-left:10px">${esc(c.dir)}</span>`;
      const host = $("#jmRows", el);
      host.innerHTML = c.albums.length ? `<table class="job-table">${c.albums.slice(0, 60).map((a) => `<tr>
          <td class="mono">JM${esc(a.album_id)}</td><td>${a.size_mb} MB</td>
          <td><span class="tag ${a.has_pdf ? "ok" : "warn"}">${a.has_pdf ? "已生成 PDF" : "仅图片"}</span></td>
          <td class="dim">${esc(a.updated)}</td>
          <td style="text-align:right"><button class="btn sm ghost" data-rm="${esc(a.album_id)}">删除</button></td></tr>`).join("")}</table>`
        : '<div class="empty">缓存是空的，群里发一次 /jm 车号 就会出现在这里</div>';
      host.onclick = async (e) => {
        const b = e.target.closest("[data-rm]");
        if (!b) return;
        if (!(await confirmBox("删除缓存", `删除 JM${b.dataset.rm} 的图片与 PDF？`, { ok: "删除", danger: true }))) return;
        try { const r = await api(`/api/jm/cache/${b.dataset.rm}`, { method: "DELETE" }); this.render(el, r.cache); toast("已删除", "success"); } catch (err) { toastErr(err); }
      };
    },
    value(el) {
      return {
        enabled: $("#jmOn", el).checked,
        cache_dir: $("#jmDir", el).value.trim(),
        proxy: $("#jmProxy", el).value.trim(),
        timeout: +$("#jmTimeout", el).value,
        max_images: +$("#jmMax", el).value,
        daily_user_limit: +$("#jmUser", el).value,
        daily_group_limit: +$("#jmGroup", el).value,
        progress_interval: +$("#jmInt", el).value,
      };
    },
    save: (el, v) => post("/api/jm", v),
  },
  miyoushe_sign: {
    label: "GsCore 游戏服务",
    html: () => `<div class="panel panel-pad"><h2>官方游戏管理</h2><p>米游社签到与账号管理已由 GsCore 接管。原有自制扫码、签到设置和卡片不再运行。</p><button class="btn primary" data-go="games">打开游戏管理</button></div>`,
  },
  appearance: {
    label: "外观",
    html: () => `<div class="panel panel-pad"><div class="row wrap" style="margin-bottom:12px"><div class="grow"><div class="section-title" style="margin:0">壁纸库</div><div class="li-sub">支持图片与视频；选中若干张后「设为轮播」。轮播列表为空时显示默认主视觉</div></div>
          <span class="muted">轮播间隔</span>${numInput("wpInt", 3, 3600, 90)}<span class="muted">秒</span></div>
        <div class="row wrap" style="margin-bottom:12px"><button class="btn primary sm" id="wpAdd">${icon("upload")}添加壁纸</button>
          <button class="btn sm" id="wpApply">设为轮播</button><button class="btn sm" id="wpAll">轮播全部</button><button class="btn sm" id="wpDefault">恢复默认主视觉</button>
          <span class="grow"></span><span class="muted" id="wpSel"></span><button class="btn sm danger" id="wpDel">${icon("trash")}从库中移除</button>
          <input type="file" id="wpFile" multiple accept="image/*,video/*" style="display:none"></div>
        <div class="wp-grid" id="wpGrid"></div></div>`,
    sel: new Set(),
    async load(el) {
      const d = await api("/api/wallpapers");
      $("#wpInt", el).value = d.interval_seconds;
      const grid = $("#wpGrid", el);
      grid.innerHTML = d.items.length ? d.items.map((w) => `<div class="wp ${this.sel.has(w.id) ? "sel" : ""}" data-id="${w.id}" title="${esc(w.name)}" style="${w.kind === "image" ? `background-image:url('/media/thumb/${w.id}')` : ""}">
          ${w.kind === "video" ? `<video src="/media/wp/${w.id}#t=1" muted preload="metadata"></video>` : ""}
          ${w.kind === "video" ? '<span class="tag wp-tag">视频</span>' : ""}${w.playing ? `<span class="wp-play">${icon("play")}</span>` : ""}</div>`).join("")
        : '<div class="empty" style="grid-column:1/-1">壁纸库是空的，点「添加壁纸」上传图片或视频</div>';
      const upd = () => { $("#wpSel", el).textContent = this.sel.size ? `已选 ${this.sel.size} 张` : ""; };
      upd();
      grid.onclick = (e) => { const w = e.target.closest("[data-id]"); if (!w) return; this.sel.has(w.dataset.id) ? this.sel.delete(w.dataset.id) : this.sel.add(w.dataset.id); w.classList.toggle("sel"); upd(); };
      const after = async (msg) => { if (msg) toast(msg, "success"); await bg.loadLibrary(); this.load(el); };
      $("#wpAdd", el).onclick = () => $("#wpFile", el).click();
      $("#wpFile", el).onchange = async (e) => {
        const files = [...e.target.files]; e.target.value = "";
        let ok = 0;
        for (const f of files) {
          toast(`正在上传 ${f.name}…`, "info", 2000);
          try { await api("/api/wallpapers/upload", { method: "POST", raw: f, headers: { "X-Filename": encodeURIComponent(f.name), "Content-Type": "application/octet-stream" } }); ok++; }
          catch (err) { toastErr(new Error(`${f.name}：${err.message}`)); }
        }
        if (ok) after(`已添加 ${ok} 张壁纸`);
      };
      $("#wpApply", el).onclick = async () => {
        if (!this.sel.size) return toast("先点选要轮播的壁纸", "warn");
        const ids = d.items.map((w) => w.id).filter((id) => this.sel.has(id));
        await post("/api/wallpapers/playlist", { ids }); this.sel.clear();
        after(ids.length > 1 ? `已设为 ${ids.length} 张轮播` : "已应用这张壁纸");
      };
      $("#wpAll", el).onclick = async () => { await post("/api/wallpapers/playlist", { ids: d.items.map((w) => w.id) }); after("已轮播全部壁纸"); };
      $("#wpDefault", el).onclick = async () => { await post("/api/wallpapers/playlist", { ids: [] }); after("已恢复默认主视觉"); };
      $("#wpDel", el).onclick = async () => {
        if (!this.sel.size) return toast("先点选要移除的壁纸", "warn");
        if (!(await confirmBox("移除壁纸", `从壁纸库移除 ${this.sel.size} 张？\n面板上传的文件会一并删除；旧版壁纸目录里的原图保留不动。`, { ok: "移除", danger: true }))) return;
        await post("/api/wallpapers/remove", { ids: [...this.sel] }); this.sel.clear(); after("已移除");
      };
      $("#wpInt", el).onchange = async (e) => { const v = +e.target.value; if (v >= 3 && v <= 3600) { await post("/api/wallpapers/interval", { seconds: v }); after(`轮播间隔已改为 ${v} 秒`); } };
    },
  },
};

// ================= B站生活 =================
const BILI_STATE = { idle: ["摸鱼中", ""], browsing: ["在找视频", "info"], watching: ["正在看", "ok"], thinking: ["在回味", "warn"], acting: ["在动手", "warn"] };
const BILI_KIND = { comment: ["评论", "info"], reply: ["回复", "info"], danmaku: ["弹幕", "ok"], dynamic: ["动态", "warn"] };
const BILI_ACT = { like: "点赞", danmaku: "弹幕", comment: "评论", share: "分享到群" };
const BILI_FEED_C = { start: "var(--green)", end: "var(--dim)", watch: "var(--blue)", thought: "var(--purple)", like: "#fb7299", danmaku: "#8ff0c2", comment: "var(--blue)", reply: "#6ad4ff", dynamic: "var(--accent)", share: "#fb7299", slang: "var(--accent)", error: "var(--red)", result: "var(--muted)", info: "var(--muted)" };
const biliCover = (u, w = 336, h = 190) => u ? esc(String(u).replace(/^http:/, "https:").replace(/^\/\//, "https://")) + `@${w}w_${h}h_1c.webp` : "";
const biliAgo = (ts, now) => {
  const s = Math.max(0, (now || Date.now() / 1000) - (ts || 0));
  if (s < 60) return "刚刚"; if (s < 3600) return `${Math.floor(s / 60)} 分钟前`; if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
};
const biliTime = (ts) => new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });
const biliOpen = (url) => post("/api/open/url", { url }).catch(toastErr);

pages.bili = {
  timer: null, tab: "watched", d: null, loginTimer: null,
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>B站生活</h1><span class="sub" id="blAcct">加载中…</span><span class="grow"></span>
        <div class="row" id="blActs">
          <input class="input" id="blKw" placeholder="想让她搜什么（可留空）" style="width:190px">
          <button class="btn primary" id="blSurf">${icon("play")}刷一下</button>
          <button class="btn" id="blDyn">${icon("send")}发动态</button>
          <button class="btn" id="blReply" title="看一眼评论区有没有人回她，想回就回">${icon("chat")}看回复</button>
          <button class="btn" id="blPause"></button>
          <button class="btn ghost" id="blLogin"></button>
        </div></div>
      <div class="scroll grow bl-body">
        <div class="bl-top">
          <div class="panel panel-pad bl-live" id="blLive"></div>
          <div class="panel panel-pad bl-feed-wrap"><div class="section-title">动态流<span class="hint">她刷 B 站时的每一步，3 秒刷新</span></div><div class="bl-feed" id="blFeed"></div></div>
        </div>
        <div class="bl-stats" id="blStats"></div>
        <div class="panel panel-pad">
          <div class="row" style="margin-bottom:12px"><div class="seg" id="blTabs">
            <button data-t="watched" class="on">看过的视频</button><button data-t="posts">发过的</button><button data-t="slang">梗库与热搜</button></div>
            <span class="grow"></span><span class="muted" id="blTabHint"></span></div>
          <div id="blTab"></div>
        </div>
      </div>`;
    $("#blTabs", el).onclick = (e) => { const b = e.target.closest("[data-t]"); if (!b) return; this.tab = b.dataset.t; $$("#blTabs button", el).forEach((x) => x.classList.toggle("on", x === b)); this.renderTab(); };
    $("#blSurf", el).onclick = () => this.act("surf", { keyword: $("#blKw", el).value.trim() });
    $("#blDyn", el).onclick = async () => { if (await confirmBox("发动态", "让她按今天的经历想一条动态（她也可能想了想决定不发）。", { ok: "去想" })) this.act("dynamic"); };
    $("#blReply", el).onclick = () => this.act("replies");
    $("#blPause", el).onclick = () => this.act(this.d && this.d.status.halted ? "resume" : "pause");
    $("#blLogin", el).onclick = async () => {
      if (this.d && this.d.status.logged_in) {
        if (await confirmBox("退出 B 站登录", "会删除小号的登录信息（Cookie），之后要重新扫码。", { ok: "退出登录", danger: true })) this.act("logout");
      } else this.login();
    };
    el.addEventListener("click", (e) => {
      const o = e.target.closest("[data-open]"); if (o) { e.preventDefault(); biliOpen(o.dataset.open); return; }
      const del = e.target.closest("[data-del-slang]");
      if (del) post("/api/bili/slang/delete", { term: del.dataset.delSlang }).then(() => { toast("已删除"); this.refresh(); }).catch(toastErr);
      if (e.target.closest("#blCancelReq")) api("/api/bili/action", { method: "DELETE" }).then(() => this.refresh());
    });
  },
  show() { this.refresh(); },
  hide() { clearTimeout(this.timer); this.timer = null; },
  async act(action, extra = {}) {
    try {
      const r = await post("/api/bili/action", { action, ...extra });
      const msg = { surf: "已经叫她去刷了，机器人 10 秒内会开始", dynamic: "已经叫她去想动态了", replies: "已经叫她去看消息了", pause: "已暂停", resume: "已恢复", logout: "已退出登录" }[action];
      toast(msg || "好", r.request ? "info" : "success");
      if (action === "surf") $("#blKw").value = "";
      this.refresh();
    } catch (e) { toastErr(e); }
  },
  async refresh() {
    clearTimeout(this.timer);
    try { this.d = await api("/api/bili"); this.render(); } catch (e) { $("#blAcct").textContent = "读取失败：" + e.message; }
    if (current === "bili") this.timer = setTimeout(() => this.refresh(), document.hidden ? 9000 : 3000);
  },
  render() {
    const d = this.d, st = d.status, running = S.overview && S.overview.state && (S.overview.state.running || S.overview.state.nonebot);
    $("#blAcct").innerHTML = st.logged_in
      ? `<span class="tag ${st.invalid ? "err" : "ok"}">${st.invalid ? "登录可能失效" : "已登录"}</span> ${esc(st.uname || "小号")}${st.mid ? ` · <a href="#" data-open="https://space.bilibili.com/${esc(st.mid)}">主页</a>` : ""}`
        + (st.halted ? ` <span class="tag warn">暂停中：${esc(st.halt_reason)}</span>` : "") + (d.dry_run ? ' <span class="tag info">演练模式</span>' : "") + (!d.enabled ? ' <span class="tag">自动刷已关</span>' : "")
        + (running === false ? ' <span class="tag err">机器人没在运行</span>' : "")
      : `<span class="tag err">未登录</span> 点右边「扫码登录」用小号扫码`;
    $("#blPause").innerHTML = st.halted ? `${icon("play")}恢复` : `${icon("stop")}暂停`;
    $("#blLogin").innerHTML = st.logged_in ? "退出登录" : `${icon("plus")}扫码登录`;
    $("#blLogin").className = st.logged_in ? "btn ghost" : "btn primary";
    $("#blSurf").disabled = $("#blDyn").disabled = $("#blReply").disabled = !st.logged_in;
    this.renderLive(); this.renderFeed(); this.renderStats(); this.renderTab();
  },
  renderLive() {
    const d = this.d, lv = d.live || {}, v = lv.video, [label, cls] = BILI_STATE[lv.state] || BILI_STATE.idle;
    const busy = lv.state && lv.state !== "idle";
    const req = d.pending ? `<div class="row" style="margin-top:10px"><span class="tag warn">等机器人领取：${{ surf: "刷一下", dynamic: "发动态", replies: "看回复", trending: "取热搜" }[d.pending.action] || d.pending.action}${d.pending.keyword ? `「${esc(d.pending.keyword)}」` : ""} · ${biliAgo(d.pending.ts, d.now)}</span><button class="btn sm ghost" id="blCancelReq">撤销</button></div>` : "";
    if (busy && v) {
      $("#blLive").innerHTML = `<div class="bl-now">
          <a href="#" data-open="https://www.bilibili.com/video/${esc(v.bvid)}" class="bl-cover big">${v.pic ? `<img src="${biliCover(v.pic, 480, 270)}" referrerpolicy="no-referrer" alt="">` : ""}<span class="bl-dur">${v.duration ? `${Math.floor(v.duration / 60)}:${String(v.duration % 60).padStart(2, "0")}` : ""}</span></a>
          <div class="bl-now-main"><div class="row"><span class="bl-pulse"></span><span class="tag ${cls}">${label}</span><span class="muted">${esc(v.source || "")}</span></div>
            <a href="#" class="bl-title" data-open="https://www.bilibili.com/video/${esc(v.bvid)}">${esc(v.title || v.bvid)}</a>
            <div class="muted">UP主：${esc(v.up || "-")}${v.tname ? ` · ${esc(v.tname)}` : ""}</div>
            <div class="bl-step">${esc(lv.step || "")}</div></div></div>${req}`;
    } else if (busy) {
      $("#blLive").innerHTML = `<div class="bl-idle"><span class="bl-pulse"></span><div><div class="login-state">${label}</div><div class="muted">${esc(lv.step || "")}</div></div></div>${req}`;
    } else {
      const last = (d.watched || [])[0];
      $("#blLive").innerHTML = `<div class="bl-idle"><div class="bl-zzz">💤</div><div><div class="login-state">没在刷</div>
          <div class="muted">${last ? `上次在 ${biliAgo(last.ts, d.now)} 看了《${esc(last.title)}》` : "还没刷过视频"} · 自动时段 ${esc(d.hours)}</div>
          <div class="muted" style="margin-top:6px">点右上角「刷一下」让她现在就去；输入关键词可以指定她搜什么。</div></div></div>${req}`;
    }
  },
  renderFeed() {
    const a = this.d.activity || [];
    $("#blFeed").innerHTML = a.length ? a.map((x) => `<div class="bl-ev"><i style="background:${BILI_FEED_C[x.kind] || "var(--muted)"}"></i><span class="mono muted">${biliTime(x.ts)}</span><span class="bl-ev-t">${esc(x.text)}</span></div>`).join("")
      : '<div class="empty">还没有动静</div>';
  },
  renderStats() {
    const t = this.d.status.today || {}, c = this.d.caps;
    const items = [["rounds", "刷了几轮", "#fb7299"], ["likes", "点赞", "#f472b6"], ["danmaku", "弹幕", "#3ecf8e"], ["comments", "评论", "#6ad4ff"], ["replies", "回复", "#a78bfa"], ["dynamics", "动态", "#ffd166"]];
    $("#blStats").innerHTML = items.map(([k, label, col]) => `<div class="panel info-tile" style="--c:${col}"><div class="cap">今天 · ${label}</div>
      <div class="val"><b style="font-size:20px">${t[k] || 0}</b> <span class="muted">${c[k] > 0 ? `/ ${c[k]}` : "· 不限"}</span></div></div>`).join("");
  },
  renderTab() {
    const d = this.d; if (!d) return;
    const box = $("#blTab");
    if (this.tab === "watched") {
      const w = d.watched || [];
      $("#blTabHint").textContent = `最近 ${w.length} 个`;
      box.innerHTML = w.length ? `<div class="bl-grid">${w.map((x) => {
        const sc = x.interest >= 7 ? "ok" : x.interest >= 4 ? "warn" : "";
        return `<div class="bl-card"><a href="#" data-open="https://www.bilibili.com/video/${esc(x.bvid)}" class="bl-cover">${x.pic ? `<img src="${biliCover(x.pic)}" referrerpolicy="no-referrer" loading="lazy" alt="">` : ""}<span class="tag ${sc} bl-score">${x.interest} 分</span></a>
          <a href="#" class="bl-title sm" data-open="https://www.bilibili.com/video/${esc(x.bvid)}">${esc(x.title)}</a>
          <div class="muted bl-meta">${esc(x.up || "")} · ${biliAgo(x.ts, d.now)}</div>
          ${x.thought ? `<div class="bl-thought">“${esc(x.thought)}”</div>` : ""}
          ${(x.acts || []).length ? `<div class="row wrap" style="gap:4px">${x.acts.map((a) => `<span class="tag info">${BILI_ACT[a] || a}</span>`).join("")}</div>` : ""}</div>`;
      }).join("")}</div>` : '<div class="empty">还没刷过视频</div>';
    } else if (this.tab === "posts") {
      const p = d.posts || [];
      $("#blTabHint").textContent = `最近 ${p.length} 条`;
      box.innerHTML = p.length ? p.map((x) => {
        const [k, cls] = BILI_KIND[x.kind] || [x.kind, ""];
        return `<div class="bl-post"><span class="tag ${cls}">${k}</span><div class="grow"><div class="bl-post-t selectable">${esc(x.text)}</div>
          ${x.kind === "reply" && x.their ? `<div class="muted bl-their">回 ${esc(x.to || "某人")}：“${esc(x.their)}”</div>` : ""}
          <div class="muted">${x.title ? `《${esc(x.title)}》${x.kind === "danmaku" && x.second != null ? ` ${Math.floor(x.second / 60)}:${String(x.second % 60).padStart(2, "0")}` : ""} · ` : ""}${biliAgo(x.ts, d.now)}</div></div>
          ${x.url ? `<button class="btn sm ghost" data-open="${esc(x.url)}">去看看</button>` : ""}</div>`;
      }).join("") : '<div class="empty">还没发过评论、回复、弹幕或动态</div>';
    } else {
      const tr = d.trending || {}, l = d.learned || [];
      $("#blTabHint").textContent = `内置 ${d.builtin_count} 个常用梗 · 学会 ${l.length} 个`;
      box.innerHTML = `<div class="section-title">今天 B 站热搜<span class="hint">${tr.ts ? `${biliAgo(tr.ts, d.now)}更新 · 每 3 小时取一次` : "机器人启动约 1 分钟后取第一次"}</span></div>
        <div class="row wrap" style="gap:6px;margin-bottom:16px">${(tr.words || []).map((w) => `<span class="tag">${esc(w)}</span>`).join("") || '<span class="muted">暂无</span>'}</div>
        <div class="section-title">她学会的新梗<span class="hint">刷视频时从弹幕和评论里学的，聊天时能看懂也会用；不对的可以删</span></div>
        ${l.length ? `<div class="bl-slang">${l.map((x) => `<div class="bl-slang-i"><b>${esc(x.term)}</b><span class="grow muted">${esc(x.meaning)}</span><span class="muted">${biliAgo(x.ts, d.now)}</span><button class="btn sm ghost" data-del-slang="${esc(x.term)}">${icon("trash")}</button></div>`).join("")}</div>` : '<div class="muted" style="margin-bottom:16px">还没学到新梗</div>'}
        <div class="section-title" style="margin-top:16px">各群的群梗<span class="hint">好几个群友都在说的短句，她也会跟着用</span></div>
        ${(d.group_memes || []).length ? d.group_memes.map((g) => `<div class="row wrap" style="gap:6px;margin-bottom:8px"><span class="tag info">群 ${esc(g.group)}</span>${g.memes.map((m) => `<span class="tag">${esc(m)}</span>`).join("")}</div>`).join("") : '<div class="muted">还没攒出群梗（≥3 个人说过同一句才算）</div>'}`;
    }
  },
  async login() {
    clearTimeout(this.loginTimer);
    let r;
    try { r = await post("/api/bili/login"); } catch (e) { return toastErr(e); }
    const m = modal.open(`<div class="modal-head"><h2>扫码登录 B 站小号</h2></div>
      <div class="modal-body"><div class="row" style="gap:18px;align-items:flex-start"><div class="qr" style="background-image:url('${r.qr}')"></div>
        <div><div class="login-state" id="blQrState">等待扫码</div><div class="muted" style="margin-top:8px">用 <b>B 站小号</b>（别用大号）的手机 App 扫码并确认。<br>二维码 3 分钟内有效；登录信息只存在本机数据库里。</div></div></div></div>
      <div class="modal-foot"><button class="btn" data-x="close">关闭</button></div>`, { onClose: () => clearTimeout(this.loginTimer) });
    m.querySelector("[data-x=close]").onclick = () => modal.close();
    const poll = async () => {
      let s;
      try { s = await api("/api/bili/login"); } catch (e) { $("#blQrState").textContent = e.message; return; }
      const el = $("#blQrState"); if (!el) return;
      if (s.state === "ok") { el.textContent = `登录成功：${s.uname || "小号"}`; toast("B 站小号登录成功", "success"); setTimeout(() => modal.close(), 1200); this.refresh(); return; }
      if (s.state === "expired") { el.textContent = "二维码过期了，关掉重新点「扫码登录」"; return; }
      el.textContent = s.state === "scanned" ? "已扫码，请在手机上确认" : "等待扫码";
      this.loginTimer = setTimeout(poll, 2000);
    };
    this.loginTimer = setTimeout(poll, 2000);
  },
};

// ================= 设置：活人感 / 语音 / B站生活（字段定义在 panel/services/huoren_settings.py） =================
function envTab(name, label) {
  const id = (k) => `eg_${k}`;
  return {
    label,
    html: () => `<div class="panel panel-pad" id="eg-${name}"><div class="empty">加载中…</div></div>`,
    async load(el) {
      const d = await api(`/api/envgroup/${name}`);
      this._fields = d.fields;
      $(`#eg-${name}`, el).innerHTML = `<div class="section-title" style="margin-bottom:4px">${esc(d.title)}<span class="hint">${esc(d.desc)}</span></div>` + d.fields.map((f) => {
        let ctl;
        if (f.type === "bool") ctl = sw(id(f.key));
        else if (f.type === "text") ctl = `<input class="input" id="${id(f.key)}" style="width:340px">`;
        else {
          const pct = f.type === "pct", step = f.type === "int" ? 1 : pct ? 0.1 : Math.pow(10, -(f.decimals || 1));
          ctl = `<div class="row" style="gap:6px"><input class="input" type="number" id="${id(f.key)}" step="${step}" ${pct ? 'min="0" max="100"' : `${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""}`} style="width:120px"><span class="muted">${pct ? "%" : esc(f.suffix || "")}</span></div>`;
        }
        return `<div class="row eg-row"><div class="grow"><div>${esc(f.label)}</div>${f.help ? `<div class="li-sub">${esc(f.help)}</div>` : ""}</div>${ctl}</div>`;
      }).join("");
      for (const f of d.fields) {
        const inp = $(`#${id(f.key)}`, el);
        if (f.type === "bool") inp.checked = !!f.value;
        else if (f.type === "pct") inp.value = Math.round(Number(f.value) * 1000) / 10;
        else inp.value = f.value;
      }
    },
    value(el) {
      const out = {};
      for (const f of this._fields || []) {
        const inp = $(`#${id(f.key)}`, el); if (!inp) continue;
        if (f.type === "bool") out[f.key] = inp.checked;
        else if (f.type === "text") out[f.key] = inp.value.trim();
        else if (f.type === "pct") out[f.key] = inp.value === "" ? "" : Math.round(Number(inp.value) * 10) / 1000;
        else out[f.key] = inp.value === "" ? "" : Number(inp.value);
      }
      return out;
    },
    save(el, v) { return post(`/api/envgroup/${name}`, { values: v }); },
  };
}
{ const ap = SETTINGS_TABS.appearance; delete SETTINGS_TABS.appearance;
  Object.assign(SETTINGS_TABS, { huoren: envTab("huoren", "活人感"), voice: envTab("voice", "语音"), bili: envTab("bili", "B站生活"), websearch: envTab("websearch", "联网搜索") });
  if (ap) SETTINGS_TABS.appearance = ap; }

pages.settings = {
  tab: "accounts", baseline: {},
  mount(el) {
    el.innerHTML = `<div class="page-head"><h1>设置</h1><span class="sub">修改后点「保存」，机器人热加载，无需重启</span></div>
      <div class="tabs" id="stTabs">${Object.entries(SETTINGS_TABS).map(([k, t]) => `<button class="tab ${k === this.tab ? "on" : ""}" data-t="${k}">${t.label}</button>`).join("")}</div>
      <div class="scroll grow" id="stPane" style="display:flex;flex-direction:column;gap:14px"></div>
      <div class="save-bar" id="stBar"><span class="muted grow" id="stHint"></span><button class="btn" id="stReset">放弃修改</button><button class="btn primary" id="stSave">${icon("save")}保存</button></div>`;
    $("#stTabs").onclick = async (e) => { const b = e.target.closest("[data-t]"); if (!b || b.dataset.t === this.tab) return; if (!(await this.leave())) return; this.tab = b.dataset.t; this.show(); };
    $("#stPane").addEventListener("input", () => this.check());
    $("#stPane").addEventListener("change", () => this.check());
    $("#stReset").onclick = () => this.show();
    $("#stSave").onclick = () => this.save();
  },
  async show() {
    $$("#stTabs .tab").forEach((x) => x.classList.toggle("on", x.dataset.t === this.tab));
    const t = SETTINGS_TABS[this.tab], pane = $("#stPane");
    pane.innerHTML = t.html(); pane.scrollTop = 0;
    $("#stBar").style.display = t.save ? "" : "none";
    try { await t.load.call(t, pane); } catch (e) { toastErr(e); }
    this.baseline = t.value ? JSON.stringify(t.value.call(t, pane)) : "";
    this.check();
  },
  dirty() { const t = SETTINGS_TABS[this.tab]; return !!(t.value && t.save) && JSON.stringify(t.value.call(t, $("#stPane"))) !== this.baseline; },
  check() {
    const d = this.dirty();
    $("#stHint").textContent = d ? "有未保存的修改" : "已是最新";
    $("#stHint").style.color = d ? "var(--accent)" : "";
    $("#stSave").disabled = !d;
  },
  async save() {
    const t = SETTINGS_TABS[this.tab], pane = $("#stPane");
    try { await t.save.call(t, pane, t.value.call(t, pane)); toast(`${t.label}已保存，机器人会自动热加载`, "success"); await this.show(); return true; }
    catch (e) { toastErr(e); return false; }
  },
  async leave() {
    if (!this.dirty()) return true;
    const r = await confirmBox("未保存的修改", `「${SETTINGS_TABS[this.tab].label}」有修改还没保存。`, { ok: "保存", third: "放弃修改" });
    if (r === true) return this.save();
    return r === "third";
  },
};

// ================= 启动 =================
(async function init() {
  buildRail();
  await pollOverview();
  if (S.overview) { bg.init(S.overview.backgrounds); }
  await bg.loadLibrary();
  schedule(pollOverview, 2000);
  schedule(pollEvents, 1500);
})();

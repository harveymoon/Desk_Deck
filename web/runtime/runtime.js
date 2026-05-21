// Desk_Deck — tablet runtime with title bar + overlays.

import { renderWidget, updateWidget } from "/shared/widgets.js";
import { connect } from "/shared/ws.js";
import { applyTheme, DEFAULT_THEME } from "/shared/theme.js";

const TOKEN_KEY = "dd.token";

function resolveToken() {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("t");
  if (fromUrl) {
    localStorage.setItem(TOKEN_KEY, fromUrl);
    url.searchParams.delete("t");
    history.replaceState(null, "", url.toString());
    return fromUrl;
  }
  return localStorage.getItem(TOKEN_KEY);
}

const token = resolveToken();
if (!token) {
  window.location.href = "/pair";
}

// Register service worker for "real app" feel
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/runtime-static/sw.js").catch(() => {});
}

// ───────── Fullscreen + Wake Lock + Orientation ─────────
// Android Chrome won't grant true PWA install on a plain-HTTP LAN origin,
// so "Add to home screen" hands you a shortcut that opens in a regular tab
// (Chrome top bar visible). The Fullscreen API works in *any* browser
// context — we trip it on the first user gesture and the chrome disappears.
let _fullscreenAttempted = false;
let _wakeLock = null;

async function goFullscreen() {
  if (_fullscreenAttempted) return;
  if (document.fullscreenElement) { _fullscreenAttempted = true; return; }
  const el = document.documentElement;
  const req = el.requestFullscreen || el.webkitRequestFullscreen || el.mozRequestFullScreen;
  if (!req) return;
  try {
    await req.call(el, { navigationUI: "hide" });
    _fullscreenAttempted = true;
  } catch (e) {
    // Some browsers reject without a recent gesture; we'll try again on the next pointerdown.
  }
}

async function lockOrientationLandscape() {
  try { await screen.orientation.lock("landscape"); } catch {}
}

async function requestWakeLock() {
  if (!("wakeLock" in navigator)) return;
  try {
    _wakeLock = await navigator.wakeLock.request("screen");
    _wakeLock.addEventListener("release", () => { _wakeLock = null; });
  } catch {}
}

// First user tap fires all three. Subsequent taps re-request fullscreen only
// if the user exited (e.g. by pressing Back) and they tap something else.
document.addEventListener("pointerdown", async () => {
  await goFullscreen();
  await lockOrientationLandscape();
  await requestWakeLock();
}, { capture: true });

// When the tablet wakes from sleep / tab returns, re-acquire the wake lock
// (it auto-releases when the page is hidden).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") requestWakeLock();
});

// If the user manually exits fullscreen (Back / Esc), re-arm so the next
// tap takes them back into it.
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement) _fullscreenAttempted = false;
});

applyTheme(document.documentElement, DEFAULT_THEME);

const stage       = document.getElementById("stage");
const tbName      = document.getElementById("tb-name");
const tbSub       = document.getElementById("tb-sub");
const tbSource    = document.getElementById("tb-source");
const sbStatus    = document.getElementById("sb-status");
const sbStatusLbl = document.getElementById("sb-status-lbl");
const overlay     = document.getElementById("overlay");
const overlayTitle = document.getElementById("overlay-title");
const overlayBody  = document.getElementById("overlay-body");
const overlayClose = document.getElementById("overlay-close");
const overlayBack  = document.getElementById("overlay-back");

let currentLayout = null;
let widgetEls = new Map(); // widget id -> DOM element
let desktops = [];

function setStatus(text, cls = "") {
  sbStatusLbl.textContent = text;
  sbStatus.className = "dd-sb-status " + cls;
  sbStatus.title = text;
}

function renderLayout(layout, theme) {
  currentLayout = layout;
  widgetEls = new Map();
  stage.innerHTML = "";
  if (theme) applyTheme(document.documentElement, theme);

  const canvas = layout.canvas || {};
  const cw = canvas.width  || 1600;
  const ch = canvas.height || 1000;

  const vw = window.innerWidth - 112;
  const vh = window.innerHeight;
  const scale = Math.min(vw / cw, vh / ch);
  stage.style.width  = `${cw}px`;
  stage.style.height = `${ch}px`;
  stage.style.transform = `scale(${scale}) translate(${(vw - cw * scale) / 2 / scale}px, ${(vh - ch * scale) / 2 / scale}px)`;

  for (const w of layout.widgets || []) {
    const el = renderWidget(w, { onEvent: send });
    stage.appendChild(el);
    if (w.id) widgetEls.set(w.id, { el, widget: w });
  }
  // Top titlebar: big app context + focused window subhead
  const ctx = layout._context || {};
  const synth = ctx.synthetic;
  const matched = ctx.matched;
  let bigName = "—";
  let source = "";
  if (matched) {
    bigName = layout.name || "—";
    source = "matched";
  } else if (synth) {
    bigName = (ctx.process || "?").replace(/\.exe$/i, "").toUpperCase();
    source = "auto windows";
  } else if (ctx.process) {
    bigName = (ctx.process || "?").toUpperCase();
    source = "no config";
  } else {
    bigName = layout.name || "—";
    source = "";
  }
  tbName.textContent = bigName;
  tbSub.textContent = ctx.title || "";
  tbSource.textContent = source;
}

window.addEventListener("resize", () => {
  if (currentLayout) renderLayout(currentLayout, null);
});

const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live?t=${encodeURIComponent(token || "")}&device=tablet`;
const conn = connect({
  url: wsUrl,
  onOpen: () => setStatus("connected", "is-connected"),
  onClose: () => setStatus("reconnecting…"),
  onMessage: (msg) => {
    if (msg.t === "layout") {
      renderLayout(msg.layout, msg.theme);
    } else if (msg.t === "widget_update") {
      const entry = widgetEls.get(msg.id);
      if (entry) updateWidget(entry.el, entry.widget, msg.patch);
    } else if (msg.t === "theme_update") {
      applyTheme(document.documentElement, msg.theme);
    } else if (msg.t === "desktops") {
      desktops = msg.desktops || [];
      if (overlay && !overlay.hidden && overlay.dataset.kind === "spaces") {
        renderSpacesOverlay();
      }
    } else if (msg.t === "error") {
      setStatus(`error: ${msg.msg}`, "is-error");
    }
  },
});

function send(msg) { conn.send(msg); }

// ───────── Overlays ─────────

// Sidebar buttons are now driven by configs/_sidebar.yaml served at
// /api/sidebar. We rebuild the button strip on first load and after the
// user edits the sidebar in the editor (refresh via SSE or page reload).
async function loadSidebar() {
  let cfg;
  try {
    const r = await fetch("/api/sidebar?t=" + encodeURIComponent(token));
    cfg = await r.json();
  } catch (e) {
    console.error("sidebar fetch failed", e);
    return;
  }
  const host = document.getElementById("sb-buttons");
  host.innerHTML = "";
  for (const b of cfg.buttons || []) {
    const btn = document.createElement("button");
    btn.className = "dd-sb-menu";
    btn.dataset.id = b.id || "";
    btn.title = b.label || "";
    const g = document.createElement("span");
    g.className = "dd-sb-glyph";
    g.textContent = b.glyph || "•";
    btn.appendChild(g);
    const l = document.createElement("span");
    l.className = "dd-sb-lbl";
    l.textContent = b.label || "";
    btn.appendChild(l);
    btn.addEventListener("click", () => handleSidebarClick(b));
    host.appendChild(btn);
  }
}

function handleSidebarClick(b) {
  if (navigator.vibrate) navigator.vibrate(8);
  if (b.kind === "overlay") {
    openOverlay(b.target);
  } else if (b.kind === "action") {
    fetch("/api/action?t=" + encodeURIComponent(token), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: b.target || {} }),
    }).catch(() => {});
  } else {
    console.warn("unknown sidebar button kind:", b.kind);
  }
}

loadSidebar();
overlayClose.addEventListener("click", closeOverlay);

function openOverlay(kind) {
  overlay.dataset.kind = kind;
  overlay.hidden = false;
  // body.has-overlay drives the full-screen takeover (hides titlebar +
  // sidebar so the X button in the overlay head is the only way out).
  document.body.classList.add("has-overlay");
  overlayBody.innerHTML = "";
  overlayBody.classList.remove("is-bookmarks");
  overlayBody.classList.remove("is-winri");
  overlayBack.hidden = true;
  _appsDrill = null;
  stopWinriPoll();
  if (kind === "apps") {
    overlayTitle.textContent = "Apps";
    fetchApps();
  } else if (kind === "spaces") {
    overlayTitle.textContent = "Spaces — virtual desktops";
    fetchSpaces();
  } else if (kind === "bookmarks") {
    overlayTitle.textContent = "Favorites";
    fetchBookmarks();
  } else if (kind === "winri") {
    overlayTitle.textContent = "Winri — tiling controller";
    openWinri();
  }
}

function closeOverlay() {
  overlay.hidden = true;
  overlayBody.innerHTML = "";
  overlay.dataset.kind = "";
  document.body.classList.remove("has-overlay");
  overlayBack.hidden = true;
  _appsDrill = null;
  stopWinriPoll();
}

overlayBack.addEventListener("click", () => {
  _appsDrill = null;
  overlayBack.hidden = true;
  overlayTitle.textContent = "Apps";
  renderAppsView();
});

// Apps overlay state: top-level shows one tile per process; drilling in shows
// the windows / tabs of that one app. Cache survives overlay close/reopen
// so re-entry feels instant; we re-fetch in the background and silently
// re-render when fresh data arrives.
let _appsCache = { wins: [], tabs: null, chromeWindows: null, chromeAvailable: false, ts: 0 };
let _appsDrill = null;       // null = top-level; otherwise a process name
let _appsFetching = false;

async function fetchApps() {
  // If we have any cached data, paint it immediately and refresh in the
  // background. Otherwise show a loading hint and wait.
  if (_appsCache.wins && _appsCache.wins.length) {
    renderAppsView();
    refreshAppsAsync();
    return;
  }
  overlayBody.innerHTML = "<div class='dd-tile'>loading…</div>";
  await refreshAppsAsync();
}

async function refreshAppsAsync() {
  if (_appsFetching) return;
  _appsFetching = true;
  try {
    const [tabsResp, winsResp] = await Promise.allSettled([
      fetch("/api/chrome/tabs?t=" + encodeURIComponent(token)).then(r => r.json()),
      fetch("/api/windows?t=" + encodeURIComponent(token)).then(r => r.json()),
    ]);
    if (winsResp.status !== "fulfilled") {
      // If we never had cached data, surface the failure; otherwise stay quiet.
      if (!_appsCache.wins.length) {
        overlayBody.innerHTML = "<div class='dd-tile'>failed to load windows</div>";
      }
      return;
    }
    _appsCache = {
      wins: winsResp.value || [],
      tabs: (tabsResp.status === "fulfilled" && tabsResp.value.available) ? tabsResp.value.tabs : null,
      chromeWindows: (tabsResp.status === "fulfilled" && tabsResp.value.available) ? tabsResp.value.windows : null,
      chromeAvailable: tabsResp.status === "fulfilled" && tabsResp.value.available,
      ts: Date.now(),
    };
    // Only re-render if the user is still looking at the apps overlay.
    if (!overlay.hidden && overlay.dataset.kind === "apps") {
      renderAppsView();
    }
  } finally {
    _appsFetching = false;
  }
}

function renderAppsView() {
  overlayBody.innerHTML = "";
  if (_appsDrill) {
    overlayBack.hidden = false;
    renderAppsDrill(_appsDrill);
  } else {
    overlayBack.hidden = true;
    overlayTitle.textContent = "Apps";
    renderAppsRoot();
  }
}

function renderAppsRoot() {
  const wins = _appsCache.wins;
  if (!wins.length) {
    overlayBody.innerHTML = "<div class='dd-tile'>no visible windows</div>";
    return;
  }
  // Group by process (case-insensitive).
  const groups = new Map();
  for (const w of wins) {
    const key = (w.process || "(unknown)").toLowerCase();
    if (!groups.has(key)) {
      groups.set(key, { process: w.process || "(unknown)", icon: w.icon, windows: [], total: 0 });
    }
    const g = groups.get(key);
    g.windows.push(w);
    g.total++;
    if (!g.icon && w.icon) g.icon = w.icon;
  }

  // Sort: alphabetical.
  const list = Array.from(groups.values())
    .sort((a, b) => a.process.toLowerCase().localeCompare(b.process.toLowerCase()));

  for (const g of list) {
    // Chrome with CDP shows tab-count subtitle and drills into tabs.
    const isChrome = g.process.toLowerCase() === "chrome.exe";
    const tabCount = (isChrome && _appsCache.tabs) ? _appsCache.tabs.length : null;
    let meta;
    if (g.total === 1) {
      meta = g.windows[0].title;
    } else if (tabCount !== null) {
      meta = `${tabCount} tab${tabCount === 1 ? "" : "s"} · ${g.total} window${g.total === 1 ? "" : "s"}`;
    } else {
      meta = `${g.total} windows`;
    }

    const tile = buildTile({
      title: prettyAppName(g.process),
      meta,
      icon: g.icon,
    });
    tile.addEventListener("click", () => {
      // Single instance: focus directly.
      const needsDrill = g.total > 1 || (isChrome && tabCount !== null && tabCount > 1);
      if (!needsDrill) {
        send({ t: "focus_hwnd", hwnd: g.windows[0].hwnd });
        closeOverlay();
      } else {
        _appsDrill = g.process;
        renderAppsView();
      }
    });
    overlayBody.appendChild(tile);
  }
}

function renderAppsDrill(processName) {
  const isChrome = processName.toLowerCase() === "chrome.exe";
  const osWins = _appsCache.wins.filter(w => (w.process || "").toLowerCase() === processName.toLowerCase());

  // Chrome with CDP + window grouping → tree view
  if (isChrome && _appsCache.chromeWindows && _appsCache.chromeWindows.length) {
    const groups = _appsCache.chromeWindows;
    let totalTabs = 0;
    for (const g of groups) totalTabs += g.tabs.length;
    overlayTitle.textContent =
      `${prettyAppName(processName)} · ${totalTabs} tab${totalTabs === 1 ? "" : "s"} · ${groups.length} window${groups.length === 1 ? "" : "s"}`;

    groups.forEach((g, idx) => {
      const head = document.createElement("button");
      head.className = "dd-section-head dd-section-clickable";
      const firstTab = g.tabs[0];
      const label = firstTab ? truncate(firstTab.title, 80) : "(unnamed)";
      head.innerHTML =
        `<span class="dd-section-tag">Window ${idx + 1} · ${g.tabs.length} tab${g.tabs.length === 1 ? "" : "s"}</span>` +
        `<span class="dd-section-label">${escapeHtml(label)}</span>`;
      if (firstTab) {
        head.addEventListener("click", async () => {
          await fetch(`/api/chrome/activate/${encodeURIComponent(firstTab.id)}?t=${encodeURIComponent(token)}`, { method: "POST" });
          closeOverlay();
        });
      }
      overlayBody.appendChild(head);

      for (const tab of g.tabs) {
        const tile = buildTile({
          title: tab.title,
          meta: hostnameOf(tab.url),
          icon: chromeFavicon(tab.url),
          cls: "dd-tile-tab",
        });
        tile.addEventListener("click", async () => {
          await fetch(`/api/chrome/activate/${encodeURIComponent(tab.id)}?t=${encodeURIComponent(token)}`, { method: "POST" });
          closeOverlay();
        });
        overlayBody.appendChild(tile);
      }
    });
    return;
  }

  // Chrome with CDP but no grouping (older Chrome / WS failed) → flat tab list
  if (isChrome && _appsCache.tabs && _appsCache.tabs.length) {
    overlayTitle.textContent =
      `${prettyAppName(processName)} · ${_appsCache.tabs.length} tabs`;
    for (const tab of _appsCache.tabs) {
      const tile = buildTile({
        title: tab.title,
        meta: hostnameOf(tab.url),
        icon: chromeFavicon(tab.url),
        cls: "dd-tile-tab",
      });
      tile.addEventListener("click", async () => {
        await fetch(`/api/chrome/activate/${encodeURIComponent(tab.id)}?t=${encodeURIComponent(token)}`, { method: "POST" });
        closeOverlay();
      });
      overlayBody.appendChild(tile);
    }
    return;
  }

  // Fallback (no CDP) — per-window tiles
  overlayTitle.textContent = `${prettyAppName(processName)} · ${osWins.length} window${osWins.length === 1 ? "" : "s"}`;
  for (const w of osWins) {
    const tile = buildTile({ title: w.title, meta: w.process || "—", icon: w.icon });
    tile.addEventListener("click", () => {
      send({ t: "focus_hwnd", hwnd: w.hwnd });
      closeOverlay();
    });
    overlayBody.appendChild(tile);
  }
}

function truncate(s, n) { return (s && s.length > n) ? s.slice(0, n - 1) + "…" : (s || ""); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function prettyAppName(process) {
  const name = process.replace(/\.exe$/i, "");
  // CamelCase → "Camel Case" for nicer display
  return name.replace(/([a-z])([A-Z])/g, "$1 $2");
}

function buildTile({ title, meta, icon, cls = "" }) {
  const tile = document.createElement("button");
  tile.className = "dd-tile" + (cls ? " " + cls : "");
  const iconEl = document.createElement("div");
  iconEl.className = "dd-tile-icon";
  if (icon) {
    const img = document.createElement("img");
    img.src = icon;
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.addEventListener("error", () => { iconEl.classList.add("is-fallback"); iconEl.textContent = (title || "?").trim().slice(0, 1).toUpperCase(); });
    iconEl.appendChild(img);
  } else {
    iconEl.classList.add("is-fallback");
    iconEl.textContent = (title || "?").trim().slice(0, 1).toUpperCase();
  }
  tile.appendChild(iconEl);

  const body = document.createElement("div");
  body.className = "dd-tile-body";
  const t = document.createElement("div");
  t.className = "dd-tile-title";
  t.textContent = title;
  body.appendChild(t);
  const m = document.createElement("div");
  m.className = "dd-tile-meta";
  m.textContent = meta || "";
  body.appendChild(m);
  tile.appendChild(body);
  return tile;
}

// ───────── Winri overlay ─────────
let _winriPollTimer = null;
let _winriThumbTimer = null;
let _winriBuilt = false;     // controls vs strip are split so polls don't wipe scroll
let _winriStripEl = null;
let _winriStripHead = null;
let _overviewToggleEl = null;

async function openWinri() {
  overlayBody.classList.add("is-winri");
  overlayBody.innerHTML = "<div class='dd-winri-empty'>loading winri…</div>";
  _winriBuilt = false;
  _winriStripEl = null;
  _winriStripHead = null;
  await refreshWinri();
  _winriPollTimer = setInterval(() => {
    if (overlay.hidden || overlay.dataset.kind !== "winri") {
      stopWinriPoll(); return;
    }
    refreshWinri({ silent: true });
  }, 1500);
  // Refresh thumbnails on a slower cadence — they're heavier than the state poll
  _winriThumbTimer = setInterval(() => {
    if (overlay.hidden || overlay.dataset.kind !== "winri") {
      stopWinriPoll(); return;
    }
    refreshAllThumbnails();
  }, 12000);
}

function stopWinriPoll() {
  if (_winriPollTimer)  { clearInterval(_winriPollTimer);  _winriPollTimer  = null; }
  if (_winriThumbTimer) { clearInterval(_winriThumbTimer); _winriThumbTimer = null; }
}

function thumbnailUrl(wid, bust) {
  const q = bust ? `&v=${bust}` : "";
  return `/api/winri/thumbnail/${wid}?t=${encodeURIComponent(token)}&w=320${q}`;
}

function refreshAllThumbnails() {
  if (!_winriStripEl) return;
  const v = Date.now();
  for (const item of _winriStripEl.children) {
    const wid = item.dataset.wid;
    const img = item.querySelector("img.dd-winri-strip-thumb");
    if (img && wid) img.src = thumbnailUrl(wid, v);
  }
}

async function refreshWinri({ silent = false } = {}) {
  let state;
  try {
    const r = await fetch("/api/winri/state?t=" + encodeURIComponent(token));
    if (r.status === 503) {
      _winriBuilt = false;
      overlayBody.innerHTML =
        "<div class='dd-winri-empty'>" +
        "winri API not reachable on <code>127.0.0.1:47812</code>.<br>" +
        "Enable it in <code>%APPDATA%/winri/config.toml</code>:<br><br>" +
        "<code>[api]<br>enabled = true<br>port = 47812</code><br><br>" +
        "Then restart winri (Win+Esc, then re-launch)." +
        "</div>";
      return;
    }
    state = await r.json();
  } catch (e) {
    if (!silent) {
      overlayBody.innerHTML = `<div class='dd-winri-empty'>winri fetch failed: ${e}</div>`;
      _winriBuilt = false;
    }
    return;
  }

  if (!_winriBuilt) {
    buildWinriControls();
    _winriBuilt = true;
  }
  updateOverviewToggle(state);
  updateWinriStrip(state);
}

function setOverviewToggle(active) {
  if (!_overviewToggleEl) return;
  _overviewToggleEl._isOn = !!active;
  _overviewToggleEl.classList.toggle("is-on", !!active);
  const lbl   = _overviewToggleEl.querySelector(".dd-winri-toggle-lbl");
  const glyph = _overviewToggleEl.querySelector(".dd-winri-glyph");
  if (lbl)   lbl.textContent = active ? "Exit overview" : "Overview";
  if (glyph) glyph.textContent = active ? "▢" : "▦";
}

function updateOverviewToggle(state) {
  // Older winri binaries don't return `overview_active` / `mode` yet —
  // fall back to the toggle's last-seen state so it doesn't flicker off.
  if (state.overview_active === undefined && state.mode === undefined) return;
  const active = !!(state.overview_active || state.mode === "overview");
  setOverviewToggle(active);
}

function buildWinriControls() {
  overlayBody.innerHTML = "";

  // No section heads — the X-only header is the chrome, the grids stack
  // back-to-back with their button glyphs+labels carrying their own meaning.

  const navGrid = document.createElement("div");
  navGrid.className = "dd-winri-grid";
  navGrid.appendChild(winriBtn({ glyph: "◀", label: "Prev", action: "focus-prev" }));
  navGrid.appendChild(winriBtn({ glyph: "▶", label: "Next", action: "focus-next" }));
  overlayBody.appendChild(navGrid);

  const scrollGrid = document.createElement("div");
  scrollGrid.className = "dd-winri-grid is-tight";
  scrollGrid.appendChild(winriBtn({ glyph: "◀◀", label: "Far",    scroll: -600 }));
  scrollGrid.appendChild(winriBtn({ glyph: "◀",  label: "Left",   scroll: -200 }));
  scrollGrid.appendChild(winriBtn({ glyph: "⊙",  label: "Center", action: "center-focused" }));
  scrollGrid.appendChild(winriBtn({ glyph: "▶",  label: "Right",  scroll:  200 }));
  scrollGrid.appendChild(winriBtn({ glyph: "▶▶", label: "Far",    scroll:  600 }));
  overlayBody.appendChild(scrollGrid);

  const sizeGrid = document.createElement("div");
  sizeGrid.className = "dd-winri-grid";
  sizeGrid.appendChild(winriBtn({ glyph: "▮",    label: "1/4",     resize: "quarter" }));
  sizeGrid.appendChild(winriBtn({ glyph: "▮▮",   label: "1/2",     resize: "half" }));
  sizeGrid.appendChild(winriBtn({ glyph: "▮▮▮▮", label: "Full",    resize: "full" }));
  sizeGrid.appendChild(winriBtn({ glyph: "−",    label: "Width −", action: "width-decrement" }));
  sizeGrid.appendChild(winriBtn({ glyph: "+",    label: "Width +", action: "width-increment" }));
  overlayBody.appendChild(sizeGrid);

  // Mode (single toggle, syncs with winri's state.overview_active)
  const modeGrid = document.createElement("div");
  modeGrid.className = "dd-winri-grid";
  _overviewToggleEl = document.createElement("button");
  _overviewToggleEl.className = "dd-winri-btn dd-winri-toggle";
  _overviewToggleEl._isOn = false;
  const glyph = document.createElement("span");
  glyph.className = "dd-winri-glyph";
  glyph.textContent = "▦";
  _overviewToggleEl.appendChild(glyph);
  const lbl = document.createElement("span");
  lbl.className = "dd-winri-toggle-lbl";
  lbl.textContent = "Overview";
  _overviewToggleEl.appendChild(lbl);
  _overviewToggleEl.addEventListener("click", async () => {
    if (navigator.vibrate) navigator.vibrate(8);
    // Optimistically flip so the user gets immediate feedback; the next
    // poll will correct us if the action didn't take.
    const wasOn = _overviewToggleEl._isOn;
    setOverviewToggle(!wasOn);
    const act = wasOn ? "close-overview" : "open-overview";
    try {
      await fetch(`/api/winri/action/${act}?t=${encodeURIComponent(token)}`, { method: "POST" });
    } catch {}
  });
  modeGrid.appendChild(_overviewToggleEl);
  overlayBody.appendChild(modeGrid);

  // Live strip — built once, mutated in place by updateWinriStrip()
  _winriStripHead = null;
  _winriStripEl = document.createElement("div");
  _winriStripEl.className = "dd-winri-strip";
  overlayBody.appendChild(_winriStripEl);
}

function updateWinriStrip(state) {
  if (!_winriStripEl) return;
  const wins = state.windows || [];

  // Key existing children by data-wid so we can update without recreating.
  const existing = new Map();
  for (const child of Array.from(_winriStripEl.children)) {
    existing.set(child.dataset.wid, child);
  }

  // Preserve scroll position across reorderings.
  const savedScroll = _winriStripEl.scrollLeft;

  const seen = new Set();
  let prev = null;
  for (const w of wins) {
    const wid = String(w.id);
    seen.add(wid);
    let item = existing.get(wid);
    if (!item) {
      item = document.createElement("button");
      item.className = "dd-winri-strip-item";
      item.dataset.wid = wid;

      // Thumbnail wrap with an <img> that lazy-loads the downscaled JPEG.
      const wrap = document.createElement("div");
      wrap.className = "dd-winri-strip-thumb-wrap";
      const img = document.createElement("img");
      img.className = "dd-winri-strip-thumb";
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = "";
      img.src = thumbnailUrl(wid);
      img.addEventListener("error", () => {
        img.style.display = "none";
        if (!wrap.querySelector(".dd-winri-strip-thumb-placeholder")) {
          const ph = document.createElement("div");
          ph.className = "dd-winri-strip-thumb-placeholder";
          ph.textContent = "no preview";
          wrap.appendChild(ph);
        }
      });
      wrap.appendChild(img);
      item.appendChild(wrap);

      const body = document.createElement("div");
      body.className = "dd-winri-strip-body";
      const title = document.createElement("div");
      title.className = "dd-winri-strip-title";
      body.appendChild(title);
      const proc = document.createElement("div");
      proc.className = "dd-winri-strip-proc";
      body.appendChild(proc);
      item.appendChild(body);

      item.addEventListener("click", async () => {
        await fetch(`/api/winri/focus/${wid}?t=${encodeURIComponent(token)}`, { method: "POST" });
        // Don't full-refresh here; the 1.5s poll will catch it without disturbing scroll.
      });
    }
    // Update text content in place
    item.classList.toggle("is-focused", !!w.focused);
    const body = item.querySelector(".dd-winri-strip-body");
    body.children[0].textContent = w.title || "(untitled)";
    body.children[1].textContent = `${w.process || "—"}  ·  w:${Math.round(w.width || 0)}px`;
    // Ensure the item sits at its expected position (after `prev`, or as
    // firstChild if prev is null). Bug fix: a new item starts detached from
    // the DOM, so insertBefore(item, expectedPos) both moves AND attaches.
    const expectedPos = prev ? prev.nextSibling : _winriStripEl.firstChild;
    if (item !== expectedPos) {
      _winriStripEl.insertBefore(item, expectedPos);
    }
    prev = item;
  }

  // Drop closed windows
  for (const [wid, child] of existing) {
    if (!seen.has(wid)) _winriStripEl.removeChild(child);
  }

  // Restore scroll position so the user's drag isn't snapped back to 0.
  _winriStripEl.scrollLeft = savedScroll;
}

function section(text) {
  const el = document.createElement("div");
  el.className = "dd-winri-section-head";
  el.textContent = text;
  return el;
}

function winriBtn({ glyph, label, action, scroll, resize, warn }) {
  const b = document.createElement("button");
  b.className = "dd-winri-btn" + (warn ? " is-warn" : "");
  const g = document.createElement("span");
  g.className = "dd-winri-glyph";
  g.textContent = glyph;
  b.appendChild(g);
  const l = document.createElement("span");
  l.textContent = label;
  b.appendChild(l);
  b.addEventListener("click", async () => {
    if (navigator.vibrate) navigator.vibrate(8);
    try {
      if (action) {
        await fetch(`/api/winri/action/${action}?t=${encodeURIComponent(token)}`, { method: "POST" });
      } else if (scroll !== undefined) {
        await fetch(`/api/winri/scroll?t=${encodeURIComponent(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ delta: scroll }),
        });
      } else if (resize) {
        await fetch(`/api/winri/resize/${resize}?t=${encodeURIComponent(token)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        });
      }
      // Next 1.5s poll updates the strip; no immediate full re-render
      // (keeps user's strip scroll position intact).
    } catch (e) {
      console.error("winri action failed", e);
    }
  });
  return b;
}

function hostnameOf(url) {
  try { return new URL(url).host || url; } catch { return url; }
}

function chromeFavicon(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return `https://www.google.com/s2/favicons?sz=64&domain=${encodeURIComponent(u.hostname)}`;
  } catch { return null; }
}

async function fetchSpaces() {
  try {
    const r = await fetch("/api/desktops?t=" + encodeURIComponent(token));
    if (!r.ok) throw new Error("api/desktops " + r.status);
    const j = await r.json();
    desktops = j.desktops || [];
    if (!j.available) {
      overlayBody.innerHTML = "<div class='dd-tile'>virtual desktops not available on this system</div>";
      return;
    }
    renderSpacesOverlay();
  } catch (e) {
    overlayBody.innerHTML = `<div class='dd-tile'>failed: ${e}</div>`;
  }
}

function renderSpacesOverlay() {
  overlayBody.innerHTML = "";
  if (!desktops.length) {
    overlayBody.innerHTML = "<div class='dd-tile'>no desktops</div>";
    return;
  }
  for (const d of desktops) {
    const tile = document.createElement("button");
    tile.className = "dd-tile" + (d.current ? " is-current" : "");
    const t = document.createElement("div");
    t.className = "dd-tile-title";
    t.textContent = d.name || `Desktop ${d.index}`;
    tile.appendChild(t);
    const m = document.createElement("div");
    m.className = "dd-tile-meta";
    m.textContent = d.current ? "current" : `#${d.index}`;
    tile.appendChild(m);
    tile.addEventListener("click", () => {
      send({ t: "switch_desktop", index: d.index });
      closeOverlay();
    });
    overlayBody.appendChild(tile);
  }
}

async function fetchBookmarks() {
  try {
    const r = await fetch("/api/bookmarks?t=" + encodeURIComponent(token));
    if (!r.ok) throw new Error("api/bookmarks " + r.status);
    const bm = await r.json();
    overlayBody.innerHTML = "";
    overlayBody.classList.add("is-bookmarks");
    if (!bm.widgets || !bm.widgets.length) {
      overlayBody.innerHTML = "<div class='dd-tile'>add buttons in configs/bookmarks.yaml</div>";
      overlayBody.classList.remove("is-bookmarks");
      return;
    }
    // Render bookmarks as if it were a mini layout, scaled to fit overlay
    const mini = document.createElement("div");
    mini.className = "dd-stage-mini";
    const canvas = bm.canvas || { width: 1600, height: 1000 };
    const overlayRect = overlayBody.getBoundingClientRect();
    const scale = Math.min(
      (overlayRect.width - 32) / canvas.width,
      (overlayRect.height - 32) / canvas.height,
    );
    mini.style.width = canvas.width + "px";
    mini.style.height = canvas.height + "px";
    mini.style.transform = `scale(${scale})`;
    overlayBody.appendChild(mini);
    for (const w of bm.widgets) {
      const el = renderWidget(w, {
        onEvent: (msg) => {
          // Bookmarks aren't part of the live layout, so we can't go through `press` (server looks
          // up by widget id in active cfg). Instead, dispatch the action directly via HTTP.
          if (msg.t === "press") {
            const action = (w.props || {}).action;
            if (!action) return;
            fireBookmarkAction(action).then(() => closeOverlay()).catch(() => {});
          }
        },
      });
      mini.appendChild(el);
    }
  } catch (e) {
    overlayBody.innerHTML = `<div class='dd-tile'>failed: ${e}</div>`;
  }
}

async function fireBookmarkAction(action) {
  // Bookmarks ride the dedicated `/api/bookmark-press` shortcut handler we
  // didn't ship; instead, route through known endpoints by action type.
  if (action.type === "focus_window" && action.hwnd) {
    await fetch(`/api/focus/${action.hwnd}?t=${encodeURIComponent(token)}`, { method: "POST" });
  } else if (action.type === "switch_desktop" && action.index != null) {
    await fetch(`/api/desktops/${action.index}?t=${encodeURIComponent(token)}`, { method: "POST" });
  } else {
    // For hotkey / command / launch we have no general HTTP endpoint, so we
    // route via WS using a synthetic widget id that the server doesn't know.
    // Simpler: temporarily send a press for the bookmark id and rely on a
    // server-side handler for bookmarks. We use the dedicated /api/bookmarks/run.
    await fetch(`/api/bookmarks/run?t=${encodeURIComponent(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    }).catch(() => {});
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !overlay.hidden) closeOverlay();
});

setStatus("connecting…");

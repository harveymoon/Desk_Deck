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

const stage     = document.getElementById("stage");
const tbName    = document.getElementById("tb-name");
const tbSource  = document.getElementById("tb-source");
const tbStatus  = document.getElementById("tb-status");
const overlay   = document.getElementById("overlay");
const overlayTitle = document.getElementById("overlay-title");
const overlayBody  = document.getElementById("overlay-body");
const overlayClose = document.getElementById("overlay-close");
const overlayBack  = document.getElementById("overlay-back");

let currentLayout = null;
let widgetEls = new Map(); // widget id -> DOM element
let desktops = [];

function setStatus(text, cls = "") {
  tbStatus.textContent = text;
  tbStatus.className = "dd-tb-status " + cls;
}

function renderLayout(layout, theme) {
  currentLayout = layout;
  widgetEls = new Map();
  stage.innerHTML = "";
  if (theme) applyTheme(document.documentElement, theme);

  const canvas = layout.canvas || {};
  const cw = canvas.width  || 1600;
  const ch = canvas.height || 1000;

  const vw = window.innerWidth;
  const vh = window.innerHeight - 72;
  const scale = Math.min(vw / cw, vh / ch);
  stage.style.width  = `${cw}px`;
  stage.style.height = `${ch}px`;
  stage.style.transform = `scale(${scale}) translate(${(vw - cw * scale) / 2 / scale}px, ${(vh - ch * scale) / 2 / scale}px)`;

  for (const w of layout.widgets || []) {
    const el = renderWidget(w, { onEvent: send });
    stage.appendChild(el);
    if (w.id) widgetEls.set(w.id, { el, widget: w });
  }
  // Title bar
  const ctx = layout._context || {};
  const synth = ctx.synthetic;
  const matched = ctx.matched;
  if (matched) {
    tbName.textContent = layout.name || "—";
    tbSource.textContent = `match: ${ctx.process || "?"}`;
  } else if (synth) {
    tbName.textContent = `${(ctx.process || "?").replace(/\.exe$/i, "").toUpperCase()}`;
    tbSource.textContent = `no config · auto-window-list`;
  } else if (ctx.process) {
    tbName.textContent = (ctx.process || "?").toUpperCase();
    tbSource.textContent = `no config`;
  } else {
    tbName.textContent = layout.name || "—";
    tbSource.textContent = "";
  }
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

document.querySelectorAll(".dd-tb-menu").forEach((btn) => {
  btn.addEventListener("click", () => openOverlay(btn.dataset.overlay));
});
overlayClose.addEventListener("click", closeOverlay);

function openOverlay(kind) {
  overlay.dataset.kind = kind;
  overlay.hidden = false;
  overlayBody.innerHTML = "";
  overlayBody.classList.remove("is-bookmarks");
  overlayBack.hidden = true;
  _appsDrill = null;
  if (kind === "apps") {
    overlayTitle.textContent = "Apps";
    fetchApps();
  } else if (kind === "spaces") {
    overlayTitle.textContent = "Spaces — virtual desktops";
    fetchSpaces();
  } else if (kind === "bookmarks") {
    overlayTitle.textContent = "Bookmarks";
    fetchBookmarks();
  }
}

function closeOverlay() {
  overlay.hidden = true;
  overlayBody.innerHTML = "";
  overlay.dataset.kind = "";
  overlayBack.hidden = true;
  _appsDrill = null;
}

overlayBack.addEventListener("click", () => {
  _appsDrill = null;
  overlayBack.hidden = true;
  overlayTitle.textContent = "Apps";
  renderAppsView();
});

// Apps overlay state: top-level shows one tile per process; drilling in shows
// the windows / tabs of that one app.
let _appsCache = { wins: [], tabs: null, ts: 0 };
let _appsDrill = null;       // null = top-level; otherwise a process name

async function fetchApps() {
  overlayBody.innerHTML = "<div class='dd-tile'>loading…</div>";
  const [tabsResp, winsResp] = await Promise.allSettled([
    fetch("/api/chrome/tabs?t=" + encodeURIComponent(token)).then(r => r.json()),
    fetch("/api/windows?t=" + encodeURIComponent(token)).then(r => r.json()),
  ]);
  if (winsResp.status !== "fulfilled") {
    overlayBody.innerHTML = "<div class='dd-tile'>failed to load windows</div>";
    return;
  }
  _appsCache = {
    wins: winsResp.value || [],
    tabs: (tabsResp.status === "fulfilled" && tabsResp.value.available) ? tabsResp.value.tabs : null,
    chromeAvailable: tabsResp.status === "fulfilled" && tabsResp.value.available,
    ts: Date.now(),
  };
  _appsDrill = null;
  renderAppsView();
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
  const wins = _appsCache.wins.filter(w => (w.process || "").toLowerCase() === processName.toLowerCase());
  const showTabs = isChrome && _appsCache.tabs && _appsCache.tabs.length;

  overlayTitle.textContent =
    `${prettyAppName(processName)} · ${showTabs ? _appsCache.tabs.length + " tabs" : wins.length + " windows"}`;

  if (showTabs) {
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

  for (const w of wins) {
    const tile = buildTile({ title: w.title, meta: w.process || "—", icon: w.icon });
    tile.addEventListener("click", () => {
      send({ t: "focus_hwnd", hwnd: w.hwnd });
      closeOverlay();
    });
    overlayBody.appendChild(tile);
  }
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

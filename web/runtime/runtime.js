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

applyTheme(document.documentElement, DEFAULT_THEME);

const stage     = document.getElementById("stage");
const tbName    = document.getElementById("tb-name");
const tbSource  = document.getElementById("tb-source");
const tbStatus  = document.getElementById("tb-status");
const overlay   = document.getElementById("overlay");
const overlayHead  = document.getElementById("overlay-title");
const overlayBody  = document.getElementById("overlay-body");
const overlayClose = document.getElementById("overlay-close");

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
  const vh = window.innerHeight - 44;
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
  if (kind === "apps") {
    overlayHead.textContent = "Apps — all visible windows";
    fetchApps();
  } else if (kind === "spaces") {
    overlayHead.textContent = "Spaces — virtual desktops";
    fetchSpaces();
  } else if (kind === "bookmarks") {
    overlayHead.textContent = "Bookmarks";
    fetchBookmarks();
  }
}

function closeOverlay() {
  overlay.hidden = true;
  overlayBody.innerHTML = "";
  overlay.dataset.kind = "";
}

async function fetchApps() {
  try {
    const r = await fetch("/api/windows?t=" + encodeURIComponent(token));
    if (!r.ok) throw new Error("api/windows " + r.status);
    const wins = await r.json();
    overlayBody.innerHTML = "";
    if (!wins.length) {
      overlayBody.innerHTML = "<div class='dd-tile'>no visible windows</div>";
      return;
    }
    for (const w of wins) {
      const tile = document.createElement("button");
      tile.className = "dd-tile";
      const t = document.createElement("div");
      t.className = "dd-tile-title";
      t.textContent = w.title;
      tile.appendChild(t);
      const m = document.createElement("div");
      m.className = "dd-tile-meta";
      m.textContent = w.process || "—";
      tile.appendChild(m);
      tile.addEventListener("click", () => {
        send({ t: "focus_hwnd", hwnd: w.hwnd });
        closeOverlay();
      });
      overlayBody.appendChild(tile);
    }
  } catch (e) {
    overlayBody.innerHTML = `<div class='dd-tile'>failed: ${e}</div>`;
  }
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

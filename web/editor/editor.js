// Desk_Deck visual layout editor.
// Drag widgets from the palette onto the canvas. Click to select, drag to
// move, edit props in the inspector, Save writes YAML on the server.

import { renderWidget } from "/shared/widgets.js";

// ───────── auth ─────────
let token = localStorage.getItem("dd.token");
if (!token) {
  try {
    const r = await fetch("/api/token");
    if (r.ok) { token = (await r.json()).token; localStorage.setItem("dd.token", token); }
  } catch {}
}
if (!token) {
  document.body.innerHTML = "<p style='padding:20px;font-family:monospace'>No token. Open /pair first.</p>";
  throw new Error("no token");
}
const qs = `?t=${encodeURIComponent(token)}`;

// ───────── state ─────────
let layout = newLayout("untitled");
let selectedId = null;

const els = {
  canvas: document.getElementById("canvas"),
  canvasW: document.getElementById("canvas-w"),
  canvasH: document.getElementById("canvas-h"),
  matchProcess: document.getElementById("match-process"),
  matchTitle: document.getElementById("match-title"),
  matchClass: document.getElementById("match-class"),
  inspector: document.getElementById("inspector"),
  configSelect: document.getElementById("config-select"),
  themeSelect: document.getElementById("theme-select"),
  snapSelect: document.getElementById("snap-select"),
  saveBtn: document.getElementById("save-btn"),
  previewBtn: document.getElementById("preview-btn"),
  newBtn: document.getElementById("config-new"),
  deleteBtn: document.getElementById("config-delete"),
  toast: document.getElementById("toast"),
};

function newLayout(name) {
  return {
    name,
    match: { process: "" },
    canvas: { width: 1600, height: 1000, theme: "midnight" },
    widgets: [],
  };
}

function uid(prefix) {
  return prefix + "_" + Math.random().toString(36).slice(2, 8);
}

function defaultProps(type) {
  switch (type) {
    case "button":  return { label: "BUTTON", action: { type: "hotkey", keys: "" } };
    case "label":   return { text: "LABEL", align: "left", size: 12 };
    case "slider":  return { label: "VAL", min: 0, max: 100, step: 1, orientation: "horizontal", action: { type: "python", provider: "" } };
    case "textbox": return { monospace: true, max_lines: 200, source: "" };
    case "input":   return { placeholder: "type...", submit_label: "SEND", action: { type: "python", provider: "" } };
    case "rotary":  return { label: "VAL", min: 0, max: 100, step: 1, action: { type: "python", provider: "" } };
    default:        return {};
  }
}

function defaultSize(type) {
  switch (type) {
    case "label":   return { w: 400, h: 24 };
    case "textbox": return { w: 600, h: 300 };
    case "slider":  return { w: 320, h: 80 };
    case "input":   return { w: 400, h: 60 };
    case "rotary":  return { w: 160, h: 160 };
    default:        return { w: 200, h: 100 };
  }
}

// ───────── load configs / themes ─────────
async function loadConfigsList() {
  try {
    const r = await fetch("/api/configs" + qs);
    const list = await r.json();
    els.configSelect.innerHTML = "<option value=''>(new)</option>" +
      list.map(c => `<option value="${c.name}">${c.name}</option>`).join("");
  } catch (e) { toast("failed to load configs: " + e, true); }
}

async function loadThemesList() {
  try {
    const r = await fetch("/api/themes" + qs);
    const list = await r.json();
    els.themeSelect.innerHTML = list.map(t => `<option value="${t.name}">${t.name}</option>`).join("");
    els.themeSelect.value = layout.canvas.theme || list[0]?.name || "midnight";
  } catch (e) { toast("failed to load themes: " + e, true); }
}

async function loadConfig(name) {
  if (!name) {
    layout = newLayout("untitled");
    refresh();
    return;
  }
  try {
    const r = await fetch(`/api/configs/${encodeURIComponent(name)}${qs}`);
    if (!r.ok) throw new Error(r.statusText);
    const cfg = await r.json();
    layout = {
      name: cfg.name || name,
      match: cfg.match || {},
      canvas: cfg.canvas || { width: 1600, height: 1000, theme: "midnight" },
      widgets: cfg.widgets || [],
    };
    selectedId = null;
    refresh();
  } catch (e) { toast("load failed: " + e, true); }
}

// ───────── render ─────────
function refresh() {
  // canvas size
  els.canvas.style.width = layout.canvas.width + "px";
  els.canvas.style.height = layout.canvas.height + "px";
  els.canvasW.value = layout.canvas.width;
  els.canvasH.value = layout.canvas.height;

  // match
  els.matchProcess.value = layout.match?.process || "";
  els.matchTitle.value = layout.match?.window_title_regex || "";
  els.matchClass.value = layout.match?.window_class || "";

  // theme
  if (els.themeSelect.value && layout.canvas.theme !== els.themeSelect.value) {
    layout.canvas.theme = els.themeSelect.value;
  }

  // widgets
  els.canvas.innerHTML = "";
  for (const w of layout.widgets) {
    const el = renderWidget(w, { onEvent: () => {} });
    if (w.id === selectedId) el.classList.add("is-selected");
    el.dataset.widgetId = w.id;
    attachWidgetHandlers(el, w);
    els.canvas.appendChild(el);
  }
  renderInspector();
}

function renderInspector() {
  const w = layout.widgets.find(x => x.id === selectedId);
  if (!w) {
    els.inspector.innerHTML = "<em>Select a widget to edit its properties.</em>";
    return;
  }
  const wrap = document.createElement("div");

  function row(label, input) {
    const l = document.createElement("label");
    l.textContent = label;
    l.appendChild(input);
    wrap.appendChild(l);
    return input;
  }
  function num(label, key, opts={}) {
    const inp = document.createElement("input");
    inp.type = "number";
    inp.value = getPath(w, key) ?? 0;
    if (opts.min != null) inp.min = opts.min;
    if (opts.step != null) inp.step = opts.step;
    inp.addEventListener("input", () => { setPath(w, key, Number(inp.value)); refresh(); });
    return row(label, inp);
  }
  function text(label, key) {
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = getPath(w, key) ?? "";
    inp.addEventListener("input", () => { setPath(w, key, inp.value); refresh(); });
    return row(label, inp);
  }
  function sel(label, key, options) {
    const s = document.createElement("select");
    for (const o of options) {
      const op = document.createElement("option");
      op.value = o; op.textContent = o;
      s.appendChild(op);
    }
    s.value = getPath(w, key) ?? options[0];
    s.addEventListener("change", () => { setPath(w, key, s.value); refresh(); });
    return row(label, s);
  }
  function check(label, key) {
    const inp = document.createElement("input");
    inp.type = "checkbox";
    inp.checked = !!getPath(w, key);
    inp.addEventListener("change", () => { setPath(w, key, inp.checked); refresh(); });
    return row(label, inp);
  }

  text("ID", "id");
  num("X", "x", { min: 0 });
  num("Y", "y", { min: 0 });
  num("W", "w", { min: 10 });
  num("H", "h", { min: 10 });

  if (w.type === "button") {
    text("Label", "props.label");
    text("Icon", "props.icon");
    sel("Action", "props.action.type", ["hotkey", "command", "launch", "focus_window", "switch_desktop", "python"]);
    actionFields(w, wrap);
  } else if (w.type === "label") {
    text("Text", "props.text");
    sel("Align", "props.align", ["left", "center", "right"]);
    num("Size", "props.size", { min: 8, max: 64 });
  } else if (w.type === "slider" || w.type === "rotary") {
    text("Label", "props.label");
    num("Min", "props.min");
    num("Max", "props.max");
    num("Step", "props.step", { step: 0.01 });
    if (w.type === "slider") sel("Orientation", "props.orientation", ["horizontal", "vertical"]);
    sel("Action", "props.action.type", ["python", "command", "hotkey"]);
    actionFields(w, wrap);
  } else if (w.type === "textbox") {
    check("Monospace", "props.monospace");
    num("Max lines", "props.max_lines", { min: 10 });
    text("Source (provider)", "props.source");
  } else if (w.type === "input") {
    text("Placeholder", "props.placeholder");
    text("Submit label", "props.submit_label");
    sel("Action", "props.action.type", ["python", "command"]);
    actionFields(w, wrap);
  }

  const actions = document.createElement("div");
  actions.className = "row-actions";
  const dup = document.createElement("button");
  dup.textContent = "Duplicate";
  dup.addEventListener("click", duplicateSelected);
  const del = document.createElement("button");
  del.textContent = "Delete";
  del.className = "danger";
  del.addEventListener("click", deleteSelected);
  actions.appendChild(dup);
  actions.appendChild(del);
  wrap.appendChild(actions);

  els.inspector.innerHTML = "";
  els.inspector.appendChild(wrap);
}

function actionFields(w, wrap) {
  const t = (w.props?.action?.type) || "";
  function row(label, key, opts={}) {
    const inp = document.createElement("input");
    inp.type = opts.type || "text";
    inp.value = getPath(w, key) ?? "";
    inp.addEventListener("input", () => { setPath(w, key, opts.type === "number" ? Number(inp.value) : inp.value); refresh(); });
    const l = document.createElement("label");
    l.textContent = label;
    l.appendChild(inp);
    wrap.appendChild(l);
  }
  if (t === "hotkey") row("Keys", "props.action.keys");
  else if (t === "command") row("Cmd", "props.action.cmd");
  else if (t === "launch") row("Target", "props.action.target");
  else if (t === "focus_window") row("HWND", "props.action.hwnd", { type: "number" });
  else if (t === "switch_desktop") row("Index", "props.action.index", { type: "number" });
  else if (t === "python") row("Provider", "props.action.provider");
}

function getPath(obj, path) {
  return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
}
function setPath(obj, path, value) {
  const parts = path.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur[parts[i]] == null || typeof cur[parts[i]] !== "object") cur[parts[i]] = {};
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}

// ───────── canvas interactions ─────────
function attachWidgetHandlers(el, w) {
  el.style.pointerEvents = "auto";
  el.addEventListener("pointerdown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    e.preventDefault();
    selectedId = w.id;
    refresh();
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = w.x;
    const origY = w.y;
    const snap = Number(els.snapSelect.value) || 0;
    function move(ev) {
      const dx = ev.clientX - startX;
      const dy = ev.clientY - startY;
      let nx = origX + dx;
      let ny = origY + dy;
      if (snap > 0) {
        nx = Math.round(nx / snap) * snap;
        ny = Math.round(ny / snap) * snap;
      }
      w.x = Math.max(0, nx);
      w.y = Math.max(0, ny);
      el.style.left = w.x + "px";
      el.style.top  = w.y + "px";
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      renderInspector();
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

els.canvas.addEventListener("click", (e) => {
  if (e.target === els.canvas) {
    selectedId = null;
    refresh();
  }
});

// drop from palette
document.querySelectorAll(".dd-ed-tool").forEach((tool) => {
  tool.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const type = tool.dataset.type;
    const ghost = tool.cloneNode(true);
    ghost.style.position = "fixed";
    ghost.style.left = e.clientX + "px";
    ghost.style.top = e.clientY + "px";
    ghost.style.opacity = "0.7";
    ghost.style.pointerEvents = "none";
    ghost.style.zIndex = "1000";
    document.body.appendChild(ghost);
    function move(ev) {
      ghost.style.left = ev.clientX + "px";
      ghost.style.top = ev.clientY + "px";
    }
    function up(ev) {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      document.body.removeChild(ghost);
      const r = els.canvas.getBoundingClientRect();
      const x = ev.clientX - r.left;
      const y = ev.clientY - r.top;
      if (x < 0 || y < 0 || x > r.width || y > r.height) return;
      const snap = Number(els.snapSelect.value) || 0;
      const size = defaultSize(type);
      const nw = {
        id: uid(type),
        type,
        x: snap > 0 ? Math.round(x / snap) * snap : Math.round(x),
        y: snap > 0 ? Math.round(y / snap) * snap : Math.round(y),
        w: size.w,
        h: size.h,
        props: defaultProps(type),
      };
      layout.widgets.push(nw);
      selectedId = nw.id;
      refresh();
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
});

function duplicateSelected() {
  const w = layout.widgets.find(x => x.id === selectedId);
  if (!w) return;
  const copy = JSON.parse(JSON.stringify(w));
  copy.id = uid(w.type);
  copy.x += 24; copy.y += 24;
  layout.widgets.push(copy);
  selectedId = copy.id;
  refresh();
}
function deleteSelected() {
  layout.widgets = layout.widgets.filter(x => x.id !== selectedId);
  selectedId = null;
  refresh();
}

document.addEventListener("keydown", (e) => {
  if (document.activeElement && /input|textarea|select/i.test(document.activeElement.tagName)) return;
  if (e.key === "Delete" || e.key === "Backspace") {
    if (selectedId) { deleteSelected(); e.preventDefault(); }
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") {
    if (selectedId) { duplicateSelected(); e.preventDefault(); }
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    save(); e.preventDefault();
  }
});

// ───────── bar wiring ─────────
els.canvasW.addEventListener("change", () => { layout.canvas.width = Number(els.canvasW.value) || 1600; refresh(); });
els.canvasH.addEventListener("change", () => { layout.canvas.height = Number(els.canvasH.value) || 1000; refresh(); });
els.matchProcess.addEventListener("input", () => {
  if (els.matchProcess.value) layout.match.process = els.matchProcess.value;
  else delete layout.match.process;
});
els.matchTitle.addEventListener("input", () => {
  if (els.matchTitle.value) layout.match.window_title_regex = els.matchTitle.value;
  else delete layout.match.window_title_regex;
});
els.matchClass.addEventListener("input", () => {
  if (els.matchClass.value) layout.match.window_class = els.matchClass.value;
  else delete layout.match.window_class;
});
els.themeSelect.addEventListener("change", () => {
  layout.canvas.theme = els.themeSelect.value;
});
els.configSelect.addEventListener("change", () => loadConfig(els.configSelect.value));

els.newBtn.addEventListener("click", () => {
  const name = prompt("New config name?");
  if (!name) return;
  layout = newLayout(name);
  selectedId = null;
  refresh();
  els.configSelect.value = "";
});
els.deleteBtn.addEventListener("click", async () => {
  const name = els.configSelect.value;
  if (!name) return toast("nothing to delete", true);
  if (!confirm(`Delete config ${name}?`)) return;
  try {
    const r = await fetch(`/api/configs/${encodeURIComponent(name)}${qs}`, { method: "DELETE" });
    if (!r.ok) throw new Error(r.statusText);
    toast("deleted");
    await loadConfigsList();
    layout = newLayout("untitled");
    selectedId = null;
    refresh();
  } catch (e) { toast("delete failed: " + e, true); }
});

els.saveBtn.addEventListener("click", save);
els.previewBtn.addEventListener("click", preview);

async function save() {
  const name = (layout.name || "").trim() || prompt("Save as name?");
  if (!name) return;
  layout.name = name;
  try {
    const r = await fetch(`/api/configs/${encodeURIComponent(name)}${qs}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: layout.name,
        match: layout.match,
        canvas: layout.canvas,
        widgets: layout.widgets,
      }),
    });
    if (!r.ok) throw new Error(r.statusText);
    toast(`saved ${name}`);
    await loadConfigsList();
    els.configSelect.value = name;
  } catch (e) { toast("save failed: " + e, true); }
}

let previewWs = null;
function preview() {
  if (!previewWs || previewWs.readyState !== 1) {
    previewWs = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/live?t=${encodeURIComponent(token)}&device=editor`);
    previewWs.onopen = () => sendPreview();
  } else {
    sendPreview();
  }
}
function sendPreview() {
  previewWs.send(JSON.stringify({ t: "preview", layout }));
  toast("preview sent");
}

function toast(text, isErr) {
  els.toast.textContent = text;
  els.toast.className = "dd-ed-toast" + (isErr ? " is-error" : "");
  els.toast.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { els.toast.hidden = true; }, 1800);
}

// ───────── init ─────────
await loadConfigsList();
await loadThemesList();
refresh();

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
  filtersBtn: document.getElementById("filters-btn"),
  sidebarBtn: document.getElementById("sidebar-btn"),
  newBtn: document.getElementById("config-new"),
  deleteBtn: document.getElementById("config-delete"),
  toast: document.getElementById("toast"),
  modal: document.getElementById("modal"),
  modalBody: document.getElementById("modal-body"),
  modalClose: document.getElementById("modal-close"),
  modalCancel: document.getElementById("modal-cancel"),
  modalSave: document.getElementById("modal-save"),
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
    case "button":      return { label: "BUTTON", action: { type: "hotkey", keys: "" } };
    case "label":       return { text: "LABEL", align: "left", size: 12 };
    case "slider":      return { label: "VAL", min: 0, max: 100, step: 1, orientation: "horizontal", action: { type: "python", provider: "" } };
    case "textbox":     return { monospace: true, max_lines: 200, source: "" };
    case "input":       return { placeholder: "type...", submit_label: "SEND", action: { type: "python", provider: "" } };
    case "rotary":      return { label: "VAL", min: 0, max: 100, step: 1, action: { type: "python", provider: "" } };
    case "window_list": return { source: "process_windows", columns: 3, button_height: 90, gap: 12 };
    default:            return {};
  }
}

function defaultSize(type) {
  switch (type) {
    case "label":       return { w: 400, h: 24 };
    case "textbox":     return { w: 600, h: 300 };
    case "slider":      return { w: 320, h: 80 };
    case "input":       return { w: 400, h: 60 };
    case "rotary":      return { w: 160, h: 160 };
    case "window_list": return { w: 1200, h: 500 };
    default:            return { w: 200, h: 100 };
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
    if (opts.bind) inp.dataset.bind = opts.bind;
    inp.addEventListener("input", () => {
      setPath(w, key, Number(inp.value));
      // For position/size, mutate the live DOM directly so we don't lose focus.
      if (key === "x") liveSet("left", w.x + "px");
      else if (key === "y") liveSet("top", w.y + "px");
      else if (key === "w") liveSet("width", w.w + "px");
      else if (key === "h") liveSet("height", w.h + "px");
      else refresh();
    });
    return row(label, inp);
  }
  function liveSet(prop, value) {
    const el = els.canvas.querySelector(`.dd-widget[data-widget-id="${selectedId}"]`);
    if (el) el.style[prop] = value;
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
  num("X", "x", { min: 0, bind: "x" });
  num("Y", "y", { min: 0, bind: "y" });
  num("W", "w", { min: 10, bind: "w" });
  num("H", "h", { min: 10, bind: "h" });

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
  } else if (w.type === "window_list") {
    sel("Source", "props.source", ["process_windows", "chrome_tabs"]);
    num("Columns", "props.columns", { min: 1 });
    num("Button height", "props.button_height", { min: 40 });
    num("Gap", "props.gap", { min: 0 });
    num("Max rows", "props.max_rows", { min: 0 });
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
    // Update selection state WITHOUT rebuilding the canvas — otherwise the
    // `el` reference is detached and the drag updates a dead node.
    selectWidget(w.id);

    const scale = canvasScale();
    const startX = e.clientX;
    const startY = e.clientY;
    const origX = w.x;
    const origY = w.y;
    const snap = Number(els.snapSelect.value) || 0;
    let moved = false;

    function move(ev) {
      const dx = (ev.clientX - startX) / scale;
      const dy = (ev.clientY - startY) / scale;
      let nx = origX + dx;
      let ny = origY + dy;
      if (snap > 0) {
        nx = Math.round(nx / snap) * snap;
        ny = Math.round(ny / snap) * snap;
      }
      w.x = Math.max(0, Math.round(nx));
      w.y = Math.max(0, Math.round(ny));
      el.style.left = w.x + "px";
      el.style.top  = w.y + "px";
      if (!moved && (Math.abs(dx) > 1 || Math.abs(dy) > 1)) {
        moved = true;
        el.classList.add("is-dragging");
      }
      // Live-update inspector x/y fields (look them up cheaply)
      const xInput = els.inspector.querySelector('label input[data-bind="x"]');
      const yInput = els.inspector.querySelector('label input[data-bind="y"]');
      if (xInput) xInput.value = w.x;
      if (yInput) yInput.value = w.y;
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      el.classList.remove("is-dragging");
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

function canvasScale() {
  // The editor canvas is not scaled in the current layout, but if we ever
  // add zoom this is the hook. Returns 1 for now.
  return 1;
}

function selectWidget(id) {
  selectedId = id;
  // Update only selection class on existing elements (no full rebuild)
  for (const el of els.canvas.querySelectorAll(".dd-widget")) {
    el.classList.toggle("is-selected", el.dataset.widgetId === id);
  }
  renderInspector();
}

els.canvas.addEventListener("pointerdown", (e) => {
  if (e.target === els.canvas) {
    selectWidget(null);
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
els.filtersBtn.addEventListener("click", openFiltersModal);
els.sidebarBtn.addEventListener("click", openSidebarModal);
els.modalClose.addEventListener("click", closeModal);
els.modalCancel.addEventListener("click", closeModal);
els.modal.addEventListener("click", (e) => { if (e.target === els.modal) closeModal(); });

async function openFiltersModal() {
  els.modal.hidden = false;
  els.modalBody.innerHTML = "<div class='dd-ed-modal-empty'>loading…</div>";
  try {
    const [filtersResp, procsResp] = await Promise.all([
      fetch("/api/filters" + qs).then(r => r.json()),
      fetch("/api/processes" + qs).then(r => r.json()),
    ]);
    renderFiltersModal(filtersResp, procsResp);
  } catch (e) {
    els.modalBody.innerHTML = `<div class='dd-ed-modal-empty'>failed: ${e}</div>`;
  }
}

let _modalState = null;

function renderFiltersModal(filters, processes) {
  // Merge: union of currently-hidden process names and processes seen running now,
  // case-insensitive but display original casing where possible.
  const hidden = new Set((filters.hide_processes || []).map(s => s.toLowerCase()));
  const seen = new Map();
  for (const p of processes) {
    seen.set(p.process.toLowerCase(), p);
  }
  for (const hp of filters.hide_processes || []) {
    if (!seen.has(hp.toLowerCase())) {
      seen.set(hp.toLowerCase(), { process: hp, icon: null, sample_title: "(not running)", windows: 0 });
    }
  }
  const rows = Array.from(seen.values()).sort((a, b) => a.process.toLowerCase().localeCompare(b.process.toLowerCase()));

  _modalState = {
    kind: "filters",
    hidden: new Set([...(filters.hide_processes || [])].map(s => s.toLowerCase())),
    hide_classes: filters.hide_classes || [],
    hide_title_regex: filters.hide_title_regex || [],
    canonical: new Map(rows.map(r => [r.process.toLowerCase(), r.process])),
  };

  let html = "<div class='dd-ed-modal-help'>Tick apps to hide them from the tablet's Apps overlay. Window-class and title-regex filters can be edited in <code>configs/_filters.yaml</code>.</div>";
  els.modalBody.innerHTML = html;

  if (!rows.length) {
    const e = document.createElement("div");
    e.className = "dd-ed-modal-empty";
    e.textContent = "no visible apps right now";
    els.modalBody.appendChild(e);
    return;
  }

  for (const row of rows) {
    const r = document.createElement("label");
    r.className = "dd-ed-proc-row";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    const key = row.process.toLowerCase();
    cb.checked = hidden.has(key);
    cb.addEventListener("change", () => {
      if (cb.checked) _modalState.hidden.add(key);
      else _modalState.hidden.delete(key);
    });
    r.appendChild(cb);

    const iconEl = document.createElement("div");
    iconEl.className = "dd-ed-proc-icon";
    if (row.icon) {
      const img = document.createElement("img");
      img.src = row.icon;
      img.alt = "";
      iconEl.appendChild(img);
    } else {
      iconEl.textContent = (row.process || "?").slice(0, 1).toUpperCase();
    }
    r.appendChild(iconEl);

    const info = document.createElement("div");
    info.className = "dd-ed-proc-info";
    const name = document.createElement("div");
    name.className = "dd-ed-proc-name";
    name.textContent = row.process;
    info.appendChild(name);
    const samp = document.createElement("div");
    samp.className = "dd-ed-proc-sample";
    samp.textContent = row.sample_title || "(not running)";
    info.appendChild(samp);
    r.appendChild(info);

    const count = document.createElement("div");
    count.className = "dd-ed-proc-count";
    count.textContent = row.windows ? `${row.windows} win` : "—";
    r.appendChild(count);

    els.modalBody.appendChild(r);
  }
}

async function closeModal() { els.modal.hidden = true; _modalState = null; }

els.modalSave.addEventListener("click", async () => {
  if (!_modalState) return closeModal();
  if (_modalState.kind === "filters") return saveFiltersModal();
  if (_modalState.kind === "sidebar") return saveSidebarModal();
});

async function saveFiltersModal() {
  const hide_processes = Array.from(_modalState.hidden).map(k => _modalState.canonical.get(k) || k);
  hide_processes.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  try {
    const r = await fetch("/api/filters" + qs, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        hide_processes,
        hide_classes: _modalState.hide_classes,
        hide_title_regex: _modalState.hide_title_regex,
      }),
    });
    if (!r.ok) throw new Error(r.statusText);
    toast(`saved · ${hide_processes.length} hidden`);
    closeModal();
  } catch (e) { toast("save failed: " + e, true); }
}

// ───────── Sidebar modal ─────────

async function openSidebarModal() {
  els.modal.hidden = false;
  els.modalBody.innerHTML = "<div class='dd-ed-modal-empty'>loading…</div>";
  try {
    const cfg = await fetch("/api/sidebar" + qs).then(r => r.json());
    _modalState = { kind: "sidebar", buttons: (cfg.buttons || []).map(b => ({ ...b })) };
    renderSidebarModal();
  } catch (e) {
    els.modalBody.innerHTML = `<div class='dd-ed-modal-empty'>failed: ${e}</div>`;
  }
}

function renderSidebarModal() {
  const head = "<div class='dd-ed-modal-help'>Tap a button on the tablet's right-side strip to trigger its action or open an overlay. Reorder with ↑ / ↓; new buttons append to the bottom.</div>";
  els.modalBody.innerHTML = head;

  const list = document.createElement("div");
  list.className = "dd-ed-sb-list";
  _modalState.buttons.forEach((b, idx) => list.appendChild(buildSidebarRow(b, idx)));
  els.modalBody.appendChild(list);

  const adder = document.createElement("div");
  adder.className = "dd-ed-sb-add";
  const addBtn = document.createElement("button");
  addBtn.textContent = "+ Add button";
  addBtn.addEventListener("click", () => {
    _modalState.buttons.push({
      id: "btn_" + Math.random().toString(36).slice(2, 6),
      glyph: "•", label: "New", kind: "overlay", target: "apps",
    });
    renderSidebarModal();
  });
  adder.appendChild(addBtn);
  els.modalBody.appendChild(adder);
}

function buildSidebarRow(b, idx) {
  const row = document.createElement("div");
  row.className = "dd-ed-sb-row";

  // Reorder column
  const reorder = document.createElement("div");
  reorder.className = "dd-ed-sb-reorder";
  const up = document.createElement("button");
  up.textContent = "↑"; up.disabled = idx === 0;
  up.addEventListener("click", () => moveSidebarRow(idx, -1));
  const dn = document.createElement("button");
  dn.textContent = "↓"; dn.disabled = idx === _modalState.buttons.length - 1;
  dn.addEventListener("click", () => moveSidebarRow(idx, +1));
  reorder.appendChild(up); reorder.appendChild(dn);
  row.appendChild(reorder);

  // Glyph preview
  const preview = document.createElement("div");
  preview.className = "dd-ed-sb-preview";
  preview.textContent = b.glyph || "•";
  row.appendChild(preview);

  // Form fields
  const fields = document.createElement("div");
  fields.className = "dd-ed-sb-fields";

  fields.appendChild(field("Glyph", inputText(b.glyph || "", v => { b.glyph = v; preview.textContent = v || "•"; })));
  fields.appendChild(field("Label", inputText(b.label || "", v => { b.label = v; })));
  fields.appendChild(field("Kind", inputSelect(b.kind || "overlay", ["overlay", "action"], v => {
    b.kind = v;
    if (v === "overlay" && (typeof b.target !== "string")) b.target = "apps";
    if (v === "action"  && (typeof b.target !== "object" || !b.target)) b.target = { type: "hotkey", keys: "" };
    renderSidebarModal();  // re-render to swap target editor
  })));
  if (b.kind === "overlay") {
    fields.appendChild(field("Overlay", inputSelect(b.target || "apps",
      ["apps", "winri", "bookmarks"], v => { b.target = v; })));
  } else {
    const actType = (b.target && b.target.type) || "hotkey";
    fields.appendChild(field("Action", inputSelect(actType,
      ["hotkey", "command", "launch", "switch_desktop", "python"], v => {
        b.target = { type: v };
        renderSidebarModal();
      })));
    if (actType === "hotkey") {
      fields.appendChild(field("Keys", inputText((b.target?.keys) || "", v => { b.target.keys = v; })));
    } else if (actType === "command") {
      fields.appendChild(field("Cmd",  inputText((b.target?.cmd) || "", v => { b.target.cmd = v; })));
    } else if (actType === "launch") {
      fields.appendChild(field("Target", inputText((b.target?.target) || "", v => { b.target.target = v; })));
    } else if (actType === "switch_desktop") {
      fields.appendChild(field("Index", inputText(String((b.target?.index) ?? 1), v => { b.target.index = Number(v) || 1; })));
    } else if (actType === "python") {
      fields.appendChild(field("Provider", inputText((b.target?.provider) || "", v => { b.target.provider = v; })));
    }
  }

  row.appendChild(fields);

  // Delete
  const del = document.createElement("button");
  del.className = "danger";
  del.textContent = "×";
  del.title = "Remove";
  del.addEventListener("click", () => {
    _modalState.buttons.splice(idx, 1);
    renderSidebarModal();
  });
  row.appendChild(del);

  return row;
}

function field(label, input) {
  const wrap = document.createElement("label");
  wrap.className = "dd-ed-sb-field";
  const span = document.createElement("span");
  span.textContent = label;
  wrap.appendChild(span);
  wrap.appendChild(input);
  return wrap;
}

function inputText(value, onChange) {
  const i = document.createElement("input");
  i.type = "text"; i.value = value;
  i.addEventListener("input", () => onChange(i.value));
  return i;
}

function inputSelect(value, options, onChange) {
  const s = document.createElement("select");
  for (const o of options) {
    const op = document.createElement("option");
    op.value = o; op.textContent = o;
    s.appendChild(op);
  }
  s.value = value;
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

function moveSidebarRow(idx, delta) {
  const arr = _modalState.buttons;
  const j = idx + delta;
  if (j < 0 || j >= arr.length) return;
  [arr[idx], arr[j]] = [arr[j], arr[idx]];
  renderSidebarModal();
}

async function saveSidebarModal() {
  try {
    const r = await fetch("/api/sidebar" + qs, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ buttons: _modalState.buttons }),
    });
    if (!r.ok) throw new Error(r.statusText);
    toast(`saved · ${_modalState.buttons.length} buttons (reload tablet)`);
    closeModal();
  } catch (e) { toast("save failed: " + e, true); }
}

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

// ───────── Logs tab ─────────
const logsEls = {
  status: document.getElementById("logs-status"),
  filter: document.getElementById("logs-filter"),
  autoscroll: document.getElementById("logs-autoscroll"),
  clear: document.getElementById("logs-clear"),
  scroll: document.getElementById("logs-scroll"),
  body: document.getElementById("logs"),
  badge: document.getElementById("logs-badge"),
};
let _logSrc = null;
let _logFilterText = "";
let _logUnseen = 0;

function setLogStatus(text, cls) {
  logsEls.status.textContent = text;
  logsEls.status.className = "dd-ed-logs-status" + (cls ? " " + cls : "");
}

function escapeLogHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function appendLogEntry(entry) {
  const div = document.createElement("div");
  div.className = "dd-log-line" + (entry.stream === "err" ? " is-err" : "");

  const tagMatch = (entry.line || "").match(/^\[([\w-]+)\]\s*(.*)$/);
  if (tagMatch) {
    div.dataset.tag = tagMatch[1];
    div.innerHTML =
      `<span class="dd-log-ts">${entry.ts}</span>` +
      `<span class="dd-log-body"><span class="dd-log-tag">[${tagMatch[1]}]</span> ${escapeLogHtml(tagMatch[2])}</span>`;
  } else {
    div.innerHTML =
      `<span class="dd-log-ts">${entry.ts}</span>` +
      `<span class="dd-log-body">${escapeLogHtml(entry.line)}</span>`;
  }

  // Apply current filter
  if (_logFilterText && !(entry.line || "").toLowerCase().includes(_logFilterText)) {
    div.classList.add("is-filtered");
  }

  logsEls.body.appendChild(div);
  while (logsEls.body.children.length > 1000) {
    logsEls.body.removeChild(logsEls.body.firstChild);
  }
  if (logsEls.autoscroll.checked) {
    logsEls.scroll.scrollTop = logsEls.scroll.scrollHeight;
  }

  // Badge only when something looks interesting and Logs tab isn't active.
  // Skip routine uvicorn INFO / [ctx] / [config] chatter.
  const text = entry.line || "";
  const isInteresting =
    /\b(error|traceback|exception|failed|unhandled)\b/i.test(text) ||
    (entry.stream === "err" && !text.startsWith("INFO:"));
  const logsTabActive = document.querySelector(".dd-ed-tab.is-active[data-tab='logs']");
  if (isInteresting && !logsTabActive) {
    _logUnseen++;
    logsEls.badge.hidden = false;
  }
}

function startLogStream() {
  if (_logSrc) return;
  setLogStatus("connecting…");
  _logSrc = new EventSource(`/api/logs/stream${qs}`);
  _logSrc.onopen = () => setLogStatus("connected", "is-connected");
  _logSrc.onmessage = (e) => {
    try { appendLogEntry(JSON.parse(e.data)); }
    catch {}
  };
  _logSrc.onerror = () => {
    setLogStatus("reconnecting…", "is-error");
    // EventSource auto-reconnects on transient errors. If it goes CLOSED, restart it.
    if (_logSrc && _logSrc.readyState === 2) {
      _logSrc.close();
      _logSrc = null;
      setTimeout(startLogStream, 1500);
    }
  };
}

function stopLogStream() {
  if (_logSrc) { _logSrc.close(); _logSrc = null; }
  setLogStatus("disconnected");
}

logsEls.filter.addEventListener("input", () => {
  _logFilterText = logsEls.filter.value.trim().toLowerCase();
  for (const div of logsEls.body.children) {
    const txt = (div.textContent || "").toLowerCase();
    div.classList.toggle("is-filtered", !!_logFilterText && !txt.includes(_logFilterText));
  }
});

logsEls.clear.addEventListener("click", () => { logsEls.body.innerHTML = ""; });

// ───────── tab switching ─────────
document.querySelectorAll(".dd-ed-tab").forEach((t) => {
  t.addEventListener("click", () => switchTab(t.dataset.tab));
});

function switchTab(name) {
  document.querySelectorAll(".dd-ed-tab").forEach((t) => {
    t.classList.toggle("is-active", t.dataset.tab === name);
  });
  document.querySelectorAll("[data-pane]").forEach((p) => {
    p.hidden = p.dataset.pane !== name;
  });
  if (name === "logs") {
    _logUnseen = 0;
    logsEls.badge.hidden = true;
    startLogStream();
  } else {
    // Keep the stream alive so the badge counts unseen lines — no stopLogStream() here.
    // Comment out the next line if you'd rather pause the SSE when not viewing logs.
    // stopLogStream();
  }
}

// ───────── init ─────────
await loadConfigsList();
await loadThemesList();
refresh();

// Start the log stream in the background so the badge can flag new errors
// even while you're editing the canvas.
startLogStream();

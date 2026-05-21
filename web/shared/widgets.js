// Widget renderer — shared by the tablet runtime and the editor canvas.
//
// renderWidget(widget, { onEvent }) → DOM element
// updateWidget(el, widget, patch)   → mutates DOM in place
//
// onEvent({ t, id, value?, text?, payload? }) is invoked when the user
// interacts with the widget. The runtime forwards these to the server over WS.

export function renderWidget(widget, { onEvent } = {}) {
  const fn = RENDERERS[widget.type];
  const emit = onEvent || (() => {});
  if (!fn) {
    const el = document.createElement("div");
    el.textContent = `[unknown widget: ${widget.type}]`;
    positionWidget(el, widget);
    return el;
  }
  const el = fn(widget, emit);
  positionWidget(el, widget);
  el.dataset.widgetId = widget.id || "";
  el.dataset.widgetType = widget.type;
  if (widget.style) {
    for (const [k, v] of Object.entries(widget.style)) {
      el.style.setProperty(`--w-${k}`, v);
    }
  }
  return el;
}

export function updateWidget(el, widget, patch) {
  if (!el) return;
  if (widget.type === "textbox") {
    if ("replace" in patch) {
      el.textContent = patch.replace;
    } else if ("append" in patch) {
      el.appendChild(document.createTextNode(patch.append));
      const max = (widget.props && widget.props.max_lines) || 500;
      // Trim from top if too many lines
      const lines = el.innerText.split(/\r?\n/);
      if (lines.length > max) {
        el.textContent = lines.slice(lines.length - max).join("\n");
      }
      el.scrollTop = el.scrollHeight;
    }
  } else if (widget.type === "slider") {
    // Retune range first (min/max/step/label) — that way the value patch
    // gets quantised against the *new* range, not the old one.
    if ("min" in patch || "max" in patch || "step" in patch || "label" in patch) {
      if (el && typeof el._tune === "function") el._tune(patch);
    }
    if ("value" in patch) setSliderValue(el, widget, patch.value);
    if ("disabled" in patch) el.classList.toggle("is-disabled", !!patch.disabled);
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
  } else if (widget.type === "rotary") {
    if ("value" in patch) setRotaryValue(el, widget, patch.value);
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
  } else if (widget.type === "button") {
    if ("active" in patch) el.classList.toggle("is-on", !!patch.active);
    if ("disabled" in patch) {
      el.disabled = !!patch.disabled;
      el.classList.toggle("is-disabled", !!patch.disabled);
    }
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
    if ("label" in patch) {
      const lbl = el.querySelector(".dd-button-label");
      if (lbl) lbl.textContent = patch.label;
    }
  } else if (widget.type === "textbox" || widget.type === "label" || widget.type === "input") {
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
  } else if (widget.type === "value_ladder") {
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
    if ("disabled" in patch) {
      el.disabled = !!patch.disabled;
      el.classList.toggle("is-disabled", !!patch.disabled);
    }
  } else if (widget.type === "param_panel") {
    if (el && typeof el._patch === "function") el._patch(patch);
    if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
  }
}

function positionWidget(el, w) {
  el.style.position = "absolute";
  el.style.left   = `${w.x ?? 0}px`;
  el.style.top    = `${w.y ?? 0}px`;
  el.style.width  = `${w.w ?? 120}px`;
  el.style.height = `${w.h ?? 80}px`;
}

// ───────── Button ─────────
function renderButton(w, emit) {
  const p = w.props || {};
  const el = document.createElement("button");
  el.className = "dd-widget dd-button";
  el.type = "button";

  if (p.icon) {
    const icon = document.createElement("span");
    icon.className = "dd-button-icon";
    icon.textContent = p.icon;
    el.appendChild(icon);
  }
  const label = document.createElement("span");
  label.className = "dd-button-label";
  label.textContent = p.label || w.id || "";
  el.appendChild(label);

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    el.classList.add("is-active");
    if (navigator.vibrate) navigator.vibrate(8);
    emit({ t: "press", id: w.id });
  });
  const release = () => {
    if (el.classList.contains("is-active")) {
      el.classList.remove("is-active");
      emit({ t: "release", id: w.id });
    }
  };
  el.addEventListener("pointerup", release);
  el.addEventListener("pointercancel", release);
  el.addEventListener("pointerleave", release);
  return el;
}

// ───────── Label ─────────
function renderLabel(w, _emit) {
  const p = w.props || {};
  const el = document.createElement("div");
  el.className = "dd-widget dd-label";
  el.textContent = p.text || "";
  el.style.textAlign = p.align || "left";
  if (p.size) el.style.fontSize = `${p.size}px`;
  return el;
}

// ───────── Slider ─────────
function renderSlider(w, emit) {
  const p = w.props || {};
  // min/max/step/label are *mutable* — the server can patch them at runtime
  // (used by the TD rollover slider to retune itself to whatever par is
  // under the mouse, so the slider operates in real par units, not 0..1).
  let min  = p.min  ?? 0;
  let max  = p.max  ?? 100;
  let step = p.step ?? 1;
  const orientation = (p.orientation === "vertical") ? "vertical" : "horizontal";
  const el = document.createElement("div");
  el.className = `dd-widget dd-slider is-${orientation}`;

  const track = document.createElement("div");
  track.className = "dd-slider-track";
  const fill = document.createElement("div");
  fill.className = "dd-slider-fill";
  track.appendChild(fill);
  el.appendChild(track);

  const meta = document.createElement("div");
  meta.className = "dd-slider-meta";
  const valEl = document.createElement("div");
  valEl.className = "dd-slider-value";
  const labEl = document.createElement("div");
  labEl.className = "dd-slider-label";
  labEl.textContent = p.label || "";
  meta.appendChild(valEl);
  meta.appendChild(labEl);
  el.appendChild(meta);

  let value = p.value ?? min;
  let dragging = false;
  let lastSent = null;

  function setPct(pct) {
    if (orientation === "horizontal") {
      fill.style.width = `${pct * 100}%`;
      fill.style.height = `100%`;
    } else {
      fill.style.height = `${pct * 100}%`;
      fill.style.width = `100%`;
    }
  }
  function quantize(v) {
    if (step <= 0) return Math.max(min, Math.min(max, v));
    const snapped = Math.round((v - min) / step) * step + min;
    return Math.max(min, Math.min(max, snapped));
  }
  function valueToPct(v) {
    return max === min ? 0 : (v - min) / (max - min);
  }
  function pctToValue(pct) {
    return quantize(min + pct * (max - min));
  }
  // Decimal places to display, derived from step. Integer step → no
  // decimals. Otherwise count the digits after the decimal point in
  // step (e.g. step=0.001 → 3), capped at 4 so display stays readable.
  function decimalsFor(s) {
    if (!s || s >= 1 || Number.isInteger(s)) return 0;
    const str = s.toString();
    if (str.includes("e-")) return Math.min(4, parseInt(str.split("e-")[1], 10) || 0);
    const dot = str.indexOf(".");
    return dot < 0 ? 0 : Math.min(4, str.length - dot - 1);
  }
  function format(v) {
    const d = decimalsFor(step);
    return d === 0 ? `${Math.round(v)}` : v.toFixed(d);
  }
  // Patch hook: server calls el._tune({min,max,step,label}) when the par
  // under the mouse changes. Re-quantises + redraws the current value
  // against the new range so the thumb position stays meaningful.
  el._tune = (cfg) => {
    if (cfg.min  !== undefined) min  = cfg.min;
    if (cfg.max  !== undefined) max  = cfg.max;
    if (cfg.step !== undefined) step = cfg.step;
    if (cfg.label !== undefined) labEl.textContent = cfg.label;
    value = quantize(value);
    setPct(valueToPct(value));
    valEl.textContent = format(value);
  };

  // Throttle (not debounce): emit at most once every THROTTLE_MS during a
  // continuous drag, and always emit the FINAL value when the drag ends
  // so the receiver lands exactly where the user left the thumb.
  const THROTTLE_MS = 70;       // ~14 Hz — smooth for slider control
  let lastEmitTime = 0;
  let pendingValue = null;
  let throttleTimer = null;

  function flush() {
    throttleTimer = null;
    if (pendingValue === null) return;
    if (pendingValue === lastSent) {
      pendingValue = null;
      return;
    }
    lastSent = pendingValue;
    pendingValue = null;
    lastEmitTime = performance.now();
    emit({ t: "value", id: w.id, value: lastSent });
  }

  function update(v, opts = {}) {
    value = quantize(v);
    setPct(valueToPct(value));
    valEl.textContent = format(value);
    if (opts.emit) {
      pendingValue = value;
      const now = performance.now();
      const elapsed = now - lastEmitTime;
      if (elapsed >= THROTTLE_MS) {
        flush();
      } else if (throttleTimer === null) {
        throttleTimer = setTimeout(flush, THROTTLE_MS - elapsed);
      }
    }
  }
  el._update = (v) => update(v);

  function onDown(e) {
    e.preventDefault();
    dragging = true;
    el.setPointerCapture && el.setPointerCapture(e.pointerId);
    handleMove(e);
  }
  function onMove(e) {
    if (!dragging) return;
    handleMove(e);
  }
  function onUp(e) {
    dragging = false;
    try { el.releasePointerCapture(e.pointerId); } catch {}
    // Force-flush the final value so the receiver lands exactly where the
    // thumb stopped, even if the last move arrived inside the throttle window.
    if (throttleTimer !== null) { clearTimeout(throttleTimer); throttleTimer = null; }
    flush();
  }
  function handleMove(e) {
    const rect = track.getBoundingClientRect();
    let pct;
    if (orientation === "horizontal") {
      pct = (e.clientX - rect.left) / rect.width;
    } else {
      pct = 1 - (e.clientY - rect.top) / rect.height;
    }
    pct = Math.max(0, Math.min(1, pct));
    update(pctToValue(pct), { emit: true });
  }
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointercancel", onUp);

  update(value);
  return el;
}

function setSliderValue(el, widget, value) {
  if (el && typeof el._update === "function") el._update(value);
}

// ───────── Textbox (read-only) ─────────
function renderTextbox(w, _emit) {
  const p = w.props || {};
  const el = document.createElement("div");
  el.className = "dd-widget dd-textbox";
  if (p.monospace !== false) el.classList.add("is-mono");
  if (p.initial) el.textContent = p.initial;
  return el;
}

// ───────── Input (editable textbox) ─────────
function renderInput(w, emit) {
  const p = w.props || {};
  const el = document.createElement("div");
  el.className = "dd-widget dd-input";
  const inp = document.createElement("input");
  inp.type = "text";
  inp.placeholder = p.placeholder || "";
  el.appendChild(inp);
  const btn = document.createElement("button");
  btn.textContent = p.submit_label || "SEND";
  el.appendChild(btn);
  function submit() {
    const text = inp.value;
    if (!text) return;
    emit({ t: "input", id: w.id, text });
    if (p.clear_on_submit !== false) inp.value = "";
  }
  btn.addEventListener("click", submit);
  inp.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  return el;
}

// ───────── Rotary dial ─────────
function renderRotary(w, emit) {
  const p = w.props || {};
  const min = p.min ?? 0;
  const max = p.max ?? 100;
  const step = p.step ?? 1;
  const sweepDeg = 270;
  const el = document.createElement("div");
  el.className = "dd-widget dd-rotary";

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "-50 -50 100 100");
  svg.classList.add("dd-rotary-svg");

  const ring = document.createElementNS("http://www.w3.org/2000/svg", "path");
  ring.setAttribute("class", "dd-rotary-ring");
  ring.setAttribute("stroke-width", "8");
  ring.setAttribute("d", describeArc(0, 0, 40, -sweepDeg/2, sweepDeg/2));
  svg.appendChild(ring);

  const arc = document.createElementNS("http://www.w3.org/2000/svg", "path");
  arc.setAttribute("class", "dd-rotary-arc");
  arc.setAttribute("stroke-width", "8");
  svg.appendChild(arc);

  const needle = document.createElementNS("http://www.w3.org/2000/svg", "line");
  needle.setAttribute("class", "dd-rotary-needle");
  needle.setAttribute("x1", "0");
  needle.setAttribute("y1", "0");
  needle.setAttribute("x2", "0");
  needle.setAttribute("y2", "-32");
  needle.setAttribute("stroke-width", "3");
  svg.appendChild(needle);

  el.appendChild(svg);

  const valEl = document.createElement("div");
  valEl.className = "dd-rotary-value";
  el.appendChild(valEl);

  const labEl = document.createElement("div");
  labEl.className = "dd-rotary-label";
  labEl.textContent = p.label || "";
  el.appendChild(labEl);

  let value = p.value ?? min;
  let lastAngle = null;
  let dragging = false;
  let debounceTimer = null;
  let lastSent = null;

  function valueToAngle(v) {
    const pct = (v - min) / (max - min);
    return -sweepDeg/2 + pct * sweepDeg;
  }
  function setVisual(v) {
    const a = valueToAngle(v);
    arc.setAttribute("d", describeArc(0, 0, 40, -sweepDeg/2, a));
    needle.setAttribute("transform", `rotate(${a})`);
    valEl.textContent = Number.isInteger(step) ? `${v}` : v.toFixed(2);
  }
  function clamp(v) {
    return Math.max(min, Math.min(max, Math.round((v - min)/step)*step + min));
  }
  function update(v, opts={}) {
    value = clamp(v);
    setVisual(value);
    if (opts.emit && value !== lastSent) {
      lastSent = value;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => emit({ t: "value", id: w.id, value }), 30);
    }
  }
  el._update = (v) => update(v);

  function angleFromEvent(e) {
    const rect = el.getBoundingClientRect();
    const cx = rect.left + rect.width/2;
    const cy = rect.top + rect.height/2;
    const dx = e.clientX - cx;
    const dy = e.clientY - cy;
    let deg = Math.atan2(dy, dx) * 180/Math.PI + 90; // 0 deg at top
    if (deg > 180) deg -= 360;
    return Math.max(-sweepDeg/2, Math.min(sweepDeg/2, deg));
  }
  function onDown(e) {
    e.preventDefault();
    dragging = true;
    el.setPointerCapture && el.setPointerCapture(e.pointerId);
    const a = angleFromEvent(e);
    const pct = (a + sweepDeg/2) / sweepDeg;
    update(min + pct * (max - min), { emit: true });
  }
  function onMove(e) {
    if (!dragging) return;
    const a = angleFromEvent(e);
    const pct = (a + sweepDeg/2) / sweepDeg;
    update(min + pct * (max - min), { emit: true });
  }
  function onUp(e) {
    dragging = false;
    try { el.releasePointerCapture(e.pointerId); } catch {}
  }
  el.addEventListener("pointerdown", onDown);
  el.addEventListener("pointermove", onMove);
  el.addEventListener("pointerup", onUp);
  el.addEventListener("pointercancel", onUp);

  update(value);
  return el;
}

function setRotaryValue(el, widget, value) {
  if (el && typeof el._update === "function") el._update(value);
}

function polar(cx, cy, r, deg) {
  const rad = (deg - 90) * Math.PI / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
function describeArc(cx, cy, r, start, end) {
  const s = polar(cx, cy, r, end);
  const e = polar(cx, cy, r, start);
  const large = end - start <= 180 ? "0" : "1";
  return `M ${s.x} ${s.y} A ${r} ${r} 0 ${large} 0 ${e.x} ${e.y}`;
}

// Editor-only placeholder for window_list (the server expands it to buttons
// before the tablet ever sees the layout, so the runtime should never hit this
// — but if it does, render a clear stub).
function renderWindowList(w, _emit) {
  const el = document.createElement("div");
  el.className = "dd-widget dd-window-list-stub";
  el.style.border = "1px dashed var(--accent)";
  el.style.background = "var(--surface)";
  el.style.color = "var(--text-dim)";
  el.style.fontFamily = "var(--font-mono, monospace)";
  el.style.fontSize = "12px";
  el.style.display = "flex";
  el.style.alignItems = "center";
  el.style.justifyContent = "center";
  el.style.textAlign = "center";
  el.style.padding = "12px";
  const p = w.props || {};
  el.textContent = `▦ window_list · source: ${p.source || "process_windows"} · ${p.columns || 2} cols`;
  return el;
}

// ───────── Value Ladder ─────────
// Hold to activate. Vertical drag picks a magnitude from a stacked list
// (TD-style: 10 / 1 / 0.1 / 0.01 / 0.001). The first horizontal motion
// past a lock threshold makes the stack disappear and starts streaming
// per-step nudges. Each `STEP_PX` horizontal pixels = one step at the
// chosen magnitude. Release closes the ladder.
function renderValueLadder(w, emit) {
  const p = w.props || {};
  const magnitudes = p.magnitudes || [10, 1, 0.1, 0.01, 0.001];
  const STEP_PX = p.step_px || 14;
  const LOCK_PX = p.lock_px || 18;
  const ROW_PX  = p.row_px  || 44;

  const el = document.createElement("button");
  el.className = "dd-widget dd-value-ladder";
  el.type = "button";

  const lbl = document.createElement("span");
  lbl.className = "dd-value-ladder-label";
  lbl.textContent = p.label || "±";
  el.appendChild(lbl);

  // Floating stack rendered inside the widget (overflow:visible above it).
  const stack = document.createElement("div");
  stack.className = "dd-ladder-stack";
  for (const mag of magnitudes) {
    const row = document.createElement("div");
    row.className = "dd-ladder-row";
    row.textContent = String(mag);
    stack.appendChild(row);
  }
  el.appendChild(stack);

  let dragging = false;
  let phase = "off";              // off | vertical | horizontal
  let chosenIdx = Math.floor(magnitudes.length / 2);
  let startX = 0, startY = 0;
  let cursorX = 0;                // running x reference for delta math

  function highlight() {
    for (let i = 0; i < stack.children.length; i++) {
      stack.children[i].classList.toggle("is-on", i === chosenIdx);
    }
  }

  // Block long-press context menu (Android Chrome shows text-selection /
  // "Open in new tab" by default on a held button).
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  // Block the text-select callout that fires on iOS long-press.
  el.addEventListener("selectstart", (e) => e.preventDefault());

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    phase = "vertical";
    chosenIdx = Math.floor(magnitudes.length / 2);
    startX = e.clientX;
    startY = e.clientY;
    cursorX = e.clientX;
    el.classList.add("is-active");
    stack.classList.add("is-visible");
    highlight();
    try { el.setPointerCapture(e.pointerId); } catch {}
    if (navigator.vibrate) navigator.vibrate(8);
  });

  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;

    if (phase === "vertical") {
      const center = Math.floor(magnitudes.length / 2);
      // dy > 0 (moved down) → smaller magnitudes (lower in stack)
      let idx = center + Math.round(dy / ROW_PX);
      idx = Math.max(0, Math.min(magnitudes.length - 1, idx));
      if (idx !== chosenIdx) { chosenIdx = idx; highlight(); }

      if (Math.abs(dx) > LOCK_PX) {
        phase = "horizontal";
        stack.classList.remove("is-visible");
        cursorX = e.clientX;
        if (navigator.vibrate) navigator.vibrate(6);
      }
    } else if (phase === "horizontal") {
      const dxSince = e.clientX - cursorX;
      const steps = Math.trunc(dxSince / STEP_PX);
      if (steps !== 0) {
        const mag = magnitudes[chosenIdx];
        const delta = steps * mag;
        // Cap excessive precision so server JSON stays clean
        const clean = Number(delta.toFixed(6));
        emit({ t: "value", id: w.id, value: clean });
        cursorX += steps * STEP_PX;
      }
    }
  });

  const finish = (e) => {
    if (!dragging) return;
    dragging = false;
    phase = "off";
    el.classList.remove("is-active");
    stack.classList.remove("is-visible");
    try { el.releasePointerCapture(e.pointerId); } catch {}
  };
  el.addEventListener("pointerup", finish);
  el.addEventListener("pointercancel", finish);

  return el;
}

// ───────── Param Panel (live editor for a TD op's pars, page-tabbed) ─────────
function renderParamPanel(w, emit) {
  const el = document.createElement("div");
  el.className = "dd-widget dd-param-panel";
  const header = document.createElement("div");
  header.className = "dd-pp-header";
  const opLbl = document.createElement("div");
  opLbl.className = "dd-pp-op";
  opLbl.textContent = "(no op selected)";
  header.appendChild(opLbl);
  el.appendChild(header);
  const tabs = document.createElement("div");
  tabs.className = "dd-pp-tabs";
  el.appendChild(tabs);
  const body = document.createElement("div");
  body.className = "dd-pp-body";
  el.appendChild(body);

  let state = { op: null, pages: [], activeIdx: 0, highlight: null };

  function renderRows() {
    body.innerHTML = "";
    const page = state.pages[state.activeIdx];
    if (!page) {
      body.innerHTML = "<div class='dd-pp-empty'>(no pars)</div>";
      return;
    }
    const path = state.op ? state.op.path : "";
    for (const par of (page.pars || [])) {
      body.appendChild(renderParRow(par, path, state.highlight, emit));
    }
  }

  function renderTabs() {
    tabs.innerHTML = "";
    state.pages.forEach((pg, i) => {
      const t = document.createElement("button");
      t.className = "dd-pp-tab" + (i === state.activeIdx ? " is-active" : "");
      t.textContent = pg.label || pg.name || "?";
      t.addEventListener("click", () => {
        if (state.activeIdx === i) return;
        state.activeIdx = i;
        renderTabs();
        renderRows();
      });
      tabs.appendChild(t);
    });
  }

  el._patch = (patch) => {
    let needRows = false, needTabs = false;
    if ("op" in patch) {
      state.op = patch.op;
      opLbl.textContent = patch.op
        ? `${patch.op.name}  ·  ${patch.op.type || ""}  ·  ${patch.op.path}`
        : "(no op selected)";
    }
    if ("pages" in patch) {
      state.pages = patch.pages || [];
      // Keep the same active page across re-emits if it still exists.
      if (state.activeIdx >= state.pages.length) state.activeIdx = 0;
      needTabs = true;
      needRows = true;
    }
    if ("highlight" in patch) {
      state.highlight = patch.highlight;
      // Cheap path: toggle the class on existing rows instead of rebuilding.
      body.querySelectorAll(".dd-pp-row").forEach((row) => {
        row.classList.toggle("is-hot", row.dataset.par === state.highlight);
      });
    }
    if (needTabs) renderTabs();
    if (needRows) renderRows();
  };

  // Initial empty render
  renderTabs();
  renderRows();
  return el;
}

function renderParRow(par, opPath, highlight, emit) {
  const row = document.createElement("div");
  row.className = "dd-pp-row";
  row.dataset.par = par.name || "";
  if (highlight && highlight === par.name) row.classList.add("is-hot");

  const lbl = document.createElement("div");
  lbl.className = "dd-pp-lbl";
  lbl.textContent = par.label || par.name || "?";
  row.appendChild(lbl);

  const ctl = document.createElement("div");
  ctl.className = "dd-pp-ctl";
  ctl.appendChild(parRowControl(par, opPath, emit));
  row.appendChild(ctl);

  return row;
}

function parRowControl(par, opPath, emit) {
  const style = par.style || "Float";
  const wid = "td_pars";  // outbound value events route through this widget id
  const send = (value) => {
    emit({ t: "value", id: wid, value,
           payload: { path: opPath, par: par.name, style } });
  };

  if (style === "Toggle") {
    const btn = document.createElement("button");
    btn.className = "dd-pp-toggle";
    let on = !!par.value;
    btn.classList.toggle("is-on", on);
    btn.textContent = on ? "ON" : "OFF";
    btn.addEventListener("click", () => {
      on = !on;
      btn.classList.toggle("is-on", on);
      btn.textContent = on ? "ON" : "OFF";
      send(on);
    });
    return btn;
  }

  if (style === "Menu") {
    const sel = document.createElement("select");
    sel.className = "dd-pp-menu";
    const opts = par.menu || [];
    for (const m of opts) {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = m.label || m.name;
      if (m.name === par.value) o.selected = true;
      sel.appendChild(o);
    }
    sel.addEventListener("change", () => send(sel.value));
    return sel;
  }

  if (style === "Str") {
    const wrap = document.createElement("div");
    wrap.className = "dd-pp-str";
    const inp = document.createElement("input");
    inp.type = "text";
    inp.value = (par.value ?? "");
    inp.addEventListener("change", () => send(inp.value));
    wrap.appendChild(inp);
    return wrap;
  }

  // Float / Int / anything numeric → mini slider in real par units.
  const wrap = document.createElement("div");
  wrap.className = "dd-pp-slider";
  let val = (typeof par.value === "number") ? par.value :
            (par.value != null ? Number(par.value) : 0);
  let nmin = (typeof par.normMin === "number") ? par.normMin : 0;
  let nmax = (typeof par.normMax === "number") ? par.normMax : 1;
  if (nmax <= nmin) { nmin = val - 1; nmax = val + 1; }
  let lo = Math.min(nmin, val);
  let hi = Math.max(nmax, val);
  if (par.clampMin != null) lo = Math.max(lo, Number(par.clampMin));
  if (par.clampMax != null) hi = Math.min(hi, Number(par.clampMax));
  if (hi <= lo) hi = lo + 1;
  const isInt = (style === "Int");
  const step = isInt ? 1 : Math.max((hi - lo) / 1000, 1e-4);

  const track = document.createElement("div");
  track.className = "dd-pp-slider-track";
  const fill = document.createElement("div");
  fill.className = "dd-pp-slider-fill";
  track.appendChild(fill);
  const readout = document.createElement("div");
  readout.className = "dd-pp-slider-val";

  function fmt(v) {
    if (isInt) return `${Math.round(v)}`;
    const ax = Math.abs(v);
    if (ax >= 100 || ax === 0) return v.toFixed(2);
    if (ax >= 1) return v.toFixed(3);
    return v.toFixed(4);
  }
  function paint(v) {
    const pct = (v - lo) / (hi - lo);
    fill.style.width = `${Math.max(0, Math.min(1, pct)) * 100}%`;
    readout.textContent = fmt(v);
  }
  paint(val);

  let dragging = false, lastSent = null, throttleTimer = null;
  function flush() {
    throttleTimer = null;
    if (val === lastSent) return;
    lastSent = val;
    send(isInt ? Math.round(val) : val);
  }
  function setFromEvent(e) {
    const rect = track.getBoundingClientRect();
    let pct = (e.clientX - rect.left) / rect.width;
    pct = Math.max(0, Math.min(1, pct));
    let nv = lo + pct * (hi - lo);
    nv = Math.round(nv / step) * step;
    val = Math.max(lo, Math.min(hi, nv));
    paint(val);
    if (throttleTimer === null) throttleTimer = setTimeout(flush, 70);
  }
  track.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    try { track.setPointerCapture(e.pointerId); } catch {}
    setFromEvent(e);
  });
  track.addEventListener("pointermove", (e) => { if (dragging) setFromEvent(e); });
  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch {}
    if (throttleTimer !== null) { clearTimeout(throttleTimer); throttleTimer = null; }
    flush();
  };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);

  // Inline value ladder for this row — same gesture as the standalone
  // ladder widget, but emits a delta with the row's own path/par so the
  // server's td_nudge_par can resolve it without $rollover.
  const ladder = makeInlineLadder({
    isInt,
    onNudge: (delta) => {
      emit({
        t: "value",
        id: "td_pars",
        value: delta,
        payload: { path: opPath, par: par.name, style, nudge: true },
      });
    },
  });

  wrap.appendChild(track);
  wrap.appendChild(ladder);
  wrap.appendChild(readout);
  return wrap;
}

// Compact value-ladder button used inside param-panel rows. Hold the
// button, drag vertically to pick a magnitude from the stack (10 / 1 /
// 0.1 / 0.01 / 0.001 by default — narrower set for Int pars), then drag
// horizontally past LOCK_PX to lock and stream nudges every STEP_PX
// of horizontal travel. Release to end.
function makeInlineLadder({ isInt, onNudge }) {
  const magnitudes = isInt ? [100, 10, 1] : [10, 1, 0.1, 0.01, 0.001];
  const STEP_PX = 14;
  const LOCK_PX = 18;
  const ROW_PX  = 36;

  const el = document.createElement("button");
  el.type = "button";
  el.className = "dd-pp-ladder";
  el.textContent = "±";

  const stack = document.createElement("div");
  stack.className = "dd-pp-ladder-stack";
  magnitudes.forEach((m, i) => {
    const row = document.createElement("div");
    row.className = "dd-pp-ladder-row";
    row.textContent = String(m);
    row.dataset.idx = i;
    stack.appendChild(row);
  });
  el.appendChild(stack);

  // Suppress every long-press default Android Chrome / iOS might fire.
  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("selectstart", (e) => e.preventDefault());

  let dragging = false, phase = "off";
  let chosenIdx = Math.floor(magnitudes.length / 2);
  let startX = 0, startY = 0, cursorX = 0;

  function highlight() {
    stack.querySelectorAll(".dd-pp-ladder-row").forEach((r, i) => {
      r.classList.toggle("is-on", i === chosenIdx);
    });
  }

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    phase = "vertical";
    chosenIdx = Math.floor(magnitudes.length / 2);
    startX = e.clientX; startY = e.clientY; cursorX = e.clientX;
    el.classList.add("is-active");
    stack.classList.add("is-visible");
    highlight();
    try { el.setPointerCapture(e.pointerId); } catch {}
    if (navigator.vibrate) navigator.vibrate(8);
  });

  el.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (phase === "vertical") {
      // Pick magnitude based on Y delta from start; up = larger idx (smaller value).
      const center = Math.floor(magnitudes.length / 2);
      const offset = Math.round(dy / ROW_PX);
      chosenIdx = Math.max(0, Math.min(magnitudes.length - 1, center + offset));
      highlight();
      if (Math.abs(dx) > LOCK_PX) {
        phase = "horizontal";
        stack.classList.remove("is-visible");
        cursorX = e.clientX;
      }
    } else if (phase === "horizontal") {
      const dxSince = e.clientX - cursorX;
      const steps = Math.trunc(dxSince / STEP_PX);
      if (steps !== 0) {
        const mag = magnitudes[chosenIdx];
        const delta = Number((steps * mag).toFixed(6));
        onNudge(delta);
        cursorX += steps * STEP_PX;
      }
    }
  });

  const finish = (e) => {
    if (!dragging) return;
    dragging = false;
    phase = "off";
    el.classList.remove("is-active");
    stack.classList.remove("is-visible");
    try { el.releasePointerCapture(e.pointerId); } catch {}
  };
  el.addEventListener("pointerup", finish);
  el.addEventListener("pointercancel", finish);

  return el;
}

const RENDERERS = {
  button: renderButton,
  label: renderLabel,
  slider: renderSlider,
  textbox: renderTextbox,
  input: renderInput,
  rotary: renderRotary,
  window_list: renderWindowList,
  value_ladder: renderValueLadder,
  param_panel: renderParamPanel,
};

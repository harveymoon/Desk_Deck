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
    if ("value" in patch) setSliderValue(el, widget, patch.value);
  } else if (widget.type === "rotary") {
    if ("value" in patch) setRotaryValue(el, widget, patch.value);
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
  const min = p.min ?? 0;
  const max = p.max ?? 100;
  const step = p.step ?? 1;
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
    const snapped = Math.round((v - min) / step) * step + min;
    return Math.max(min, Math.min(max, snapped));
  }
  function valueToPct(v) {
    return max === min ? 0 : (v - min) / (max - min);
  }
  function pctToValue(pct) {
    return quantize(min + pct * (max - min));
  }

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
    valEl.textContent = Number.isInteger(step) ? `${value}` : value.toFixed(2);
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

const RENDERERS = {
  button: renderButton,
  label: renderLabel,
  slider: renderSlider,
  textbox: renderTextbox,
  input: renderInput,
  rotary: renderRotary,
  window_list: renderWindowList,
};

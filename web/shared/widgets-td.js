// TouchDesigner widget plugin bundle.
//
// PLUGIN ARCHITECTURE: this is the canonical example of a client-side
// plugin. All TD-specific renderers + the HSV colour picker popup live
// here. The core (widgets.js) knows nothing about TouchDesigner — it
// just exposes registerRenderer() / registerUpdater(), which this file
// calls at import time. See ARCHITECTURE.md.
//
// To add a new integration's renderers: create a sibling
// widgets-<plugin>.js, import { registerRenderer, registerUpdater }
// from "./widgets.js", and add it to runtime.js's static imports.

import { registerRenderer, registerUpdater } from "./widgets.js";

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
  let phase = "off";
  let chosenIdx = Math.floor(magnitudes.length / 2);
  let startX = 0, startY = 0;
  let cursorX = 0;

  function highlight() {
    for (let i = 0; i < stack.children.length; i++) {
      stack.children[i].classList.toggle("is-on", i === chosenIdx);
    }
  }

  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("selectstart", (e) => e.preventDefault());

  let dragPointerId = null;

  function onDocMove(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (phase === "vertical") {
      const center = Math.floor(magnitudes.length / 2);
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
        const clean = Number(delta.toFixed(6));
        emit({ t: "value", id: w.id, value: clean });
        cursorX += steps * STEP_PX;
      }
    }
  }
  function onDocEnd(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    dragging = false;
    phase = "off";
    dragPointerId = null;
    el.classList.remove("is-active");
    stack.classList.remove("is-visible");
    document.removeEventListener("pointermove", onDocMove);
    document.removeEventListener("pointerup",     onDocEnd);
    document.removeEventListener("pointercancel", onDocEnd);
    try { el.releasePointerCapture(e.pointerId); } catch {}
  }

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    phase = "vertical";
    dragPointerId = e.pointerId;
    chosenIdx = Math.floor(magnitudes.length / 2);
    startX = e.clientX;
    startY = e.clientY;
    cursorX = e.clientX;
    el.classList.add("is-active");
    const r = el.getBoundingClientRect();
    stack.style.left = `${r.left + r.width / 2}px`;
    stack.style.bottom = `${window.innerHeight - r.top + 8}px`;
    stack.classList.add("is-visible");
    highlight();
    try { el.setPointerCapture(e.pointerId); } catch {}
    document.addEventListener("pointermove", onDocMove);
    document.addEventListener("pointerup",     onDocEnd);
    document.addEventListener("pointercancel", onDocEnd);
    if (navigator.vibrate) navigator.vibrate(8);
  });

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
    const groups = groupParsByTuplet(page.pars || []);
    for (const g of groups) {
      body.appendChild(renderGroupRow(g, path, state.highlight, emit));
    }
  }

  function syncRowsInPlace() {
    const page = state.pages[state.activeIdx];
    if (!page) return;
    const groups = groupParsByTuplet(page.pars || []);
    const rows = body.querySelectorAll(".dd-pp-row");
    rows.forEach((row, i) => {
      const g = groups[i];
      if (!g) return;
      if (typeof row._syncFromGroup === "function") row._syncFromGroup(g);
    });
  }

  function structureMatches(prev, next) {
    if (!prev || !next) return false;
    const a = prev[state.activeIdx];
    const b = next[state.activeIdx];
    if (!a || !b) return false;
    const ag = groupParsByTuplet(a.pars || []);
    const bg = groupParsByTuplet(b.pars || []);
    if (ag.length !== bg.length) return false;
    for (let i = 0; i < ag.length; i++) {
      if (ag[i].name !== bg[i].name) return false;
      if (ag[i].size !== bg[i].size) return false;
      if (ag[i].style !== bg[i].style) return false;
      const ap = ag[i].pars, bp = bg[i].pars;
      if (ap.length !== bp.length) return false;
      for (let j = 0; j < ap.length; j++) {
        if (ap[j].name !== bp[j].name) return false;
        if (ap[j].style !== bp[j].style) return false;
      }
    }
    return true;
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
    let needTabs = false;
    let pagesChanged = false;
    let prevPages = null;
    if ("op" in patch) {
      state.op = patch.op;
      opLbl.textContent = patch.op
        ? `${patch.op.name}  ·  ${patch.op.type || ""}  ·  ${patch.op.path}`
        : "(no op selected)";
    }
    if ("pages" in patch) {
      prevPages = state.pages;
      state.pages = patch.pages || [];
      const prevActiveName = (prevPages && prevPages[state.activeIdx]) ? prevPages[state.activeIdx].name : null;
      if (prevActiveName) {
        const newIdx = state.pages.findIndex(p => p.name === prevActiveName);
        if (newIdx >= 0) state.activeIdx = newIdx;
      }
      if (state.activeIdx >= state.pages.length) state.activeIdx = 0;
      const tabsSig = (arr) => (arr || []).map(p => p.name).join("|");
      if (tabsSig(prevPages) !== tabsSig(state.pages)) needTabs = true;
      pagesChanged = true;
    }
    if ("highlight" in patch) {
      state.highlight = patch.highlight;
      body.querySelectorAll(".dd-pp-row").forEach((row) => {
        const names = row._parNames || [];
        row.classList.toggle("is-hot", state.highlight != null && names.indexOf(state.highlight) >= 0);
      });
    }
    if (needTabs) renderTabs();
    if (pagesChanged) {
      if (structureMatches(prevPages, state.pages)) {
        syncRowsInPlace();
      } else {
        renderRows();
      }
    }
  };

  renderTabs();
  renderRows();
  return el;
}

function groupParsByTuplet(pars) {
  const groups = [];
  let i = 0;
  while (i < pars.length) {
    const colour = tryColorTuplet(pars, i);
    if (colour) { groups.push(colour); i += colour.pars.length; continue; }
    const p = pars[i];
    const t = p.tuplet;
    if (t && t.size > 1) {
      const g = { name: t.name, label: t.label || t.name, style: t.style || null, size: t.size, pars: [] };
      while (i < pars.length && pars[i].tuplet && pars[i].tuplet.name === t.name && g.pars.length < t.size) {
        g.pars.push(pars[i]); i++;
      }
      groups.push(g);
      continue;
    }
    groups.push({ name: p.name, label: p.label || p.name, style: p.style, size: 1, pars: [p] });
    i++;
  }
  return groups;
}

function tryColorTuplet(pars, start) {
  if (start + 2 >= pars.length) return null;
  const a = pars[start], b = pars[start + 1], c = pars[start + 2];
  const isNum = (x) => x && (x.style === "Float" || x.style === "Int" || x.style === "RGB" || x.style === "RGBA");
  if (!isNum(a) || !isNum(b) || !isNum(c)) return null;
  const aN = (a.name || "").toLowerCase();
  const bN = (b.name || "").toLowerCase();
  const cN = (c.name || "").toLowerCase();
  if (aN.length < 2 || bN.length < 2 || cN.length < 2) return null;
  if (aN.slice(-1) !== "r" || bN.slice(-1) !== "g" || cN.slice(-1) !== "b") return null;
  const base = aN.slice(0, -1);
  if (bN.slice(0, -1) !== base || cN.slice(0, -1) !== base) return null;

  let chans = [a, b, c];
  let style = "RGB";
  if (start + 3 < pars.length) {
    const d = pars[start + 3];
    if (isNum(d) && (d.name || "").toLowerCase() === base + "a") {
      chans = [a, b, c, d];
      style = "RGBA";
    }
  }
  const tupletLabel = (a.tuplet && a.tuplet.label) || null;
  const label = tupletLabel || (base.charAt(0).toUpperCase() + base.slice(1) || base);
  return { name: base, label, style, size: chans.length, pars: chans };
}

function renderGroupRow(group, opPath, highlight, emit) {
  const isColor = group.size >= 3 && (group.style === "RGB" || group.style === "RGBA");
  if (isColor) return renderColorRow(group, opPath, highlight, emit);
  if (group.size === 1) return renderParRow(group.pars[0], opPath, highlight, emit);
  const row = document.createElement("div");
  row.className = "dd-pp-row dd-pp-row-tuplet";
  row.dataset.group = group.name;
  row._parNames = group.pars.map(p => p.name);
  const lbl = document.createElement("div");
  lbl.className = "dd-pp-lbl";
  lbl.textContent = group.label;
  row.appendChild(lbl);
  const ctl = document.createElement("div");
  ctl.className = "dd-pp-ctl dd-pp-tuplet-ctl";
  const subSyncs = [];
  for (const p of group.pars) {
    const sub = parRowControl(p, opPath, emit);
    const wrap = document.createElement("div");
    wrap.className = "dd-pp-tuplet-channel";
    const sl = document.createElement("div");
    sl.className = "dd-pp-tuplet-lbl";
    sl.textContent = p.label || p.name;
    wrap.appendChild(sl);
    wrap.appendChild(sub);
    ctl.appendChild(wrap);
    subSyncs.push(sub);
  }
  row.appendChild(ctl);
  row._syncFromGroup = (newGroup) => {
    newGroup.pars.forEach((np, i) => {
      const s = subSyncs[i];
      if (s && typeof s._syncValue === "function") s._syncValue(np);
    });
  };
  return row;
}

function renderParRow(par, opPath, highlight, emit) {
  const row = document.createElement("div");
  row.className = "dd-pp-row";
  row.dataset.par = par.name || "";
  row.dataset.group = par.name || "";
  row._parNames = [par.name];
  if (highlight && highlight === par.name) row.classList.add("is-hot");

  const lbl = document.createElement("div");
  lbl.className = "dd-pp-lbl";
  lbl.textContent = par.label || par.name || "?";
  row.appendChild(lbl);

  const ctl = document.createElement("div");
  ctl.className = "dd-pp-ctl";
  const control = parRowControl(par, opPath, emit);
  ctl.appendChild(control);
  row.appendChild(ctl);

  row._syncFromPar = (newPar) => {
    if (typeof control._syncValue === "function") control._syncValue(newPar);
  };
  row._syncFromGroup = (g) => {
    if (g && g.pars && g.pars[0]) row._syncFromPar(g.pars[0]);
  };

  return row;
}

function renderColorRow(group, opPath, highlight, emit) {
  const row = document.createElement("div");
  row.className = "dd-pp-row dd-pp-row-color";
  row.dataset.group = group.name;
  row._parNames = group.pars.map(p => p.name);
  if (highlight && group.pars.some(p => p.name === highlight)) {
    row.classList.add("is-hot");
  }

  const lbl = document.createElement("div");
  lbl.className = "dd-pp-lbl";
  lbl.textContent = group.label;
  row.appendChild(lbl);

  const ctl = document.createElement("div");
  ctl.className = "dd-pp-ctl dd-pp-color-ctl";

  const top = document.createElement("div");
  top.className = "dd-pp-color-main";

  const swatch = document.createElement("button");
  swatch.type = "button";
  swatch.className = "dd-pp-color-swatch";
  swatch.title = "Tap to open colour picker";

  const expand = document.createElement("button");
  expand.type = "button";
  expand.className = "dd-pp-color-expand";
  expand.textContent = "▶";
  expand.title = "Show channel sliders";

  const hex = document.createElement("div");
  hex.className = "dd-pp-color-hex";

  top.appendChild(swatch);
  top.appendChild(expand);
  top.appendChild(hex);
  ctl.appendChild(top);

  const channels = document.createElement("div");
  channels.className = "dd-pp-color-channels";
  channels.hidden = true;

  const channelControls = [];
  for (const par of group.pars) {
    const sub = document.createElement("div");
    sub.className = "dd-pp-channel-row";
    const sl = document.createElement("div");
    sl.className = "dd-pp-channel-lbl";
    sl.textContent = par.label || par.name || "?";
    sub.appendChild(sl);
    const ctrl = parRowControl(par, opPath, emit);
    sub.appendChild(ctrl);
    channels.appendChild(sub);
    channelControls.push({ par, control: ctrl });
  }
  ctl.appendChild(channels);
  row.appendChild(ctl);

  let currentPars = group.pars.slice();

  function chanVal(i) {
    const p = currentPars[i];
    if (!p) return 0;
    const v = (typeof p.value === "number") ? p.value : Number(p.value);
    return Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
  }
  function hexFromChans() {
    const toHex = (f) => Math.round(f * 255).toString(16).padStart(2, "0").toUpperCase();
    return "#" + toHex(chanVal(0)) + toHex(chanVal(1)) + toHex(chanVal(2));
  }
  function paint() {
    const h = hexFromChans();
    swatch.style.background = h;
    let summary = h;
    if (currentPars.length === 4) {
      const a = chanVal(3);
      summary += `  α ${a.toFixed(2)}`;
    }
    hex.textContent = summary;
  }
  paint();

  let expanded = false;
  function setExpanded(v) {
    expanded = !!v;
    channels.hidden = !expanded;
    expand.textContent = expanded ? "▼" : "▶";
    row.classList.toggle("is-expanded", expanded);
  }

  expand.addEventListener("click", (e) => {
    e.preventDefault();
    setExpanded(!expanded);
  });

  let pickerInstance = null;
  swatch.addEventListener("click", (e) => {
    e.preventDefault();
    if (pickerInstance) { pickerInstance.close(); pickerInstance = null; return; }
    const alphaPar = (currentPars.length === 4) ? currentPars[3] : null;
    pickerInstance = openColorPicker({
      anchor: swatch,
      hex: hexFromChans(),
      alpha: alphaPar ? Number(alphaPar.value) || 0 : null,
      onChange: (hex) => sendHex(hex),
      onAlpha: alphaPar ? (a) => {
        emit({
          t: "value", id: "td_pars",
          value: a,
          payload: { path: opPath, par: alphaPar.name, style: alphaPar.style || "Float" },
        });
        currentPars[3] = Object.assign({}, currentPars[3], { value: a });
        paint();
      } : null,
      onClose: () => { pickerInstance = null; },
    });
  });

  function sendHex(h) {
    if (!h || h.length < 7) return;
    const r = parseInt(h.slice(1, 3), 16) / 255;
    const g = parseInt(h.slice(3, 5), 16) / 255;
    const b = parseInt(h.slice(5, 7), 16) / 255;
    const chans = [r, g, b];
    for (let i = 0; i < Math.min(3, currentPars.length); i++) {
      const p = currentPars[i];
      emit({
        t: "value", id: "td_pars",
        value: chans[i],
        payload: { path: opPath, par: p.name, style: p.style || "Float" },
      });
    }
    for (let i = 0; i < Math.min(3, currentPars.length); i++) {
      currentPars[i] = Object.assign({}, currentPars[i], { value: chans[i] });
    }
    paint();
  }

  row._syncFromGroup = (newGroup) => {
    currentPars = newGroup.pars.slice();
    paint();
    newGroup.pars.forEach((np, i) => {
      const cc = channelControls[i];
      if (cc && typeof cc.control._syncValue === "function") cc.control._syncValue(np);
    });
  };

  return row;
}

function parRowControl(par, opPath, emit) {
  const style = par.style || "Float";
  const wid = "td_pars";
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
    btn._syncValue = (newPar) => {
      const v = !!newPar.value;
      if (v === on) return;
      on = v;
      btn.classList.toggle("is-on", on);
      btn.textContent = on ? "ON" : "OFF";
    };
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
    sel._syncValue = (newPar) => {
      if (newPar.value != null && sel.value !== String(newPar.value)) {
        sel.value = String(newPar.value);
      }
    };
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

  let dragging = false, lastSent = null, throttleTimer = null, dragPointerId = null;
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
  function onDocMove(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    setFromEvent(e);
  }
  function onDocEnd(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    dragging = false;
    dragPointerId = null;
    document.removeEventListener("pointermove", onDocMove);
    document.removeEventListener("pointerup",     onDocEnd);
    document.removeEventListener("pointercancel", onDocEnd);
    if (throttleTimer !== null) { clearTimeout(throttleTimer); throttleTimer = null; }
    flush();
  }
  track.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    dragPointerId = e.pointerId;
    try { track.setPointerCapture(e.pointerId); } catch {}
    document.addEventListener("pointermove", onDocMove);
    document.addEventListener("pointerup",     onDocEnd);
    document.addEventListener("pointercancel", onDocEnd);
    setFromEvent(e);
  });

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

  wrap.appendChild(ladder);
  wrap.appendChild(track);
  wrap.appendChild(readout);

  wrap._syncValue = (newPar) => {
    if (dragging) return;
    const nv = (typeof newPar.value === "number") ? newPar.value :
               (newPar.value != null ? Number(newPar.value) : val);
    if (Number.isNaN(nv)) return;
    if (Math.abs(nv - val) < 1e-9) return;
    val = nv;
    paint(val);
  };

  return wrap;
}

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

  el.addEventListener("contextmenu", (e) => e.preventDefault());
  el.addEventListener("selectstart", (e) => e.preventDefault());

  let dragging = false, phase = "off", dragPointerId = null;
  let chosenIdx = Math.floor(magnitudes.length / 2);
  let startX = 0, startY = 0, cursorX = 0;

  function highlight() {
    stack.querySelectorAll(".dd-pp-ladder-row").forEach((r, i) => {
      r.classList.toggle("is-on", i === chosenIdx);
    });
  }

  function onDocMove(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (phase === "vertical") {
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
  }
  function onDocEnd(e) {
    if (!dragging || e.pointerId !== dragPointerId) return;
    dragging = false;
    phase = "off";
    dragPointerId = null;
    el.classList.remove("is-active");
    stack.classList.remove("is-visible");
    document.removeEventListener("pointermove", onDocMove);
    document.removeEventListener("pointerup",     onDocEnd);
    document.removeEventListener("pointercancel", onDocEnd);
    try { el.releasePointerCapture(e.pointerId); } catch {}
  }

  el.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    dragging = true;
    phase = "vertical";
    dragPointerId = e.pointerId;
    chosenIdx = Math.floor(magnitudes.length / 2);
    startX = e.clientX; startY = e.clientY; cursorX = e.clientX;
    el.classList.add("is-active");
    const r = el.getBoundingClientRect();
    stack.style.left = `${r.left + r.width / 2}px`;
    stack.style.bottom = `${window.innerHeight - r.top + 6}px`;
    stack.classList.add("is-visible");
    highlight();
    try { el.setPointerCapture(e.pointerId); } catch {}
    document.addEventListener("pointermove", onDocMove);
    document.addEventListener("pointerup",     onDocEnd);
    document.addEventListener("pointercancel", onDocEnd);
    if (navigator.vibrate) navigator.vibrate(8);
  });

  return el;
}

// ─── HSV Colour Picker popup ──────────────────────────────────────
function openColorPicker({ anchor, hex, alpha, onChange, onAlpha, onClose }) {
  document.querySelectorAll(".dd-cp-backdrop").forEach(b => b.remove());

  const backdrop = document.createElement("div");
  backdrop.className = "dd-cp-backdrop";

  const popup = document.createElement("div");
  popup.className = "dd-cp-popup";

  const sv = document.createElement("div"); sv.className = "dd-cp-sv";
  const svInner = document.createElement("div"); svInner.className = "dd-cp-sv-inner";
  const svCursor = document.createElement("div"); svCursor.className = "dd-cp-cursor";
  sv.appendChild(svInner);
  sv.appendChild(svCursor);

  const hue = document.createElement("div"); hue.className = "dd-cp-hue";
  const hueCursor = document.createElement("div"); hueCursor.className = "dd-cp-cursor-h";
  hue.appendChild(hueCursor);

  let alphaEl = null, alphaCursor = null, alphaCheckerBg = null;
  if (alpha != null && onAlpha) {
    alphaEl = document.createElement("div"); alphaEl.className = "dd-cp-alpha";
    alphaCheckerBg = document.createElement("div"); alphaCheckerBg.className = "dd-cp-alpha-bg";
    alphaCursor = document.createElement("div"); alphaCursor.className = "dd-cp-cursor-h";
    alphaEl.appendChild(alphaCheckerBg);
    alphaEl.appendChild(alphaCursor);
  }

  const footer = document.createElement("div"); footer.className = "dd-cp-footer";
  const hexLbl = document.createElement("div"); hexLbl.className = "dd-cp-hex";
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "dd-cp-close";
  closeBtn.textContent = "×";
  footer.appendChild(hexLbl);
  footer.appendChild(closeBtn);

  popup.appendChild(sv);
  popup.appendChild(hue);
  if (alphaEl) popup.appendChild(alphaEl);
  popup.appendChild(footer);

  let [h0, s0, v0] = (() => {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;
    return rgbToHsv(r, g, b);
  })();
  let a0 = (alpha == null) ? 1 : alpha;

  function hslHue() { return `hsl(${h0}, 100%, 50%)`; }
  function paintSv() {
    svInner.style.background =
      `linear-gradient(to bottom, rgba(0,0,0,0), rgba(0,0,0,1)),` +
      `linear-gradient(to right, rgba(255,255,255,1), rgba(255,255,255,0)),` +
      hslHue();
    const r = sv.getBoundingClientRect();
    const cx = s0 * r.width;
    const cy = (1 - v0) * r.height;
    svCursor.style.left = `${cx}px`;
    svCursor.style.top  = `${cy}px`;
    const [rr, gg, bb] = hsvToRgb(h0, s0, v0);
    const lum = 0.2126 * rr + 0.7152 * gg + 0.0722 * bb;
    svCursor.style.borderColor = lum < 0.5 ? "#fff" : "#000";
  }
  function paintHue() {
    const r = hue.getBoundingClientRect();
    hueCursor.style.top = `${(h0 / 360) * r.height}px`;
  }
  function paintAlpha() {
    if (!alphaEl) return;
    const r = alphaEl.getBoundingClientRect();
    alphaCursor.style.top = `${(1 - a0) * r.height}px`;
    const [rr, gg, bb] = hsvToRgb(h0, s0, v0);
    const cstr = `rgb(${Math.round(rr*255)}, ${Math.round(gg*255)}, ${Math.round(bb*255)})`;
    alphaCheckerBg.style.background = `linear-gradient(to bottom, ${cstr}, transparent)`;
  }
  function currentHex() {
    const [rr, gg, bb] = hsvToRgb(h0, s0, v0);
    const toH = (f) => Math.round(Math.max(0, Math.min(1, f)) * 255).toString(16).padStart(2, "0").toUpperCase();
    return "#" + toH(rr) + toH(gg) + toH(bb);
  }
  function paintHex() { hexLbl.textContent = currentHex(); }
  function paintAll() { paintSv(); paintHue(); paintAlpha(); paintHex(); }

  function dragArea(el, onPos) {
    let id = null;
    function move(e) {
      if (e.pointerId !== id) return;
      const r = el.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      const y = Math.max(0, Math.min(1, (e.clientY - r.top)  / r.height));
      onPos(x, y);
    }
    function end(e) {
      if (e.pointerId !== id) return;
      id = null;
      document.removeEventListener("pointermove", move);
      document.removeEventListener("pointerup",     end);
      document.removeEventListener("pointercancel", end);
    }
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      id = e.pointerId;
      try { el.setPointerCapture(e.pointerId); } catch {}
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup",     end);
      document.addEventListener("pointercancel", end);
      const r = el.getBoundingClientRect();
      const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
      const y = Math.max(0, Math.min(1, (e.clientY - r.top)  / r.height));
      onPos(x, y);
    });
  }

  dragArea(sv, (x, y) => {
    s0 = x; v0 = 1 - y;
    paintAll();
    onChange(currentHex());
  });
  dragArea(hue, (_x, y) => {
    h0 = y * 360;
    paintAll();
    onChange(currentHex());
  });
  if (alphaEl) {
    dragArea(alphaEl, (_x, y) => {
      a0 = 1 - y;
      paintAlpha(); paintHex();
      onAlpha(a0);
    });
  }

  closeBtn.addEventListener("click", () => close());
  backdrop.addEventListener("pointerdown", (e) => { close(); });
  popup.addEventListener("pointerdown", (e) => e.stopPropagation());

  function close() {
    backdrop.remove();
    if (onClose) onClose();
  }

  backdrop.appendChild(popup);
  document.body.appendChild(backdrop);
  requestAnimationFrame(() => {
    const pr = popup.getBoundingClientRect();
    const top  = Math.max(8, (window.innerHeight - pr.height) / 2);
    const left = Math.max(8, (window.innerWidth  - pr.width)  / 2);
    popup.style.top  = `${top}px`;
    popup.style.left = `${left}px`;
    paintAll();
  });

  return { close };
}

function hsvToRgb(h, s, v) {
  const c = v * s;
  const hh = (h % 360) / 60;
  const x = c * (1 - Math.abs((hh % 2) - 1));
  let r, g, b;
  if (hh < 1)      { r = c; g = x; b = 0; }
  else if (hh < 2) { r = x; g = c; b = 0; }
  else if (hh < 3) { r = 0; g = c; b = x; }
  else if (hh < 4) { r = 0; g = x; b = c; }
  else if (hh < 5) { r = x; g = 0; b = c; }
  else             { r = c; g = 0; b = x; }
  const m = v - c;
  return [r + m, g + m, b + m];
}

function rgbToHsv(r, g, b) {
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const d = max - min;
  const v = max;
  const s = max === 0 ? 0 : d / max;
  let h = 0;
  if (d > 0) {
    if (max === r)      h = 60 * (((g - b) / d) % 6);
    else if (max === g) h = 60 * ((b - r) / d + 2);
    else                h = 60 * ((r - g) / d + 4);
    if (h < 0) h += 360;
  }
  return [h, s, v];
}

// ─── TD plugin registrations ──────────────────────────────────────
registerRenderer("value_ladder", renderValueLadder);
registerRenderer("param_panel",  renderParamPanel);

registerUpdater("value_ladder", (el, widget, patch) => {
  if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
  if ("disabled" in patch) {
    el.disabled = !!patch.disabled;
    el.classList.toggle("is-disabled", !!patch.disabled);
  }
});
registerUpdater("param_panel", (el, widget, patch) => {
  if (typeof el._patch === "function") el._patch(patch);
  if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
});

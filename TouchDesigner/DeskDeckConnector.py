"""DeskDeckConnector — TouchDesigner extension that bridges TD to the
Desk_Deck server over a single WebSocket.

Lives on Desk_Deck.tox. Pairs with these sibling ops inside the .tox:

  ws            (webSocketDAT)  — the network transport
  ws_callbacks  (datExecuteDAT) — onConnect / onReceiveText -> our handlers
  frame_tick    (executeDAT)    — onFrameEnd every N frames -> ext.Tick()
  log           (textDAT)       — append-only buffer; flushed to server each tick

And these custom parameters on the .tox parent (configurable per project):

  Server        str   e.g. "ws://192.168.1.161:8765"
  Token         str   the paired token from %APPDATA%\\Desk_Deck\\token

Protocol contract (matches server/td.py):

  TD -> server: {"t":"hello", ...}, {"t":"state", "kind":..., ...},
                {"t":"log", "line":...}, {"t":"ack", "id":..., "ok":...}
  server -> TD: {"t":"cmd", "id":..., "kind":..., ...}
                kinds: set_par / pulse / focus_op / macro /
                       subscribe / unsubscribe

No arbitrary Python over the wire — every inbound command is dispatched
through this class's `_handle_*` methods, so the surface is auditable.
"""

import json
import sys

import TDFunctions as TDF  # ships with TouchDesigner


class DeskDeckConnector:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp

        # Custom parameters on the .tox parent (page "Connect"):
        #   Netaddress     Str        host or ws://host[/path]
        #   Port           Int        the server port (e.g. 8765)
        #   Token          Str        paired token from %APPDATA%/Desk_Deck/token
        #   Streamtextport Toggle    if on, sys.stdout/stderr mirror to tablet log
        # Properties below are just fallbacks if those params don't exist yet.
        TDF.createProperty(self, "Netaddress", value="127.0.0.1",
                           dependable=True, readOnly=False)
        TDF.createProperty(self, "Port",  value=8765, dependable=True, readOnly=False)
        TDF.createProperty(self, "Token", value="",   dependable=True, readOnly=False)

        # State the connector diffs against to emit only on change.
        self._last_selected = None      # tuple of op paths
        # Set after we apply a set_par on the wire — forces the next tick
        # to re-emit the selected snapshot so the tablet's par-panel
        # values stay in sync with whatever just changed.
        self._selected_dirty = False
        # Unified rollover signature. Format:
        #   (kind_of, op_path, ident_name, value)
        # where kind_of ∈ {par, pargroup, page, op, panel, none}, ident_name
        # is par/pargroup/page name (or None for op/panel/none) and value is
        # only meaningful for kind_of=="par" (used to throttle jitter).
        self._last_rollover = None
        self._last_pane_path = None
        self._last_perf = None          # (fps, cook_ms, gpu)
        self._last_status = None        # ui.status string

        # Which state kinds the server has subscribed to. Only emit these.
        self._subs = set()

        # User-registered macros (name -> callable). Use RegisterMacro from
        # any TD script to wire one up. Inbound {"kind":"macro","name":...}
        # commands look up here. A handful of common operations are pre-
        # registered so the default tablet layout works out of the box.
        self._macros = {}
        self._register_builtin_macros()

        # Optional textport mirror — wraps sys.stdout/stderr so every print()
        # in TD also flows to the tablet's td_log textbox. Off by default;
        # toggled via the `Mirrorprint` custom parameter on the .tox parent.
        #
        # _td_native_stdout: captured at extension instantiation, BEFORE we
        # touch sys.stdout. This is the most reliable reference to TD's
        # actual textport sink — sys.__stdout__ is Python's preserved
        # interpreter-startup stdout, which in TD often isn't the textport
        # routing object. We restore to _td_native_stdout when the
        # Streamtextport toggle goes off.
        self._td_native_stdout = sys.stdout
        self._td_native_stderr = sys.stderr
        self._orig_stdout = None
        self._orig_stderr = None
        self._sync_print_mirror()

        # Diagnose missing log DAT once (rather than silently no-op every tick).
        self._log_dat_missing_warned = False

        # Debug counters / rate-limit.
        self._stats = {"tick": 0, "rx": 0, "tx": 0, "emit": 0}
        self._tick_log_every = 300      # ~once every 300 ticks = ~30s at 10Hz
        self._rx_verbose_until = 2      # raw-payload print only for the first 2 rx
        self._last_status_print = 0
        # Throttle for rollover value-only emits (identity changes always
        # emit instantly). Frame count since last value-only emit.
        self._last_rollover_emit_frame = -999

        # Reset Extensions case: the .tox is reloading Python but the ws is
        # already open. The real onConnect won't fire again, so re-send
        # hello manually after the comp finishes initializing — the server
        # treats every hello as "resync your subscribe cmds to me."
        try:
            ws = self.ownerComp.op("ws")
            if ws is not None and ws.par.active.eval():
                run("args[0].OnConnect()", self, delayFrames=2)
        except Exception:
            pass

    # ─────────── connection lifecycle ───────────

    def Connect(self):
        """Open the WebSocket. Reads Netaddress / Port / Token from the
        .tox parent's custom parameters.

        Netaddress accepts any of:
          127.0.0.1                       (just host)
          ws://192.168.1.161              (scheme + host; path auto-appended)
          ws://192.168.1.161/live?...     (full URL; used verbatim)

        Port comes from the separate Port param (Web Socket DAT splits
        host from port across two fields).
        """
        addr_raw = self._pp("Netaddress", self.Netaddress)
        port = self._pp("Port", self.Port)
        tok  = self._pp("Token", self.Token)

        if not addr_raw:
            self._dbg("Connect: Netaddress is empty — set the host on Desk_Deck.tox")
            return
        if not tok:
            self._dbg("Connect: WARNING — Token is empty. Server will reject "
                      "the WS with Forbidden. Paste %APPDATA%/Desk_Deck/token "
                      "into the Token parameter on Desk_Deck.tox.")

        try:
            port = int(port)
        except Exception:
            port = 8765

        from urllib.parse import urlsplit
        raw = str(addr_raw).strip()
        if "://" not in raw:
            raw = "ws://" + raw
        parts = urlsplit(raw)
        host = parts.hostname or "127.0.0.1"
        scheme = "wss" if parts.scheme in ("wss", "https") else "ws"

        # If the user supplied a path in Netaddress, use it as-is; otherwise
        # build the standard /live?device=touchdesigner&t=<token>.
        if parts.path and parts.path not in ("", "/"):
            path = parts.path + (("?" + parts.query) if parts.query else "")
        else:
            path = "/live?device=touchdesigner"
            if tok:
                path += f"&t={tok}"

        # Web Socket DAT wants the full URL (no port in URL) in netaddress,
        # port separately in port. Probe param names for build differences.
        address_url = f"{scheme}://{host}{path}"

        ws = op("ws")
        if ws is None:
            self._dbg("Connect: missing ws DAT — add a Web Socket DAT named 'ws'")
            return

        def setp(name, value):
            par = ws.par[name] if hasattr(ws.par, name) else None
            if par is None:
                return False
            try:
                par.val = value
                return True
            except Exception:
                return False

        addr_set = setp("netaddress", address_url) or setp("Netaddress", address_url) \
                   or setp("address", address_url)    or setp("Address", address_url)
        port_set = setp("port", port) or setp("Port", port)

        if not addr_set:
            self._dbg("Connect: ERROR — couldn't find an address parameter on the Web Socket DAT")
            return
        if not port_set:
            self._dbg(f"Connect: WARNING — no `port` parameter on Web Socket DAT; "
                      f"connection may try port 80 instead of {port}")

        ws.par.active = False  # cycle to force reconnect with new settings
        ws.par.active = True
        self._dbg(f"Connect: address={address_url}  port={port}")

        # Refresh the textport mirror in case Streamtextport changed since init.
        self._sync_print_mirror()

    def Disconnect(self):
        ws = op("ws")
        if ws is not None:
            ws.par.active = False
        self._dbg("Disconnect")

    def OnConnect(self):
        """Called by ws_callbacks.onConnect."""
        self._dbg("OnConnect — sending hello")
        self._send({
            "t":          "hello",
            "device":     "touchdesigner",
            "td_version": app.version,
            "project":    project.name or "(unsaved)",
        })

    def OnDisconnect(self):
        self._subs.clear()
        # Force-reset diff caches so state re-emits on reconnect.
        self._last_selected = None
        self._last_rollover = None
        self._last_pane_path = None
        self._last_perf = None
        self._dbg("OnDisconnect — cleared subs and diff caches")

    # ─────────── inbound: server → TD ───────────

    def OnRx(self, txt):
        """Called by ws_callbacks.onReceiveText for every inbound text frame."""
        self._stats["rx"] += 1
        if self._stats["rx"] <= self._rx_verbose_until:
            self._dbg(f"OnRx#{self._stats['rx']} (raw): {txt[:200]}")
        try:
            msg = json.loads(txt)
        except Exception as e:
            self._dbg(f"OnRx: bad json: {e}  raw={txt[:200]!r}")
            return
        if msg.get("t") != "cmd":
            return
        kind = msg.get("kind")
        cid  = msg.get("id")
        handler = getattr(self, f"_handle_{kind}", None)
        if not handler:
            self._dbg(f"OnRx: unknown cmd kind {kind!r}")
            self._ack(cid, False, f"unknown cmd kind {kind!r}")
            return
        try:
            handler(msg)
            # Skip the success ack for high-rate fire-and-forget kinds so
            # slider drags don't double the WS traffic. Errors always ack
            # so the server can log them.
            if kind not in ("set_par", "pulse"):
                self._ack(cid, True)
        except Exception as e:
            # Only log failures — successes are silent to keep the textport quiet.
            self._dbg(f"OnRx cmd FAILED: {kind} {self._short_args(msg)}  → {e}")
            self._ack(cid, False, str(e))

    def _short_args(self, msg):
        # Render a one-line summary of the interesting fields of an inbound cmd.
        keys = [k for k in msg if k not in ("t", "id", "kind")]
        bits = []
        for k in keys:
            v = msg[k]
            if isinstance(v, dict):
                v = "{...}"
            sv = repr(v)
            if len(sv) > 60:
                sv = sv[:57] + "...'"
            bits.append(f"{k}={sv}")
        return " ".join(bits)

    def _handle_set_par(self, msg):
        op_ = op(msg["path"])
        if op_ is None:
            raise KeyError(f"no op {msg['path']!r}")
        par = op_.par[msg["par"]]
        if par is None:
            raise KeyError(f"no par {msg['par']!r} on {msg['path']!r}")
        par.val = msg["value"]
        # If the edited par lives on the currently-selected op, ask the
        # next tick to re-emit the selected snapshot so the tablet's
        # par-panel readouts stay in sync with the new value.
        try:
            if self._last_selected and op_.path in self._last_selected:
                self._selected_dirty = True
        except Exception:
            pass

    def _handle_pulse(self, msg):
        op(msg["path"]).par[msg["par"]].pulse()

    def _handle_focus_op(self, msg):
        target = op(msg["path"])
        if target is None:
            raise KeyError(f"no op {msg['path']!r}")
        pane = ui.panes[0]
        pane.owner = target.parent()
        target.current = True

    def _handle_macro(self, msg):
        name = msg.get("name")
        fn = self._macros.get(name)
        if not fn:
            raise KeyError(f"no macro {name!r}")
        fn(**(msg.get("args") or {}))

    def _handle_subscribe(self, msg):
        what = msg.get("what")
        if not what:
            return
        was_in = what in self._subs
        self._subs.add(what)
        # Force a fresh emit on next tick by clearing the relevant cache.
        if   what == "selected":     self._last_selected     = None
        elif what == "rollover":     self._last_rollover     = None
        elif what == "pane_path":    self._last_pane_path    = None
        elif what == "perf":         self._last_perf         = None
        if not was_in:
            self._dbg(f"subscribed: {what}  now={sorted(self._subs)}")

    def _handle_unsubscribe(self, msg):
        what = msg.get("what")
        if not what:
            return
        if what in self._subs:
            self._subs.discard(what)
            self._dbg(f"unsubscribed: {what}  now={sorted(self._subs)}")

    # ─────────── outbound tick: TD → server ───────────

    def Tick(self):
        """Called every N frames by frame_tick.onFrameEnd. Diffs each enabled
        state kind and emits only when it changed."""
        self._stats["tick"] += 1
        if not self._is_open():
            if self._stats["tick"] % self._tick_log_every == 0:
                self._dbg(f"tick #{self._stats['tick']} — ws not open")
            return

        if "selected" in self._subs:
            self._diff_selected()
        if "rollover" in self._subs:
            self._diff_rollover()
        if "pane_path" in self._subs:
            self._diff_pane_path()
        if "perf" in self._subs:
            self._diff_perf()
        if "status" in self._subs:
            self._diff_status()

        # If Streamtextport is on, re-assert sys.stdout = op('log') in case
        # TD's per-textport-execution swap clobbered it. Cheap no-op when off.
        self._sync_print_mirror()

        # Always flush log buffer (server filters by subscriber list).
        self._flush_log()

        # Heartbeat so the textport shows we're alive without being too noisy.
        if self._stats["tick"] % self._tick_log_every == 0:
            self._dbg(f"tick #{self._stats['tick']}  "
                      f"subs={sorted(self._subs) or 'none'}  "
                      f"rx={self._stats['rx']} tx={self._stats['tx']} emit={self._stats['emit']}")

    def _diff_selected(self):
        ops = self._selected_ops()
        paths = tuple(o.path for o in ops if o is not None)
        # Re-emit when:
        #   - selection identity changed (new op picked), OR
        #   - we tablet-edited a par and need to refresh values (_selected_dirty)
        if paths == self._last_selected and not self._selected_dirty:
            return
        self._last_selected = paths
        self._selected_dirty = False
        self._stats["emit"] += 1
        # For the FIRST selected op, also dump all its pars (grouped by
        # page) so the tablet can render an editable parameter panel
        # without an extra round-trip. Cap so we don't blow the WS frame
        # on huge ops — the user can still edit anything visible there.
        pages_payload = []
        if ops:
            try:
                pages_payload = self._op_pages_snapshot(ops[0])
            except Exception as e:
                self._dbg(f"op_pages_snapshot failed: {e}")
        self._send({
            "t": "state", "kind": "selected",
            "ops": [self._op_brief(o) for o in ops if o is not None],
            "pages": pages_payload,
        })

    def _op_pages_snapshot(self, the_op, par_cap=400):
        """Return [{name, label, pars: [par_snapshot, ...]}] for an op.

        Skips invisible pars (those with .enable False AND no value the
        user could care about would be a stretch; we keep them all for
        now — invisible pars still display in TD). Caps total par count
        across all pages so a 1000-par shader doesn't crash the wire."""
        pages = []
        emitted = 0
        try:
            op_pages = list(getattr(the_op, "pages", []) or [])
        except Exception:
            op_pages = []
        for pg in op_pages:
            try:
                page_name  = getattr(pg, "name", "?")
                page_label = getattr(pg, "label", None) or page_name
                pars       = list(getattr(pg, "pars", []) or [])
            except Exception:
                continue
            page_pars = []
            for p in pars:
                if emitted >= par_cap:
                    break
                try:
                    page_pars.append(self._par_snapshot(p))
                    emitted += 1
                except Exception:
                    continue
            pages.append({"name": page_name, "label": page_label, "pars": page_pars})
            if emitted >= par_cap:
                break
        return pages

    def _selected_ops(self):
        """Return the user's current op selection.

        Primary: ui.panes.current.owner.selectedChildren — the TD API for
        'ops highlighted in the network the user is editing right now'.
        Works for plain clicks, shift-clicks, and box-select.

        Fallbacks cover builds / pane types where the primary path returns
        nothing.
        """
        # 1. Best: ui.panes.current → owner → selectedChildren
        try:
            cur_pane = ui.panes.current
            if cur_pane is not None:
                owner = getattr(cur_pane, "owner", None)
                if owner is not None:
                    sc = getattr(owner, "selectedChildren", None)
                    if sc:
                        return list(sc)
        except Exception:
            pass

        # Gather network panes for fallbacks
        candidates = []
        try:
            cp = ui.panes.current
            if cp is not None:
                candidates.append(cp)
        except Exception:
            pass
        try:
            ap = getattr(ui, "activePane", None)
            if ap is not None:
                candidates.append(ap)
        except Exception:
            pass
        try:
            candidates.extend(ui.panes)
        except Exception:
            pass

        seen = set()
        network_panes = []
        for pane in candidates:
            if pane is None or id(pane) in seen:
                continue
            seen.add(id(pane))
            if getattr(pane, "type", None) == "NetworkEditor":
                network_panes.append(pane)

        # 2. Any network pane owner with selectedChildren
        for pane in network_panes:
            try:
                owner = pane.owner
                sc = getattr(owner, "selectedChildren", None) if owner is not None else None
                if sc:
                    return list(sc)
            except Exception:
                continue

        # 3. pane.selected (set by box-select / shift-click)
        for pane in network_panes:
            try:
                sel = pane.selected or []
            except Exception:
                continue
            if sel:
                return sel

        # 4. pane.current (the single focused op)
        for pane in network_panes:
            try:
                cur = pane.current
            except Exception:
                continue
            if cur is not None:
                return [cur]

        return []

    def _diff_rollover(self):
        """Unified rollover via ui.rollover.

        TD's ui.rollover returns whatever's directly under the mouse with
        priority Par | ParGroup | Page | OP | PanelCOMP | None. We classify
        the returned object and emit ONE state message with a kind_of tag
        and a typed payload, so consumers (the slider, value ladder, help
        button, textbox) can pick what they understand.
        """
        try:
            ro = ui.rollover
        except Exception:
            ro = None

        kind_of, op_obj, payload_extra = self._classify_rollover(ro)
        op_brief = self._op_brief(op_obj) if op_obj is not None else None

        # Identity = anything that should trigger an immediate emit (kind
        # change, op change, par/group/page name change). Value lives only
        # in par-kind payload and is throttled separately.
        ident_name = None
        if kind_of == "par":
            ident_name = payload_extra.get("par", {}).get("name")
        elif kind_of == "pargroup":
            ident_name = payload_extra.get("pargroup", {}).get("name")
        elif kind_of == "page":
            ident_name = payload_extra.get("page", {}).get("name")
        val = None
        if kind_of == "par":
            val = payload_extra.get("par", {}).get("value")
        elif kind_of == "pargroup":
            # tuple-ify the values list so it's hashable for the signature
            vals = payload_extra.get("pargroup", {}).get("values") or []
            val = tuple(vals)

        sig = (kind_of, op_brief.get("path") if op_brief else None, ident_name, val)
        old = self._last_rollover
        if sig == old:
            return
        old_ident = old[:3] if old else None
        ident_changed = sig[:3] != old_ident
        now_frame = self._stats["tick"]
        # Same identity, value just jittered — throttle value-only emits.
        if not ident_changed and (now_frame - self._last_rollover_emit_frame) < 3:
            return
        self._last_rollover = sig
        self._last_rollover_emit_frame = now_frame
        self._stats["emit"] += 1
        msg = {
            "t": "state", "kind": "rollover",
            "kind_of": kind_of,
            "op": op_brief,
        }
        msg.update(payload_extra)
        self._send(msg)

    def _classify_rollover(self, ro):
        """Return (kind_of, op_obj_or_None, payload_extra_dict).

        kind_of priority follows TD's docs: Par > ParGroup > Page > OP >
        Panel. Identifying by class name (via MRO) avoids importing
        td.Par etc., which is brittle across TD builds.
        """
        if ro is None:
            return ("none", None, {})
        mro_names = {c.__name__ for c in type(ro).__mro__}

        if "Par" in mro_names:
            owner = getattr(ro, "owner", None)
            return ("par", owner, {"par": self._par_snapshot(ro)})

        if "ParGroup" in mro_names:
            owner = getattr(ro, "owner", None)
            return ("pargroup", owner, {"pargroup": self._pargroup_snapshot(ro)})

        if "Page" in mro_names:
            owner = getattr(ro, "owner", None)
            return ("page", owner, {"page": self._page_snapshot(ro)})

        # Anything else with a .path is an op-ish thing. Use ui.rolloverPanel
        # to distinguish a Panel hover from an OP-in-network hover (both can
        # be PanelCOMPs per the docs).
        if "OP" in mro_names or hasattr(ro, "path"):
            try:
                panel = ui.rolloverPanel
            except Exception:
                panel = None
            kind_of = "panel" if (panel is not None and panel is ro) else "op"
            return (kind_of, ro, {})

        return ("none", None, {})

    def _pargroup_snapshot(self, pg):
        try:
            pars = list(getattr(pg, "pars", []) or [])
            par_names = [getattr(p, "name", "?") for p in pars]
            values    = [self._safe_eval(p)      for p in pars]
            style = getattr(pg, "style", None)
            out = {
                "name":   getattr(pg, "name", None),
                "label":  getattr(pg, "label", None) or getattr(pg, "name", None),
                "style":  style,
                "pars":   par_names,
                "values": values,
                # normMin/normMax are per-par on TD's side. Borrow from
                # the first par so consumers can render a single-range
                # slider/picker without round-tripping.
                "normMin": getattr(pars[0], "normMin", None) if pars else None,
                "normMax": getattr(pars[0], "normMax", None) if pars else None,
            }
            # Color tag for RGB / RGBA pargroups so the tablet can decide
            # to render a colour swatch / picker. Values stay 0..1 floats
            # exactly as TD stores them — no conversion server-side.
            if style in ("RGB", "RGBA") and len(values) in (3, 4):
                try:
                    chans = [float(v) for v in values]
                    out["is_color"] = True
                    out["rgba"] = chans + ([1.0] if len(chans) == 3 else [])
                    out["hex"] = "#" + "".join(
                        f"{max(0, min(255, int(round(c * 255)))):02X}"
                        for c in chans[:3]
                    )
                except (TypeError, ValueError):
                    pass
            return out
        except Exception as e:
            return {"name": getattr(pg, "name", "?"), "error": str(e)}

    def _page_snapshot(self, pg):
        try:
            return {
                "name":     getattr(pg, "name", None),
                "label":    getattr(pg, "label", None) or getattr(pg, "name", None),
                "par_count": len(list(getattr(pg, "pars", []) or [])),
            }
        except Exception as e:
            return {"name": getattr(pg, "name", "?"), "error": str(e)}

    def _safe_eval(self, par):
        try:    return self._jsonable(par.eval())
        except Exception: return None

    def _jsonable(self, v):
        """Coerce a TD value into something json.dumps can handle.

        Par.eval() can return TD operator objects for CHOP/TOP/COMP-reference
        pars (style 'CHOP', 'TOP', 'OP', 'COMP', ...) — those aren't JSON
        serializable. We convert ops to their path string. Other unknown
        types fall back to str(). Lists/tuples are walked recursively so a
        pargroup value list with a stray op reference still survives."""
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        # TD operator → its path. Detect by duck-typing on .path; cheap
        # and works for OP, COMP, baseCOMP, panelCOMP, CHOP, TOP, etc.
        path = getattr(v, "path", None)
        if isinstance(path, str):
            return path
        if isinstance(v, (list, tuple)):
            return [self._jsonable(x) for x in v]
        if isinstance(v, dict):
            return {str(k): self._jsonable(val) for k, val in v.items()}
        try:
            return str(v)
        except Exception:
            return None

    def _diff_pane_path(self):
        try:
            path = ui.panes[0].owner.path
        except Exception:
            path = None
        if path == self._last_pane_path:
            return
        self._last_pane_path = path
        self._send({"t": "state", "kind": "pane_path", "path": path})

    def _diff_status(self):
        try:
            s = ui.status or ""
        except Exception:
            s = ""
        if s == self._last_status:
            return
        self._last_status = s
        self._stats["emit"] += 1
        self._send({"t": "state", "kind": "status", "text": s})

    def _diff_perf(self):
        try:
            fps = float(app.cookRate)  # actual cooks/second
        except Exception:
            fps = None
        cook = None
        try:
            cook = float(project.cookTime)  # ms per frame, project-level
        except Exception:
            pass
        gpu = None
        try:
            gpu = int(round(float(app.gpuMemoryUsed) / (1024 * 1024)))  # bytes → MB
        except Exception:
            pass
        sig = (round(fps, 1) if fps is not None else None,
               round(cook, 1) if cook is not None else None,
               gpu)
        if sig == self._last_perf:
            return
        self._last_perf = sig
        self._send({
            "t": "state", "kind": "perf",
            "fps": sig[0], "cook_ms": sig[1], "gpu_mem_mb": sig[2],
        })

    def _flush_log(self):
        log_dat = op("log")
        if log_dat is None:
            if not self._log_dat_missing_warned:
                self._dbg("Log: no `log` textDAT inside Desk_Deck.tox — "
                          "add one (named exactly 'log') and Log() output "
                          "will flow to the tablet.")
                self._log_dat_missing_warned = True
            return
        if log_dat.numRows == 0:
            return
        lines = [log_dat[r, 0].val for r in range(log_dat.numRows)]
        log_dat.clear()
        for line in lines:
            if line:
                self._send({"t": "log", "line": line})

    # ─────────── helpers ───────────

    def Log(self, msg):
        """Append a line to the textport-mirror buffer; flushed on next Tick.

        Always works regardless of the Mirrorprint toggle — that toggle
        only controls whether print()/debug() output is ALSO captured.
        Explicit Log() calls are always sent.
        """
        log_dat = op("log")
        if log_dat is None:
            debug(f"[DeskDeck:Log] (no log DAT) {msg}")
            return
        log_dat.appendRow([str(msg)])

    def SyncPrintMirror(self):
        """Public alias — call after toggling the Streamtextport parameter.

        Example from textport:
          op('Desk_Deck').par.Streamtextport = True
          op('Desk_Deck').SyncPrintMirror()
        """
        self._sync_print_mirror(force=True)

    def RepairStdout(self):
        """Force sys.stdout / sys.stderr back to TD's textport sink,
        regardless of the Streamtextport toggle or current state.

        Call from the textport if prints have stopped showing up there
        (e.g. you toggled Streamtextport off but it didn't catch):
          op('Desk_Deck').RepairStdout()

        Tries the native sink we captured at extension init first, then
        sys.__stdout__, then a placeholder so at least *something*
        coherent is wired up.
        """
        target_out = self._td_native_stdout or sys.__stdout__
        target_err = self._td_native_stderr or sys.__stderr__
        if target_out is not None: sys.stdout = target_out
        if target_err is not None: sys.stderr = target_err
        # Clear any cached install state so the next install starts clean.
        self._orig_stdout = None
        self._orig_stderr = None
        # Print AFTER restoring so the message lands in the textport.
        print(f"[DeskDeck] stdout repaired -> {type(sys.stdout).__name__}")

    def _sync_print_mirror(self, force=False):
        """Install / restore the sys.stdout & sys.stderr mirror based on
        the Streamtextport toggle. TD doesn't dispatch print() through a
        generic file-like wrapper — only through file-API-compliant objects
        like TextDATs. Setting sys.stdout directly to op('log') is the
        approach that actually works (TD swaps the textport DAT in
        the same way internally).

        TD also re-asserts its own stdout per textport execution, so the
        mirror has to be re-installed periodically — Tick() calls this
        every tick when Streamtextport is on so prints typed after a
        prior textport command still get captured.

        Restoration is more delicate than capture: TD's textport sink is
        whatever sys.__stdout__ was at interpreter start (Python preserves
        the original stdout reference in __stdout__, and TD's launcher
        wires that to the textport panel). We restore to *that* — the
        previously-captured value can be stale if TD rotated its sink
        between our install and the toggle-off."""
        want = bool(self._pp("Streamtextport", False))
        log_dat = op("log")

        if want and log_dat is None:
            if force:
                self._dbg("Streamtextport: can't mirror — no `log` textDAT in Desk_Deck.tox")
            return

        if want:
            # Re-install if TD swapped sys.stdout back to its own textport.
            # Re-capture the originals every install so we always have a
            # fresh reference if TD rotated its sink.
            if sys.stdout is not log_dat:
                self._orig_stdout = sys.stdout
                self._orig_stderr = sys.stderr
                sys.stdout = log_dat
                sys.stderr = log_dat
                if force:
                    self._dbg("Streamtextport ON — sys.stdout & stderr now write to op('log')")
        else:
            # Only restore if WE are the current sink; otherwise TD or
            # someone else has already swapped sys.stdout to something
            # appropriate and we shouldn't stomp it.
            #
            # Restoration priority (most-trusted first):
            #   1. _td_native_stdout — captured at extension __init__,
            #      which is the textport sink TD set up for THIS class.
            #   2. _orig_stdout — captured when we installed the mirror;
            #      can be a transient sink if TD rotated it, but better
            #      than nothing.
            #   3. sys.__stdout__ — Python's preserved interpreter-init
            #      stdout. Last resort; in TD this is often NOT the
            #      textport.
            if sys.stdout is log_dat:
                sys.stdout = (self._td_native_stdout
                              or self._orig_stdout
                              or sys.__stdout__
                              or sys.stdout)
            if sys.stderr is log_dat:
                sys.stderr = (self._td_native_stderr
                              or self._orig_stderr
                              or sys.__stderr__
                              or sys.stderr)
            self._orig_stdout = None
            self._orig_stderr = None
            if force:
                self._dbg(f"Streamtextport OFF — sys.stdout restored ({type(sys.stdout).__name__})")

    def RegisterMacro(self, name, fn):
        """Register a callable invokable from the tablet via
        action: {type: td_macro, name: "<name>"}."""
        self._macros[str(name)] = fn

    def _register_builtin_macros(self):
        """Pre-register macros for common TD operations so the default
        tablet layout works without the user adding anything. User-registered
        macros (via RegisterMacro) override these by name."""
        def perform_mode(**_):  ui.performMode = True
        def exit_perform(**_):  ui.performMode = False
        def toggle_perform(**_): ui.performMode = not ui.performMode
        def save_project(**_):
            try: project.save(project.saveName)
            except Exception: project.save()
        def open_textport(**_):
            try: ui.panes.current.changeType(PaneType.TEXTPORT)
            except Exception: pass
        def open_palette(**_):
            try: ui.panes.current.changeType(PaneType.PALETTE)
            except Exception: pass

        for name, fn in (
            ("perform_mode",   perform_mode),
            ("exit_perform",   exit_perform),
            ("toggle_perform", toggle_perform),
            ("save_project",   save_project),
            ("open_textport",  open_textport),
            ("open_palette",   open_palette),
        ):
            self._macros[name] = fn

    def _op_brief(self, o):
        if o is None:
            return None
        try:
            return {
                "path":   o.path,
                "name":   o.name,
                "type":   o.OPType,                          # e.g. "noiseCHOP"
                "family": (o.family or "").upper(),          # "CHOP" / "TOP" / ...
            }
        except Exception:
            return {"path": getattr(o, "path", None), "name": getattr(o, "name", None)}

    def _par_snapshot(self, par):
        try:
            style = par.style
            # Read both raw val (typed value or expression result) and eval()
            # (always evaluated number). For expression-mode pars they differ.
            # _jsonable() coerces OP / COMP / CHOP / TOP references to their
            # path string so the snapshot stays JSON-serialisable.
            try:    value = self._jsonable(par.eval())
            except Exception: value = None
            try:    raw_val = self._jsonable(par.val)
            except Exception: raw_val = None
            # TD's clampMin/clampMax pars are toggles + values; the actual
            # numeric clamp lives on `clampMinValue` / `clampMaxValue`. Read
            # whichever path the build exposes.
            cmin = self._first_attr(par, ("clampMinValue", "clampMin"))
            cmax = self._first_attr(par, ("clampMaxValue", "clampMax"))
            out = {
                "name":     par.name,
                "label":    par.label,
                "style":    style,
                "value":    value,
                "val":      raw_val,
                "normMin":  getattr(par, "normMin", None),
                "normMax":  getattr(par, "normMax", None),
                "clampMin": cmin,
                "clampMax": cmax,
            }
            if style == "Menu":
                names  = getattr(par, "menuNames",  None) or []
                labels = getattr(par, "menuLabels", None) or []
                out["menu"] = [{"name": n, "label": l} for n, l in zip(names, labels)]
            return out
        except Exception as e:
            return {"name": getattr(par, "name", "?"), "error": str(e)}

    def _first_attr(self, obj, names):
        for n in names:
            v = getattr(obj, n, None)
            if v is not None and not isinstance(v, bool):
                return v
        return None

    def _send(self, payload):
        ws = op("ws")
        if ws is None:
            self._dbg("_send: no ws DAT")
            return
        try:
            ws.sendText(json.dumps(payload))
            self._stats["tx"] += 1
        except Exception as e:
            self._dbg(f"_send failed: {e}")

    # ─────────── public diagnostics (call from the textport) ───────────

    def Status(self):
        """Dump a one-shot summary to the textport.

        Call:  op('Desk_Deck').Status()
        """
        ws = op("ws")
        ws_active = bool(ws and ws.par.active.eval()) if ws is not None else False
        subs_str = sorted(self._subs) if self._subs else "(none — server has not asked)"
        rx = self._stats["rx"]; tx = self._stats["tx"]
        em = self._stats["emit"]; tk = self._stats["tick"]
        print()
        print("[DeskDeck Status] ----------------------------------------")
        print(f"  ws DAT present:    {ws is not None}")
        print(f"  ws active:         {ws_active}")
        print(f"  server subscribed: {subs_str}")
        print(f"  stats:             rx={rx} tx={tx} emit={em} tick={tk}")
        print(f"  last selected:     {self._last_selected}")
        print(f"  last rollover:     {self._last_rollover}")
        print(f"  last pane_path:    {self._last_pane_path}")
        print(f"  last perf:         {self._last_perf}")
        print(f"  macros:            {sorted(self._macros)}")
        print("[DeskDeck Status] ----------------------------------------")
        print()

    def DumpWsParams(self):
        """List every parameter on the ws DAT — useful when the user's TD
        build exposes different names than we expect."""
        ws = op("ws")
        if ws is None:
            print("[DeskDeck] no ws DAT")
            return
        print(f"[DeskDeck] {ws.path} parameters:")
        for p in ws.pars():
            try:
                print(f"  {p.name:20s} = {p.eval()!r}")
            except Exception as e:
                print(f"  {p.name:20s} <eval error: {e}>")

    def ForceSubscribeAll(self):
        """For local testing without the server: pretend the server told us
        to stream every kind. Call once, then Tick() will emit state
        snapshots even before the server's `subscribe` cmd arrives."""
        for k in ("selected", "rollover", "perf", "pane_path", "status"):
            self._subs.add(k)
        self._last_selected = None
        self._last_rollover = None
        self._last_pane_path = None
        self._last_perf = None
        self._dbg(f"ForceSubscribeAll → subs={sorted(self._subs)}")

    def _ack(self, cid, ok, error=None):
        if cid is None:
            return
        msg = {"t": "ack", "id": cid, "ok": bool(ok)}
        if error is not None:
            msg["error"] = error
        self._send(msg)

    def _is_open(self):
        ws = op("ws")
        return bool(ws and ws.par.active.eval())

    def _pp(self, name, fallback):
        """Read a parameter off the .tox parent if it exists, else fallback."""
        try:
            par = self.ownerComp.par[name]
            if par is not None:
                v = par.eval()
                return v if v != "" else fallback
        except Exception:
            pass
        return fallback

    def _dbg(self, msg):
        debug(f"[DeskDeck] {msg}")

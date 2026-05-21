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

import TDFunctions as TDF  # ships with TouchDesigner


class DeskDeckConnector:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp

        # Configurable on the parent .tox custom params, with fallback defaults
        # if the user hasn't wired them yet.
        TDF.createProperty(self, "Server", value="ws://127.0.0.1:8765",
                           dependable=True, readOnly=False)
        TDF.createProperty(self, "Token", value="", dependable=True, readOnly=False)

        # State the connector diffs against to emit only on change.
        self._last_selected = None      # tuple of op paths
        self._last_rollover_op = None   # op path or None
        self._last_rollover_par = None  # (op_path, par_name, value) tuple
        self._last_pane_path = None
        self._last_perf = None          # (fps, cook_ms, gpu)

        # Which state kinds the server has subscribed to. Only emit these.
        self._subs = set()

        # User-registered macros (name -> callable). Use RegisterMacro from
        # any TD script to wire one up. Inbound {"kind":"macro","name":...}
        # commands look up here.
        self._macros = {}

        # Debug counters / rate-limit.
        self._stats = {"tick": 0, "rx": 0, "tx": 0, "emit": 0}
        self._tick_log_every = 300      # ~once every 300 ticks = ~30s at 10Hz
        self._rx_verbose_until = 2      # raw-payload print only for the first 2 rx
        self._last_status_print = 0
        # Throttle for rollover_par value-only emits (identity changes always
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
        """Open the WebSocket. Reads `Server` and `Token` from .tox params.

        Accepts `Server` in any of these forms:
          ws://192.168.1.161:8765        (scheme + host + port)
          192.168.1.161:8765             (no scheme — assumes ws://)
          192.168.1.161                  (just host — uses default port 8765)

        Web Socket DAT parameter shape varies between TD builds. We probe
        for a single `url` param first (modern); otherwise fall back to
        `netaddress` + `port` (older) and try a `path`-style param for the
        URL path. The chosen route is printed via debug() so you can sanity-
        check in the textport.
        """
        srv = self._pp("Server", self.Server)
        tok = self._pp("Token", self.Token)
        if not srv:
            self._dbg("Connect: no Server configured")
            return
        if not tok:
            self._dbg("Connect: WARNING — no Token. Server will reject the WS "
                      "with 'Forbidden'. Paste %APPDATA%\\Desk_Deck\\token into "
                      "the Token parameter on Desk_Deck.tox.")

        # Normalize the Server string into host / port / scheme.
        from urllib.parse import urlsplit
        raw = srv.strip()
        if "://" not in raw:
            raw = "ws://" + raw
        parts = urlsplit(raw)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (8765 if parts.scheme in ("ws", "http") else 443)
        secure = parts.scheme in ("wss", "https")

        path = f"/live?device=touchdesigner"
        if tok:
            path += f"&t={tok}"

        # Web Socket DAT's address parameter wants the full URL — scheme,
        # host, path, query — but WITHOUT the port (the port lives in the
        # separate `port` param). Verified against TD 2023+ Web Socket DAT.
        scheme = "wss" if secure else "ws"
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
                   or setp("address", address_url) or setp("Address", address_url)
        port_set = setp("port", port) or setp("Port", port)
        for s in ("Secure", "secure", "Usehttps"):
            if setp(s, secure):
                break

        if not addr_set:
            self._dbg("Connect: ERROR — couldn't find an address parameter on the Web Socket DAT")
            return
        if not port_set:
            self._dbg(f"Connect: WARNING — no `port` parameter on Web Socket DAT; "
                      f"connection may try port 80 instead of {port}")

        ws.par.active = False  # cycle to force reconnect with new settings
        ws.par.active = True
        self._dbg(f"Connect: address={address_url}  port={port}")

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
        self._last_rollover_op = None
        self._last_rollover_par = None
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
        elif what == "rollover_par": self._last_rollover_par = None
        elif what == "rollover_op":  self._last_rollover_op  = None
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
        if "rollover_op" in self._subs or "rollover_par" in self._subs:
            self._diff_rollover()
        if "pane_path" in self._subs:
            self._diff_pane_path()
        if "perf" in self._subs:
            self._diff_perf()

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
        if paths == self._last_selected:
            return
        self._last_selected = paths
        self._stats["emit"] += 1
        self._send({
            "t": "state", "kind": "selected",
            "ops": [self._op_brief(o) for o in ops if o is not None],
        })

    def _selected_ops(self):
        """Return the user's current op selection.

        TD distinguishes:
          - pane.selected — list of ops with selection rectangle (set by
            box-select or shift-click; a plain single click does NOT
            populate this in many builds).
          - pane.current  — the single op with focus (set by any click).

        We try selected first; if empty, fall back to current as a single-
        element list so the tablet still updates on a plain click.
        """
        candidates = []
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

        # 1. Pane with a non-empty selection wins.
        for pane in network_panes:
            try:
                sel = pane.selected or []
            except Exception:
                continue
            if sel:
                return sel

        # 2. Else use the current op of the first network pane.
        for pane in network_panes:
            try:
                cur = pane.current
            except Exception:
                continue
            if cur is not None:
                return [cur]

        # 3. Last resort: whatever the active pane's selected returns (may
        #    be empty — covers the deselect-everything case so the tablet
        #    clears its label).
        if candidates:
            try:
                return candidates[0].selected or []
            except Exception:
                pass
        return []

    def _diff_rollover(self):
        try:
            ro_op = ui.rolloverOp
        except Exception:
            ro_op = None
        try:
            ro_par = ui.rolloverPar
        except Exception:
            ro_par = None

        ro_op_path = ro_op.path if ro_op is not None else None
        if ro_op_path != self._last_rollover_op:
            self._last_rollover_op = ro_op_path
            self._stats["emit"] += 1
            self._send({
                "t": "state", "kind": "rollover_op",
                "op": self._op_brief(ro_op) if ro_op is not None else None,
            })

        # Build a cheap signature. Animated / expression pars re-evaluate to
        # a different float every tick — throttle value-only changes to
        # ~4 Hz so we don't flood the WS + textport. Identity changes
        # (different par hovered) always emit immediately.
        sig = None
        ident = None
        if ro_par is not None and ro_par.owner is not None:
            try:
                v = ro_par.eval()
            except Exception:
                v = None
            sig = (ro_par.owner.path, ro_par.name, v)
            ident = (ro_par.owner.path, ro_par.name)
        old = self._last_rollover_par
        if sig == old:
            return
        old_ident = (old[0], old[1]) if old else None
        ident_changed = ident != old_ident
        now_frame = self._stats["tick"]
        if not ident_changed and (now_frame - self._last_rollover_emit_frame) < 3:
            # Same par, value just jittered, last emit was <3 ticks ago — skip.
            return
        self._last_rollover_par = sig
        self._last_rollover_emit_frame = now_frame
        self._stats["emit"] += 1
        self._send({
            "t": "state", "kind": "rollover_par",
            "op":  self._op_brief(ro_par.owner) if ro_par is not None else None,
            "par": self._par_snapshot(ro_par)  if ro_par is not None else None,
        })

    def _diff_pane_path(self):
        try:
            path = ui.panes[0].owner.path
        except Exception:
            path = None
        if path == self._last_pane_path:
            return
        self._last_pane_path = path
        self._send({"t": "state", "kind": "pane_path", "path": path})

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
        if log_dat is None or log_dat.numRows == 0:
            return
        lines = [log_dat[r, 0].val for r in range(log_dat.numRows)]
        log_dat.clear()
        for line in lines:
            if line:
                self._send({"t": "log", "line": line})

    # ─────────── helpers ───────────

    def Log(self, msg):
        """Append a line to the textport-mirror buffer; flushed on next Tick."""
        log_dat = op("log")
        if log_dat is None:
            debug(f"[DeskDeck:Log] (no log DAT) {msg}")
            return
        log_dat.appendRow([str(msg)])

    def RegisterMacro(self, name, fn):
        """Register a callable invokable from the tablet via
        action: {type: td_macro, name: "<name>"}."""
        self._macros[str(name)] = fn

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
            try:    value = par.eval()
            except Exception: value = None
            try:    raw_val = par.val
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
        print(f"  last rollover_op:  {self._last_rollover_op}")
        print(f"  last rollover_par: {self._last_rollover_par}")
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
        for k in ("selected", "rollover_op", "rollover_par", "perf", "pane_path"):
            self._subs.add(k)
        self._last_selected = None
        self._last_rollover_op = None
        self._last_rollover_par = None
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

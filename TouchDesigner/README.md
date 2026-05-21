# Desk_Deck — TouchDesigner connector

`Desk_Deck.tox` is a small component you drop into any TouchDesigner
project. It opens a WebSocket to your running Desk_Deck server and
makes TD's live state (selected op, parameter under the mouse, perf,
log) visible on the tablet — and lets the tablet drive TD parameters
back.

`DeskDeckConnector.py` is the extension class that ships with it.

## Build the .tox

Inside `Desk_Deck.tox`, add these operators (names matter — the
extension looks them up by name):

| Op             | Type            | Settings                                                                                 |
|----------------|-----------------|------------------------------------------------------------------------------------------|
| `connector`    | (the owning component) | Promote `DeskDeckConnector` as the extension. Promote Extension = On. |
| `ws`           | Web Socket DAT  | Active = Off (the extension flips it on via `Connect()`).                                |
| `ws_callbacks` | DAT Execute DAT | Callback functions: `onConnect`, `onDisconnect`, `onReceiveText`. See snippet below.    |
| `frame_tick`   | Execute DAT     | Callback: `onFrameEnd`. Calls `ext.DeskDeckConnector.Tick()` every N frames (~10 Hz).   |
| `log`          | Text DAT        | Empty. Treated as an append-only buffer; the extension flushes lines each tick.         |

### Custom parameters on the .tox parent

Add a custom parameter page (e.g. `Connect`) with:

| Name             | Type   | Default        | Notes                                                                              |
|------------------|--------|----------------|------------------------------------------------------------------------------------|
| `Netaddress`     | Str    | `127.0.0.1`    | Just the host (no port, no scheme). Or a full `ws://host[/path]` if you prefer.    |
| `Port`           | Int    | `8765`         | Server port. The Web Socket DAT splits host + port across two fields, so this is separate. |
| `Token`          | Str    | *(paste your paired token)* | The contents of `%APPDATA%\Desk_Deck\token`.                       |
| `Streamtextport` | Toggle | `Off`          | When on, every `print()` / `debug()` in TD is mirrored to the tablet's `td_log` textbox. After toggling, call `op('Desk_Deck').SyncPrintMirror()` (or hit Connect again). |

### `ws_callbacks` snippet

```python
def onConnect(dat):
    op('connector').ext.DeskDeckConnector.OnConnect()

def onDisconnect(dat):
    op('connector').ext.DeskDeckConnector.OnDisconnect()

def onReceiveText(dat, rowIndex, message):
    op('connector').ext.DeskDeckConnector.OnRx(message)
```

### `frame_tick` snippet

```python
TICK_EVERY_N_FRAMES = 6  # at 60 fps ≈ 10 Hz

def onFrameEnd(frame):
    if frame % TICK_EVERY_N_FRAMES == 0:
        op('connector').ext.DeskDeckConnector.Tick()
```

## Open the connection

From the textport (or a button parameter):

```python
op('Desk_Deck').Connect()
# … later
op('Desk_Deck').Disconnect()
```

Watch the server's stdout for `[td] hello · MyShow.toe · version 2023.11600`.

### How the Web Socket DAT params get wired

TD's Web Socket DAT splits the connection across two parameters:

- **Network Address (`netaddress`)** wants the *full URL* — scheme, host,
  path, query — but **without the port** (e.g.
  `ws://192.168.1.161/live?device=touchdesigner&t=<token>`).
- **Network Port (`port`)** is the port number separately (e.g. `8765`).

The `Connect()` method handles this — you only set the `Server` param on
the .tox parent (e.g. `ws://192.168.1.161:8765`) and the connector
splits it into the right two pars on the underlying Web Socket DAT.

If `Connect()` prints `WARNING — no port parameter` in the textport,
your TD build's Web Socket DAT param names differ — share a screenshot
and we'll adjust the connector.

## Streamed state

The server tells the connector which kinds to stream (so we only emit
what the tablet's actually subscribed to). The connector's `Tick()`
diffs each enabled kind and only sends when it changes:

| Kind          | Fired when                                      |
|---------------|-------------------------------------------------|
| `selected`    | `ui.panes[0].selected` changes                   |
| `rollover`    | `ui.rollover` identity or value changes — payload includes `kind_of` (par / pargroup / page / op / panel / none) and a typed sub-payload |
| `pane_path`   | `ui.panes[0].owner.path` changes                 |
| `perf`        | `app.cookRate` / `project.cookTime` / GPU mem change |
| `status`      | `ui.status` text changes                         |

## Debug helpers

Three textport-callable methods if something isn't flowing:

```python
op('Desk_Deck').Status()
# Dumps: ws status, current server subscriptions, rx/tx/emit/tick
# counters, and the last cached value for each state kind.

op('Desk_Deck').DumpWsParams()
# Lists every parameter on the ws DAT with its current value —
# useful when troubleshooting netaddress/port/url field names.

op('Desk_Deck').ForceSubscribeAll()
# Pretends the server told us to stream everything (selected,
# rollover, perf, pane_path, status). Use to test that Tick() actually
# fires before debugging the server-side subscribe path. After this,
# Tick() should start emitting on the next call.
```

The connector also auto-logs:

- Every `OnRx` cmd (with shortened arg summary)
- Every subscribe/unsubscribe change
- Every state emit (one line per change)
- A heartbeat every ~60 ticks (`tick #N  subs=...  rx=N tx=N emit=N`)

If `Status()` shows `subs=(none)` after the tablet has TouchDesigner
focused → the server isn't sending `subscribe` cmds (either no
provider is bound to a textbox, or the WS broke).

If `subs={...}` is populated but counters never grow → `frame_tick`
isn't actually calling `Tick()`. Sanity check by typing
`op('Desk_Deck').Tick()` in the textport — should bump tick counter
and emit a state line.

## Registering macros

In any TD Python script (run once, e.g. in `Execute DAT.onStart`):

```python
def reset_camera():
    op('/proj/cam1').par.tx = 0
    op('/proj/cam1').par.ty = 0
    op('/proj/cam1').par.tz = 5

op('Desk_Deck').RegisterMacro('reset_camera', reset_camera)
```

Then bind a tablet button:

```yaml
- id: cam_reset
  type: button
  ...
  props:
    label: RESET CAM
    action: { type: td_macro, name: "reset_camera" }
```

## Logging to the tablet textbox

From any script:

```python
op('Desk_Deck').Log(f"cooked {op('noise1').name} in {op('noise1').cookTime:.1f} ms")
```

The line shows up in the tablet's `td_log` textbox on the next tick.

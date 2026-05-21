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

Add a custom parameter page named `Desk Deck`:

| Name     | Type   | Default                          | Notes                                                                 |
|----------|--------|----------------------------------|-----------------------------------------------------------------------|
| `Server` | string | `ws://192.168.1.161:8765`        | Address of the Desk_Deck server. `http(s)://` is normalized to `ws(s)://`. |
| `Token`  | string | *(paste your paired token)*      | The contents of `%APPDATA%\Desk_Deck\token`.                          |

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
| `rollover_op` | `ui.rolloverOp` changes                          |
| `rollover_par`| `ui.rolloverPar`'s `(owner.path, name, value)` changes |
| `pane_path`   | `ui.panes[0].owner.path` changes                 |
| `perf`        | `app.cookRate` / `project.cookTime` / GPU mem change |

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

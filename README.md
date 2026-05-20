# Desk_Deck

> A context-aware touch controller for your PC, built from an unused tablet
> and any modern browser.

Desk_Deck turns a tablet on your desk into a programmable touch surface. A
small Python server on your PC watches which application is in the
foreground, pushes a matching **layout** to the tablet's browser over
WebSocket, and dispatches actions when you tap a widget (keystrokes, shell
commands, custom Python, window focus, virtual desktop switches).

Layouts are designed in a browser-based **visual editor** on the PC or
hand-written as YAML in `configs/`. Widgets are placed freely on a per-context
canvas: buttons, sliders, rotary dials, read-only textboxes that the server
streams text into, plain labels, and editable text inputs.

A title bar across the top of the tablet shows the current context and
exposes three slide-out overlays:

- **Apps** — every visible window on your PC; tap to focus.
- **Spaces** — your Windows virtual desktops; tap to switch.
- **Bookmarks** — a global panel of macros that's always one tap away.

No app install. No App Store. The tablet just opens a URL.

---

## Why

Stream Deck-style hardware locks you into a fixed grid, a vendor app, and a
small static screen. Most people have a tablet that already has a big touch
screen, a fast CPU, and a real browser. Desk_Deck uses what's already on
your desk and makes the layout dynamic — you get a different control surface
for TouchDesigner, Premiere, your terminal, or whatever you're focused on
right now.

The textbox widget is bidirectional: any script on your PC can
`POST /widget/<id>` and the text shows up on the tablet. That makes the
tablet a glanceable console for build output, chat, log tails, or anything
you'd rather not Alt+Tab to.

---

## Architecture

```
+------------------------------+                +------------------------------+
| Pixel tablet -- runtime view |                | PC -- Python server          |
|   /  (full-screen widgets)   |  WebSocket     |   FastAPI + uvicorn          |
|   . title bar (context +     | <------------> |                              |
|     Apps / Spaces / Marks)   |                |   Foreground watcher (4 Hz)  |
|   . renders pushed layout    |                |           |                  |
|   . sends press/value/input  |                |           v                  |
+------------------------------+                |   Config loader (YAML)       |
                                                |   . hot-reload via watchdog  |
+------------------------------+                |           |                  |
| PC browser -- visual editor  |  WebSocket     |   Provider registry          |
|   /editor                    | <------------> |   . dynamic widget sources   |
|   . palette -> drag widgets  |  HTTP CRUD     |   . textbox push streams     |
|   . free placement + snap    |                |           |                  |
|   . property inspector       |                |   Action dispatcher          |
|   . match-rule editor        |                |   . hotkey, command, launch  |
|   . theme picker             |                |   . focus_window, python     |
|   . save -> writes YAML      |                |   . switch_desktop           |
|   . preview-on-tablet        |                |                              |
+------------------------------+                |   External hook:             |
                                                |   POST /widget/:id  ---------+--> any script
                                                |   (push text into textbox)   |    can write
                                                +------------------------------+    to console
```

Two clients, one server. The tablet renders runtime layouts; the editor
edits them. Both share the same widget renderer (`web/shared/widgets.js`),
so the editor canvas is a true WYSIWYG of what the tablet will display.

---

## Quick start

Requirements: Windows 10/11, Python 3.10+, a tablet on the same Wi-Fi.

```
git clone https://github.com/harveymoon/Desk_Deck.git
cd Desk_Deck
run.bat
```

`run.bat` creates a `.venv`, installs dependencies, and launches the server.
On startup the terminal prints a QR code and three URLs:

```
  Tablet:  http://<lan-ip>:8765/?t=<token>
  Pair:    http://<lan-ip>:8765/pair
  Editor:  http://127.0.0.1:8765/editor
```

Open `/pair` in any browser on the PC, scan the QR from the tablet, and the
tablet stores the token in `localStorage`. After that, the tablet URL works
without the `?t=` query string.

**Add to home screen** (Chrome → Install app) for a fullscreen, no-Chrome-chrome
experience with the Desk_Deck app icon.

Manual install (if you'd rather skip `run.bat`):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m server --port 8765
```

To start at login:

```
install_autostart.bat        REM creates a Task Scheduler entry
uninstall_autostart.bat      REM removes it
```

---

## What you get out of the box

### Context-aware layouts

The server watches `GetForegroundWindow()` at ~4 Hz. When the foreground
changes, it finds the first `configs/*.yaml` whose `match:` rule matches
(process name / window title regex / window class). The matching layout is
pushed to every connected tablet over WebSocket.

Shipped configs: **TouchDesigner**, **Notepad**, **Chrome**, **VS Code**.

### Dynamic fallback for unconfigured apps

When no YAML matches the foreground process, the server **auto-generates**
a layout listing every visible top-level window of that process. Each
button is wired to `focus_window` with the HWND baked in, so tapping
brings that window to the front.

A 1 Hz poller refreshes the synthetic layout when the active app opens or
closes a window, so the list stays current without alt-tab.

### Bidirectional textboxes

A textbox widget can stream text in from two sources:

- A Python provider in `server/providers/` (e.g. `tail_log.py` tails a file).
- Any HTTP client: `curl -X POST http://<ip>:8765/widget/<id>?t=<token> -d '{"text":"hello\n"}'`.

Lets your build scripts, watcher daemons, and chat bots push to the tablet
without any client-side code.

### Visual editor at `/editor`

A three-pane editor — palette, free-placement canvas with snap-to-grid,
inspector — that reads and writes the same YAML files. Save commits the
config to disk; the next time that app comes to focus, the layout is live.
A **Preview on tablet** button broadcasts the in-progress draft to any
connected tablet so you can iterate visually.

### Title bar overlays

The top of the tablet shows the current context and three menu buttons:

| Button | Overlay | Backed by                                              |
|--------|---------|---------------------------------------------------------|
| **▦**  | Apps    | `/api/windows` — every visible top-level window, each rendered with the app's real Windows icon. Also lists **Chrome tabs** if Chrome is running with DevTools Protocol on port 9222 (use `start_chrome_debug.bat`). Apps you've hidden (see below) are filtered out. |
| **▢▢** | Spaces  | `/api/desktops` — Windows virtual desktops via [pyvda]  |
| **★**  | Bookmarks | `configs/bookmarks.yaml` — pinned cross-context macros |

### Hiding invisible / noise apps

Some Windows processes have visible top-level windows that aren't really
useful as switch targets — NVIDIA overlays, search hosts, the Program
Manager desktop. The editor has a **Hidden apps** button in the top bar
that opens a modal listing every running process with its icon; tick the
ones you want to suppress and save.

The selection is persisted to `configs/_filters.yaml`:

```yaml
hide_processes:
  - NVIDIA Overlay.exe
  - TextInputHost.exe
hide_classes:
  - Progman          # the desktop
  - Shell_TrayWnd    # the taskbar
hide_title_regex:
  - ^Program Manager$
```

A sensible default set ships with the project — you can edit the YAML
directly to add window-class or title-regex filters (the modal only
covers process names).

[pyvda]: https://github.com/mrob95/pyvda

### Chrome tab listing

To list Chrome tabs in the Apps overlay (not just Chrome windows), Chrome
needs to be launched with `--remote-debugging-port=9222`. Two options:

1. **Quick**: close all Chrome windows, then run `start_chrome_debug.bat`.
   Reopens Chrome with the debug port enabled and your existing profile.
2. **Permanent**: edit your Chrome shortcut and add
   `--remote-debugging-port=9222` to the Target.

When the port is open, the Apps overlay shows a "Chrome tabs" section
with every open tab; tapping switches to that tab and brings its Chrome
window to the front.

---

## CLI

```
python -m server [options]

  --port 8765       Port to bind (default 8765).
  --host 0.0.0.0    Bind host (default 0.0.0.0 -- LAN-accessible).
  --reset-token     Rotate the pairing token. Invalidates all paired devices.
  --reload          Uvicorn auto-reload for development.
```

The token is stored in `%APPDATA%\Desk_Deck\token` and persists across runs.

---

## Configs

A config is a YAML file in `configs/` describing one context: a match rule,
a canvas size, and a list of widgets.

### Minimal example

```yaml
# configs/touchdesigner.yaml
name: TouchDesigner
match:
  process: TouchDesigner.exe
canvas:
  width: 1600
  height: 1000
  theme: midnight
widgets:
  - id: save
    type: button
    x: 40
    y: 64
    w: 240
    h: 140
    props:
      label: SAVE
      action: { type: hotkey, keys: ctrl+s }
```

### Match rules

Any combination of the three keys; at least one is required.

| Key                  | Value                                                    |
|----------------------|----------------------------------------------------------|
| `process`            | Exact match against the foreground process (case-insensitive). |
| `window_title_regex` | Python regex matched against the foreground window title. |
| `window_class`       | Exact match against the Win32 window class.              |

### Canvas

```yaml
canvas:
  width: 1600
  height: 1000
  theme: midnight
```

The tablet runtime scales the canvas to fit its viewport while preserving
the aspect ratio.

---

## Widgets

Every widget shares the same envelope; type-specific fields go under
`props`.

```yaml
- id: save_btn
  type: button
  x: 40
  y: 40
  w: 220
  h: 120
  props:
    label: SAVE
    action: { type: hotkey, keys: ctrl+s }
```

### button

| Prop     | Type   | Notes                                           |
|----------|--------|-------------------------------------------------|
| `label`  | string | Text shown on the button.                       |
| `icon`   | string | Emoji or glyph shown before the label.          |
| `action` | object | See [Actions](#actions).                        |
| `mode`   | string | `momentary` (default) or `toggle`.              |

### slider

| Prop          | Type   | Notes                                          |
|---------------|--------|------------------------------------------------|
| `label`       | string | Label.                                         |
| `min` / `max` | number | Value range.                                   |
| `step`        | number | Snap increment.                                |
| `orientation` | string | `horizontal` or `vertical`.                    |
| `action`      | object | Called with `value` on change (debounced 30 ms).|

### rotary

A circular dial. Same props as slider but rendered as an SVG ring with a
needle and an arc fill.

### textbox

Read-only. Server pushes lines via WS `widget_update`.

| Prop        | Type    | Notes                                                |
|-------------|---------|------------------------------------------------------|
| `source`    | string  | Provider name in `server/providers/` (optional).      |
| `monospace` | boolean | Render in the mono font.                              |
| `max_lines` | number  | Trim from the top when exceeded. Default 500.         |

Two ways to push text into it:

1. **Pull**: YAML names a provider whose `subscribe(emit)` streams text in.
2. **Push**: `POST /widget/<id>` with `{"text": "..."}`.

### input

Editable text. User types on the tablet, value sent to the PC on submit.

| Prop            | Type    | Notes                                       |
|-----------------|---------|---------------------------------------------|
| `placeholder`   | string  | Hint shown when empty.                      |
| `submit_label`  | string  | Button label (default `SEND`).              |
| `clear_on_submit` | bool  | Wipe input after submit (default true).     |
| `action`        | object  | Called with `text` payload on submit.       |

### label

| Prop    | Type   | Notes                                |
|---------|--------|--------------------------------------|
| `text`  | string | Static text.                         |
| `align` | string | `left`, `center`, or `right`.        |
| `size`  | number | Font size in px.                     |

---

## Actions

Buttons, sliders, rotaries, and inputs fire actions when the user
interacts. The dispatcher routes by `action.type`.

### `hotkey`

```yaml
action: { type: hotkey, keys: ctrl+shift+s }
```

Simulates a keystroke via the `keyboard` library. Supports modifier chains
(`ctrl+alt+f4`), function keys (`f11`), media keys.

### `command`

```yaml
action: { type: command, cmd: ["python", "C:/scripts/build.py"] }
```

Runs a shell command via `subprocess.Popen`. List → no shell; string →
`shell=True`.

### `launch`

```yaml
action: { type: launch, target: "https://example.com" }
action: { type: launch, target: "C:/Users/me/Documents/notes.md" }
```

Opens a file, URL, or app via the OS shell.

### `focus_window`

```yaml
action: { type: focus_window, hwnd: 12345 }
```

Brings a window to the front by HWND. Used by the dynamic fallback layout
(HWND baked into each generated button) and the Apps overlay (HWND passed
in the press payload). The dispatcher uses `AttachThreadInput` so it works
even though the server isn't the foreground process.

### `switch_desktop`

```yaml
action: { type: switch_desktop, index: 2 }
```

Switches to the 1-indexed Windows virtual desktop via [pyvda]. Falls back
to `Win+Ctrl+Right` cycling if pyvda is unavailable.

### `python`

```yaml
action: { type: python, provider: set_bpm }
```

Calls the provider's `on_value(value, widget, context)` (sliders/rotaries
pass `value`; buttons pass `"press"`). Use this to wire custom logic
without writing a new action type.

---

## Themes

Themes are CSS-variable bundles in `themes/*.yaml`. The currently-applied
theme is pushed alongside every layout, and themes hot-reload when you
edit a file.

```yaml
# themes/midnight.yaml
name: midnight
vars:
  --bg:        "#0a0a0c"
  --surface:   "#141418"
  --surface-2: "#1c1c22"
  --border:    "#2a2a32"
  --text:      "#e6e6ea"
  --text-dim:  "#7a7a85"
  --accent:    "#5cf"
  --danger:    "#f55"
  --font-ui:   "Inter, system-ui, sans-serif"
  --font-mono: "JetBrains Mono, ui-monospace, monospace"
  --radius:    "0px"
  --border-w:  "1px"
```

Shipped presets:

- **midnight** — charcoal + cyan accent. Default.
- **graphite** — neutral grey with a white accent.
- **terminal** — black + green phosphor; everything mono.

Aesthetic defaults are deliberate: sharp 1px borders, no shadows, square
corners (`--radius: 0px`), monospace for textboxes and labels,
sans-serif for buttons, single accent used sparingly.

---

## Providers

Drop a `.py` file in `server/providers/` to extend Desk_Deck. Three
optional flavors per file — implement only what you need.

```python
# server/providers/tail_log.py — textbox source
import threading, time

def subscribe(emit):
    """Called when a textbox uses source: tail_log.
    Call emit(text) to append. Return a cleanup function."""
    f = open("C:/path/log.txt"); f.seek(0, 2)
    stop = threading.Event()
    def loop():
        while not stop.is_set():
            line = f.readline()
            if line: emit(line)
            else: time.sleep(0.1)
    threading.Thread(target=loop, daemon=True).start()
    return lambda: (stop.set(), f.close())
```

```python
# server/providers/set_bpm.py — slider/button action target
def on_value(value, widget=None, context=None):
    """Called when a slider changes or a python button fires."""
    print(f"bpm={value}")
```

Providers are auto-discovered at startup.

The repo ships with `tail_log` (textbox demo) and `set_volume` (slider
demo that just logs the value — wire to your favorite volume API).

---

## HTTP & WebSocket API

All endpoints except `/pair` and `/api/token` require a token in the
`Authorization: Bearer` header or `?t=` query string. The WebSocket uses
the query-string form.

| Method | Path                       | Auth      | Purpose                              |
|--------|----------------------------|-----------|--------------------------------------|
| GET    | `/`                        | --        | Tablet runtime                       |
| GET    | `/editor`                  | --        | Layout editor                        |
| GET    | `/pair`                    | --        | QR code page                         |
| GET    | `/api/token`               | localhost | Convenience for editor auto-fill     |
| GET    | `/api/configs`             | token     | List configs                         |
| GET    | `/api/configs/{name}`      | token     | Fetch one                            |
| PUT    | `/api/configs/{name}`      | token     | Save (editor uses this)              |
| DELETE | `/api/configs/{name}`      | token     | Remove                               |
| GET    | `/api/themes`              | token     | List themes                          |
| GET    | `/api/themes/{name}`       | token     | Fetch theme                          |
| GET    | `/api/context`             | token     | Current foreground + active config   |
| GET    | `/api/windows`             | token     | Visible top-level windows (filtered, includes icons). `?include_hidden=true` to see filtered entries too. |
| GET    | `/api/processes`           | token     | Distinct processes with icons (used by the Hidden Apps modal) |
| GET    | `/api/filters`             | token     | App filter config                    |
| PUT    | `/api/filters`             | token     | Save app filter config               |
| GET    | `/api/desktops`            | token     | Virtual desktops list                |
| POST   | `/api/desktops/{i}`        | token     | Switch to desktop i                  |
| POST   | `/api/focus/{hwnd}`        | token     | Focus a window by HWND               |
| GET    | `/api/bookmarks`           | token     | Bookmarks config                     |
| POST   | `/api/bookmarks/run`       | token     | Dispatch a bookmark action           |
| POST   | `/api/action`              | token     | Dispatch any action                  |
| GET    | `/api/chrome/tabs`         | token     | Chrome tabs via DevTools (CDP)       |
| POST   | `/api/chrome/activate/{id}`| token     | Switch to a Chrome tab               |
| POST   | `/widget/{id}`             | token     | Push text into a textbox             |
| WS     | `/live?t=<token>`          | token     | Runtime + editor channel             |

### WebSocket protocol

**Client → server**

```json
{ "t": "hello",          "device": "tablet" }
{ "t": "press",          "id": "save_btn" }
{ "t": "release",        "id": "save_btn" }
{ "t": "value",          "id": "bpm", "value": 128 }
{ "t": "input",          "id": "search", "text": "..." }
{ "t": "focus_hwnd",     "hwnd": 12345 }
{ "t": "switch_desktop", "index": 2 }
{ "t": "preview",        "layout": { ... } }
```

**Server → client**

```json
{ "t": "layout",        "layout": { ..., "_context": {...} }, "theme": { ... } }
{ "t": "widget_update", "id": "console", "patch": { "append": "new line\n" } }
{ "t": "desktops",      "desktops": [{ "index": 1, "name": "Desktop 1", "current": true }] }
{ "t": "error",         "msg": "..." }
```

---

## Security

Desk_Deck is a LAN tool, not an internet service. Threat model: "no one
else on my Wi-Fi should be able to type into my PC."

- Random 256-bit token generated on first run, stored in
  `%APPDATA%\Desk_Deck\token`.
- All HTTP and WebSocket endpoints (except `/pair` and the localhost-only
  `/api/token`) require the token.
- Pairing is one-tap: scan QR → tablet stores token in `localStorage`.
- `--reset-token` rotates the token and forces re-pairing.
- No TLS. If you need it, put Caddy or nginx in front.

---

## Project layout

```
Desk_Deck/
  server/
    __main__.py        # entrypoint: token, QR, uvicorn
    app.py             # FastAPI routes + WebSocket hub
    auth.py            # token + middleware
    config.py          # YAML load/save + watchdog hot-reload
    actions.py         # hotkey, command, launch, focus_window, switch_desktop, python
    bus.py             # in-process pub/sub
    watcher.py         # foreground window watcher
    dynamic.py         # window enumeration + synthetic fallback
    themes.py          # theme loader + hot-reload
    desktops.py        # virtual desktop helpers (pyvda)
    chrome.py          # Chrome DevTools Protocol client (tabs)
    filters.py         # app filter (hide list) load/save/check
    icons.py           # Windows .exe icon extraction → PNG data URLs
    registry.py        # provider discovery
    providers/
      __init__.py
      tail_log.py      # textbox demo
      set_volume.py    # slider demo
  web/
    shared/
      widgets.js       # render(widget) -> DOM, shared by runtime + editor
      ws.js            # reconnecting WebSocket client
      theme.js         # apply CSS vars to a root
    runtime/
      index.html       # tablet page
      runtime.js
      runtime.css
      manifest.webmanifest
      sw.js            # service worker (app-shell cache)
      icon.svg         # PWA app icon
      icon-192.png
      icon-512.png
      favicon.ico
    editor/
      index.html       # layout editor
      editor.js
      editor.css
  configs/
    default.yaml
    bookmarks.yaml
    touchdesigner.yaml
    notepad.yaml
    chrome.yaml
    vscode.yaml
    _filters.yaml      # app hide list (managed via editor modal)
  themes/
    midnight.yaml
    graphite.yaml
    terminal.yaml
  requirements.txt
  run.bat
  install_autostart.bat
  uninstall_autostart.bat
  start_chrome_debug.bat
  README.md
```

---

## Status

All phases of the original plan are implemented and end-to-end tested:

| Phase | Scope                                                              | Status |
|-------|--------------------------------------------------------------------|--------|
| 1     | Runtime + button/label widgets + hotkey dispatch + pairing         | done   |
| 2     | Foreground watcher + YAML hot-reload + multi-config                | done   |
| 2.5   | Dynamic fallback (window list for processes without a YAML)        | done   |
| 3     | Slider + textbox widgets + bus + `POST /widget/:id`                | done   |
| 4     | Theme YAML files + hot-reload + presets                            | done   |
| 5     | Visual layout editor at `/editor`                                  | done   |
| 6     | PWA install + autostart helper + rotary + editable textbox + title bar overlays (Apps, Spaces, Bookmarks) | done |

---

## License

MIT.

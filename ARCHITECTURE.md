# Desk_Deck architecture

## Core vs plugins

```
┌──────────────────────────── CORE ────────────────────────────┐
│  server/app.py        FastAPI app, Hub, WebSocket router      │
│  server/actions.py    Generic actions + register_action()     │
│  server/registry.py   Plugin discovery, register(api) hook    │
│  server/devices.py    WebSocket device-handler registry       │
│  server/config.py     Per-app YAML loader + hot-reload        │
│  server/themes.py     CSS variable theme loader               │
│  server/sidebar.py    Right-side button rail                  │
│  server/auth.py       Token gate                              │
│  web/shared/widgets.js     Generic renderers + registries     │
│  web/runtime/runtime.js    Tablet runtime                     │
└───────────────────────────────────────────────────────────────┘
              ▲                              ▲
              │ register(api)                │ registerRenderer()
              │                              │ registerUpdater()
┌──────────────────────────── PLUGINS ─────────────────────────┐
│  server/providers/<name>.py        actions, device, hooks    │
│  server/providers/<name>_*.py      textbox sources           │
│  web/shared/widgets-<name>.js      custom renderers          │
│  configs/<context>.yaml            layout that references    │
│                                    plugin-provided types     │
└───────────────────────────────────────────────────────────────┘
```

## Plugin contract — server side

A provider module in `server/providers/<name>.py` MAY expose any of:

```python
# Called once at server startup. Optional. Use to wire actions,
# device handlers, and startup hooks. See registry._PluginAPI.
def register(api):
    api.register_action("td_set_par", _td_set_par)
    api.register_device("touchdesigner",
                        on_open=_on_open,
                        on_message=_on_message,
                        on_close=_on_close)
    api.register_startup_hook(_on_startup)

# Called when a textbox widget binds its `source:` field to this
# provider name. emit(text, replace=True|False). Returns a cleanup
# callable. Unchanged from the original provider contract.
def subscribe(emit):
    ...
    return cleanup

# Sliders / buttons can name a provider for value/press hooks
# (alternative to using register_action). Unchanged.
def on_value(value, widget, context): ...
def on_press(widget, context): ...
```

### The `api` object

Handed to `register(api)`. Three methods:

| Method                                      | Effect                                |
|---------------------------------------------|---------------------------------------|
| `api.register_action(type_name, fn)`        | `fn(action, payload, context, widget)` becomes the handler for `action.type == type_name`. |
| `api.register_device(name, on_open=, on_message=, on_close=)` | Routes `/live?device=name` connections through your handlers. Each can be sync or async. |
| `api.register_startup_hook(fn)`             | `fn(loop)` is called once after `registry.reload()`. Sync or async. Use for `td.subscribe(...)`-style wiring that needs the loop. |

Last registration wins for duplicate names (matches the original
static-dict semantics).

## Plugin contract — client side

A plugin bundle is `web/shared/widgets-<name>.js`. It imports the
registration API and registers types at module-import time:

```javascript
import { registerRenderer, registerUpdater } from "./widgets.js";

function renderMyWidget(widget, emit) {
  const el = document.createElement("div");
  // ... build DOM ...
  // Optional: el._patch = (patch) => { ... }  for in-place value sync
  return el;
}

registerRenderer("my_widget", renderMyWidget);
registerUpdater("my_widget", (el, widget, patch) => {
  if (typeof el._patch === "function") el._patch(patch);
  if ("hidden" in patch) el.style.display = patch.hidden ? "none" : "";
});
```

To load the bundle, add one static import to `web/runtime/runtime.js`:

```javascript
import "/shared/widgets-mything.js";
```

(Future: layout-driven dynamic loading via a `plugins:` field. Not v1.)

## Reference plugin: TouchDesigner

`server/providers/td_integration.py` is the canonical example. It
registers:

- 5 actions: `td_set_par`, `td_nudge_par`, `td_macro`, `td_toggle_par`, `td_open_help`.
- 1 device: `touchdesigner` (forwards JSON ↔ `server/td.py`).
- 1 startup hook: subscribes to TD state channels and pushes
  widget updates to the param panel + rollover slider/toggle.

`web/shared/widgets-td.js` registers 2 renderers: `param_panel` and
`value_ladder`, plus the HSV color-picker popup helper.

Reading these two files end-to-end is the fastest way to learn the
plugin shape for the next integration.

## Layouts reference plugin types by string

A YAML in `configs/<context>.yaml` doesn't know anything about plugins:

```yaml
widgets:
  - type: param_panel        # ← provided by widgets-td.js plugin
    id: td_pars
    x: 560; y: 184; w: 996; h: 792
    props:
      action: { type: td_set_par }   # ← provided by td_integration.py
```

A missing plugin produces an `[unknown widget: ...]` log line, not a
crash. Layouts and plugins evolve independently.

## What's NOT pluggable yet (deferred)

- **Fail-fast collision guards** on duplicate names. Today: last wins.
- **Plugin hot-reload.** Touching a provider file requires a server
  restart.
- **Dynamic client bundle loading.** All plugin bundles are statically
  imported by `runtime.js`. A layout declaring `plugins: [td]` could
  drive dynamic imports — useful when the bundle list grows.

These are all explicit non-goals for v1. They become worth adding when
there's a second real plugin to justify the surface area.

## See also

- `CLAUDE.md` — short rule doc for future Claude sessions.
- `README.md` — user-facing project overview.

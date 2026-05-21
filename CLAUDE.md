# Desk_Deck — repo rules

## Plugin architecture (load-bearing)

**All integration-specific code lives in plugins. Never edit core files
to add or extend an integration.**

The core is small and generic. Integrations (TouchDesigner, Resolume,
Ableton, Blender, anything else) are plugins that drop into:

- `server/providers/<name>.py` — exposes a `register(api)` entry point;
  registers actions, device handlers, startup hooks via the passed API.
  Optionally exposes `subscribe(emit)` for textbox sources too.
- `web/shared/widgets-<name>.js` — client-side bundle. Imports
  `{ registerRenderer, registerUpdater }` from `./widgets.js` and
  registers any custom widget renderers at module import time.

**Off-limits for integration-specific edits:**

- `server/app.py` — the FastAPI app + Hub + WebSocket router.
  The WS handler dispatches device traffic via `server/devices.py`.
  Never add `if device == "<plugin>"` branches.
- `server/actions.py` — generic actions only (hotkey, command, launch,
  focus_window, switch_desktop, python, chrome_tab). Plugins call
  `api.register_action(type, fn)` from their `register()` hook.
- `server/registry.py` — provider discovery + plugin API surface.
  Add new API methods only when truly generic.
- `web/shared/widgets.js` — generic widget renderers only (button,
  label, slider, textbox, input, rotary, window_list). Plugins call
  `registerRenderer(type, fn)` and `registerUpdater(type, fn)` from
  their own bundle.

**Why this rule exists:** the original Desk_Deck design intent was
plugin-based, but the TouchDesigner integration grew until 5 of 13
action handlers, 3 startup hooks, and 2 of 10 widget renderers were
TD-specific code living in core files. Adding the next integration
(Resolume / Ableton / etc.) would have required forking each of
those files. The current shape is what restores the original intent.

For the full plugin contract (registration API surface, examples,
verification), see **ARCHITECTURE.md**.

## Other repo notes

- Square corners, dark, minimal palette. No playful Material rounding.
- Square widgets, not rounded. Technical look.
- The user works primarily with TouchDesigner; TD is the canonical
  example plugin.

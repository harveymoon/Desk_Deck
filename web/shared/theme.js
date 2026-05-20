// Default dark theme (Phase 4 will load from themes/*.yaml via the server).
export const DEFAULT_THEME = {
  name: "midnight",
  vars: {
    "--bg":        "#0a0a0c",
    "--surface":   "#141418",
    "--surface-2": "#1c1c22",
    "--surface-3": "#24242c",
    "--border":    "#2a2a32",
    "--border-hi": "#3a3a45",
    "--text":      "#e6e6ea",
    "--text-dim":  "#7a7a85",
    "--accent":    "#5cf",
    "--danger":    "#f55",
    "--font-ui":   "Inter, system-ui, sans-serif",
    "--font-mono": "JetBrains Mono, ui-monospace, monospace",
    "--radius":    "0px",
    "--border-w":  "1px",
    "--gap":       "8px",
  },
};

export function applyTheme(root, theme) {
  const vars = (theme && theme.vars) || DEFAULT_THEME.vars;
  for (const [k, v] of Object.entries(vars)) {
    root.style.setProperty(k, v);
  }
}

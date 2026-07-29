---
description: Enable the KimiWtf quota status line (writes [status_line] command to tui.toml)
---

Enable the KimiWtf status line by running its installer exactly once:

```bash
python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-wtf/scripts/tui_config.py" install
```

The installer idempotently sets `[status_line] command` in `tui.toml` to the plugin's managed copy of `statusline.py` and keeps a `tui.toml.bak` backup. Show the command output to the user verbatim.

IMPORTANT: the status line will NOT appear until the user runs `/reload-tui` (or restarts the CLI) — a running session never re-reads `tui.toml` on its own. End your reply with a prominent, explicit instruction: «Теперь выполните `/reload-tui`, иначе строка не появится». Do not edit `tui.toml` by hand — the installer handles escaping and preserves the rest of the file.

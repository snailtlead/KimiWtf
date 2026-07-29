---
description: Disable the KimiWtf quota status line (restores the default footer)
---

Disable the KimiWtf status line by running:

```bash
python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-wtf/scripts/tui_config.py" uninstall
```

This removes the `[status_line] command` key from `tui.toml` (the rest of the file is untouched), which makes the CLI fall back to its built-in footer layout. Show the command output to the user verbatim, then remind them to run `/reload-tui` (or restart the CLI).

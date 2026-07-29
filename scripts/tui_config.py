#!/usr/bin/env python3
"""Установка/снятие кастомной строки состояния KimiWtf в tui.toml.

Идемпотентно правит ключ `command` в секции `[status_line]` файла
`$KIMI_CODE_HOME/tui.toml`, сохраняя остальное содержимое как есть.
Перед первой правкой создаёт резервную копию `tui.toml.bak`.

Использование:
    tui_config.py install [<корень плагина>]
    tui_config.py uninstall

Корень плагина по умолчанию — родительская папка этого скрипта: при запуске
из managed-копии плагина путь в `command` указывает именно на неё и потому
переживает обновления плагина.
"""

import os
import shlex
import shutil
import sys

SECTION = "status_line"
KEY = "command"
STATUSLINE_FILE = "statusline.py"

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None


class TuiConfigError(Exception):
    """Понятная пользователю ошибка правки tui.toml."""


# ---------- пути ----------

def kimi_home():
    return os.environ.get("KIMI_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".kimi-code")


def tui_toml_path():
    return os.path.join(kimi_home(), "tui.toml")


def default_plugin_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- мини-патчер TOML (правим только нужное, остальное не трогаем) ----------

def _section_header(line):
    """Имя секции для строки вида `[name]`, иначе None. Комментарии игнорируются."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
        return stripped[1:-1].strip().strip('"').strip("'")
    return None


def _find_section(lines, name):
    """Диапазон [start, end) строк секции `name`; None, если секции нет."""
    start = None
    for i, line in enumerate(lines):
        header = _section_header(line)
        if header is None:
            continue
        if start is not None:
            return start, i
        if header == name:
            start = i
    if start is not None:
        return start, len(lines)
    return None


def _is_key_line(line, key):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return False
    if "=" not in stripped:
        return False
    return stripped.split("=", 1)[0].strip().strip('"').strip("'") == key


def _toml_basic_string(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_status_line_command(text, command):
    """Возвращает текст с установленным [status_line] command."""
    lines = text.splitlines()
    key_line = f"{KEY} = {_toml_basic_string(command)}"
    span = _find_section(lines, SECTION)
    if span is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{SECTION}]")
        lines.append(key_line)
    else:
        start, end = span
        for i in range(start + 1, end):
            if _is_key_line(lines[i], KEY):
                lines[i] = key_line
                break
        else:
            lines.insert(start + 1, key_line)
    return "\n".join(lines) + "\n"


def remove_status_line_command(text):
    """Возвращает текст без ключа command в [status_line].

    Опустевшую секцию (только заголовок и пустые строки) удаляет целиком.
    """
    lines = text.splitlines()
    span = _find_section(lines, SECTION)
    if span is None:
        return text
    start, end = span
    body = [line for line in lines[start + 1:end] if not _is_key_line(line, KEY)]
    if all(not line.strip() for line in body):
        new_lines = lines[:start] + lines[end:]
        # Убираем пустую строку, оставшуюся на стыке в конце файла.
        while new_lines and not new_lines[-1].strip():
            new_lines.pop()
        if not new_lines:
            return ""
        return "\n".join(new_lines) + "\n"
    new_lines = lines[:start + 1] + body + lines[end:]
    return "\n".join(new_lines) + "\n"


# ---------- операции ----------

def validate_toml(text):
    if tomllib is None:
        return
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TuiConfigError(f"tui.toml не является валидным TOML, правка отменена: {exc}") from exc


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise TuiConfigError(f"не удалось прочитать {path}: {exc}") from exc


def _write(path, text):
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        raise TuiConfigError(f"не удалось записать {path}: {exc}") from exc


def _apply(transform):
    path = tui_toml_path()
    original = _read(path)
    validate_toml(original)
    updated = transform(original)
    if updated == original:
        return path, False
    if original:
        shutil.copyfile(path, path + ".bak")
    validate_toml(updated)
    _write(path, updated)
    return path, True


def install(plugin_root):
    script = os.path.join(plugin_root, STATUSLINE_FILE)
    if not os.path.isfile(script):
        raise TuiConfigError(f"не найден {STATUSLINE_FILE} рядом с плагином: {script}")
    # Раннер TUI выполняет команду через `sh -c`: вызов через python3 не зависит
    # от executable-бита, который теряется при установке плагина из zip.
    command = f"python3 {shlex.quote(script)}"
    path, changed = _apply(lambda text: set_status_line_command(text, command))
    state = "обновлён" if changed else "уже был настроен"
    print(f"[kimi-wtf] status line {state}: {path}")
    print(f"[kimi-wtf] command = {command}")
    print("[kimi-wtf] выполните /reload-tui в запущенной сессии (или перезапустите CLI)")


def uninstall():
    path, changed = _apply(remove_status_line_command)
    state = "ключ command удалён" if changed else "и так не был настроен"
    print(f"[kimi-wtf] status line: {state} ({path})")
    print("[kimi-wtf] выполните /reload-tui в запущенной сессии (или перезапустите CLI)")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    action, rest = argv[0], argv[1:]
    try:
        if action == "install":
            plugin_root = os.path.abspath(rest[0]) if rest else default_plugin_root()
            install(plugin_root)
        elif action == "uninstall":
            uninstall()
        else:
            print(f"неизвестное действие: {action} (ожидается install|uninstall)", file=sys.stderr)
            return 1
    except TuiConfigError as exc:
        print(f"[kimi-wtf] ошибка: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

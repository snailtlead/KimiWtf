#!/usr/bin/env python3
"""Кастомная строка состояния для Kimi Code CLI.

Читает JSON-снимок TUI из stdin (model, cwd, gitBranch, permissionMode,
planMode, contextUsage, contextTokens, maxContextTokens, sessionId, version)
и печатает ОДНУ строку футера, добавляя квоту управляемого аккаунта
(недельная квота + скользящее 5-часовое окно) в виде цветных прогресс-баров.

Квота запрашивается у GET <base_url>/usages с OAuth access-токеном, который
CLI хранит в credentials/kimi-code.json. Чтобы уложиться в бюджет TUI
(300 мс на запуск, не чаще раза в секунду) и не нагружать API, сетевые
запросы никогда не выполняются в пути рендера: при протухшем кеше запускается
отделённый процесс `--refresh`, а строка рисуется из последних известных
данных.

Кеш: $KIMI_CODE_HOME/statusline-quota-cache.json
  {"attempt_at": ts, "fetched_at": ts, "quota": {"week": {...}, "win5h": {...}}}

Скрипт — read-only потребитель файла токена: он никогда его не перезаписывает
и не ходит на OAuth token-endpoint; обновлением токена единолично владеет
сам CLI. Мёртвый токен приводит к HTTP 401, что эквивалентно любой другой
ошибке запроса: старые данные остаются, повтор не чаще раза в минуту.
"""

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

USAGES_URL = "https://api.kimi.com/coding/v1/usages"

CACHE_TTL_S = 300        # квота считается свежей 5 минут
RETRY_INTERVAL_S = 60    # не чаще одной попытки обновления в минуту
FETCH_TIMEOUT_S = 8      # таймаут запроса (только в отделённом рефрешере)

CACHE_FILE_NAME = "statusline-quota-cache.json"


# ---------- пути (вычисляются в момент вызова ради тестируемости) ----------

def kimi_home():
    return os.environ.get("KIMI_CODE_HOME") or os.path.join(os.path.expanduser("~"), ".kimi-code")


def credentials_path():
    return os.path.join(kimi_home(), "credentials", "kimi-code.json")


def cache_path():
    return os.path.join(kimi_home(), CACHE_FILE_NAME)


# ---------- кеш ----------

def read_cache():
    try:
        with open(cache_path(), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_cache(data):
    fd, tmp = tempfile.mkstemp(dir=kimi_home(), prefix=".statusline-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, cache_path())
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------- обновление квоты (отдельный процесс) ----------

def normalize_row(detail, window=None):
    if not isinstance(detail, dict):
        return None
    try:
        used = int(detail.get("used", 0))
        limit = int(detail.get("limit", 0))
    except (TypeError, ValueError):
        return None
    row = {"used": used, "limit": limit, "reset": detail.get("resetTime")}
    if window:
        row["window"] = window
    return row


def fetch_quota():
    """Запрашивает /usages и возвращает нормализованную квоту или None при ошибке."""
    try:
        with open(credentials_path(), encoding="utf-8") as f:
            token = json.load(f).get("access_token")
    except Exception:
        return None
    if not token:
        return None
    req = urllib.request.Request(
        USAGES_URL,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as r:
            payload = json.loads(r.read())
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    quota = {"week": normalize_row(payload.get("usage")), "win5h": None}
    for item in payload.get("limits") or []:
        if not isinstance(item, dict):
            continue
        row = normalize_row(item.get("detail"), item.get("window"))
        if row is None:
            continue
        # Скользящее rate-limit окно (например, 300 минут = 5 часов).
        if row.get("window", {}).get("timeUnit") == "TIME_UNIT_MINUTE":
            quota["win5h"] = row
    if quota["win5h"] is None and payload.get("limits"):
        first = payload["limits"][0]
        if isinstance(first, dict):
            quota["win5h"] = normalize_row(first.get("detail"), first.get("window"))
    return quota if (quota["week"] or quota["win5h"]) else None


def refresh():
    """Отделённый рефрешер: сначала фиксирует попытку, затем пробует сеть."""
    cache = read_cache()
    cache["attempt_at"] = time.time()
    write_cache(cache)
    quota = fetch_quota()
    if quota is not None:
        cache["fetched_at"] = time.time()
        cache["quota"] = quota
        write_cache(cache)


def maybe_spawn_refresh(cache):
    now = time.time()
    fresh = now - cache.get("fetched_at", 0) < CACHE_TTL_S
    attempted_recently = now - cache.get("attempt_at", 0) < RETRY_INTERVAL_S
    if fresh or attempted_recently:
        return
    with contextlib.suppress(Exception):
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )


# ---------- рендер ----------

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"


def fg(n):
    return f"\x1b[38;5;{n}m"


def usage_color(pct):
    # 80% совпадает с порогом, с которого консоль Kimi предлагает Extra Usage.
    if pct < 50:
        return 114  # зелёный
    if pct < 80:
        return 221  # жёлтый
    return 203  # красный


def progress_bar(pct, width=8):
    filled = max(0, min(width, round(pct * width / 100)))
    return f"{fg(usage_color(pct))}{'█' * filled}{fg(238)}{'░' * (width - filled)}{RESET}"


def fmt_reset(iso, with_date):
    if not isinstance(iso, str) or not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ""
    return dt.strftime("%d.%m" if with_date else "%H:%M")


def quota_segment(cache):
    quota = cache.get("quota") or {}
    parts = []
    for key, label, with_date in (("win5h", "5h", False), ("week", "wk", True)):
        row = quota.get(key)
        if not row or not row.get("limit"):
            continue
        pct = round(row["used"] * 100 / row["limit"])
        seg = f"{label} {progress_bar(pct)} {fg(usage_color(pct))}{pct}%{RESET}"
        reset = fmt_reset(row.get("reset"), with_date)
        if reset:
            seg += f" {DIM}↻{reset}{RESET}"
        parts.append(seg)
    return "  ".join(parts) if parts else f"{DIM}quota …{RESET}"


def shorten_cwd(cwd):
    home = os.path.expanduser("~")
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + cwd[len(home):]
    return cwd


def render(snapshot, cache):
    parts = []

    # Цвета повторяют встроенный футер: plan — акцентный, yolo/auto — warning.
    badges = []
    if snapshot.get("planMode"):
        badges.append(f"{BOLD}{fg(81)}plan{RESET}")
    perm = snapshot.get("permissionMode")
    if perm and perm != "manual":
        badges.append(f"{BOLD}{fg(221)}{perm}{RESET}")
    if badges:
        parts.append("[" + "+".join(badges) + "]")

    model = snapshot.get("model")
    if model:
        parts.append(str(model))

    cwd = snapshot.get("cwd") or ""
    if cwd:
        cwd = shorten_cwd(cwd)
        branch = snapshot.get("gitBranch")
        text = f"{cwd} ({branch})" if branch else cwd
        parts.append(f"{DIM}{text}{RESET}")

    # Расход контекста здесь намеренно не показывается: вторая строка футера
    # всегда рисует его справа, даже при кастомной команде.

    parts.append(quota_segment(cache))
    return f" {DIM}│{RESET} ".join(parts)


def main():
    if "--refresh" in sys.argv[1:]:
        refresh()
        return
    try:
        raw = sys.stdin.read()
        snapshot = json.loads(raw) if raw.strip() else {}
    except Exception:
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    cache = read_cache()
    maybe_spawn_refresh(cache)
    try:
        print(render(snapshot, cache))
    except Exception:
        # Запасной вариант: футер не должен ломаться никогда.
        print(snapshot.get("model") or "kimi")


if __name__ == "__main__":
    main()

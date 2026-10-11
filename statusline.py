#!/usr/bin/env python3
"""Кастомная строка состояния для Kimi Code CLI.

Читает JSON-снимок TUI из stdin (model, cwd, gitBranch, permissionMode,
planMode, contextUsage, contextTokens, maxContextTokens, sessionId, version)
и печатает ОДНУ строку футера, добавляя квоту управляемого аккаунта
(месячная квота + скользящее 5-часовое окно) в виде цветных прогресс-баров.

Квота запрашивается у GET <base_url>/usages с OAuth access-токеном. CLI
хранит токен в credentials/kimi-code-env-*.json (по одному файлу на окружение);
устаревший credentials/kimi-code.json читается как запасной вариант.
Чтобы уложиться в бюджет TUI
(300 мс на запуск, не чаще раза в секунду) и не нагружать API, сетевые
запросы никогда не выполняются в пути рендера: при протухшем кеше запускается
отделённый процесс `--refresh`, а строка рисуется из последних известных
данных.

Кеш: $KIMI_CODE_HOME/statusline-quota-cache.json
  {"attempt_at": ts, "fetched_at": ts, "quota": {"month": {...}, "win5h": {...}}}

Скрипт — read-only потребитель файла токена: он никогда его не перезаписывает
и не ходит на OAuth token-endpoint; обновлением токена единолично владеет
сам CLI. Мёртвый токен приводит к HTTP 401, что эквивалентно любой другой
ошибке запроса: старые данные остаются, повтор не чаще раза в минуту.

Кастомная строка полностью заменяет первую строку встроенного футера, поэтому
встроенный слот `tasks` (фоновые задачи) пропадает. Чтобы вернуть его, строка
дополнительно читает реестр задач текущей сессии с диска:
$KIMI_CODE_HOME/sessions/<wd>/session_<id>/agents/*/tasks/*.json — каждый файл
описывает задачу со status ("running" и т.д.) и kind ("agent" | "process").
Чтение локальное и дешёвое, сетевых запросов здесь нет.

Текущий thinking-эффорт (low/high/max) в снимке TUI отсутствует, поэтому
читается с диска: из последней записи llm.request в wire.jsonl текущей сессии
(отражает переключение эффорта на лету), а до первого запроса — из
[thinking].effort в config.toml. Показывается рядом с моделью: `K3·max`.
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


def load_token():
    """Access-токен из credentials.

    CLI пишет токен в kimi-code-env-<hash>.json (по файлу на окружение),
    а устаревший kimi-code.json может остаться пустым. Берём файл
    с непустым access_token и наибольшим expires_at.
    """
    cred_dir = os.path.join(kimi_home(), "credentials")
    try:
        names = [e.name for e in os.scandir(cred_dir)]
    except OSError:
        return None
    paths = [os.path.join(cred_dir, n) for n in names if n.startswith("kimi-code-env-") and n.endswith(".json")]
    paths.append(credentials_path())
    best = None
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        token = data.get("access_token")
        if not token:
            continue
        try:
            expires = float(data.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires = 0
        if best is None or expires > best[0]:
            best = (expires, token)
    return best[1] if best else None


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


def ratio_row(entry):
    """Строка квоты из записи usages.*: {"used_ratio": 0..1, "reset_time": iso}."""
    if not isinstance(entry, dict):
        return None
    try:
        pct = round(float(entry.get("used_ratio")) * 100)
    except (TypeError, ValueError):
        return None
    return {"pct": max(0, min(100, pct)), "reset": entry.get("reset_time")}


def fetch_quota():
    """Запрашивает /usages и возвращает нормализованную квоту или None при ошибке."""
    token = load_token()
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

    # Текущий формат: usages.limit_5h / limit_month_total / limit_month_code
    # (доли used_ratio + reset_time). Недельная квота (usage) отключена
    # сервисом, но разбирается ради старых ответов и кеша.
    quota = {"month": None, "week": normalize_row(payload.get("usage")), "win5h": None}
    usages = payload.get("usages")
    if isinstance(usages, dict):
        quota["month"] = ratio_row(usages.get("limit_month_total"))
        quota["win5h"] = ratio_row(usages.get("limit_5h"))
    for item in payload.get("limits") or []:
        if not isinstance(item, dict):
            continue
        row = normalize_row(item.get("detail"), item.get("window"))
        if row is None:
            continue
        # Скользящее rate-limit окно (например, 300 минут = 5 часов).
        if quota["win5h"] is None and row.get("window", {}).get("timeUnit") == "TIME_UNIT_MINUTE":
            quota["win5h"] = row
    if quota["win5h"] is None and payload.get("limits"):
        first = payload["limits"][0]
        if isinstance(first, dict):
            quota["win5h"] = normalize_row(first.get("detail"), first.get("window"))
    return quota if any(quota.values()) else None


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


# ---------- фоновые задачи текущей сессии ----------

def session_dir(snapshot):
    """Каталог сессии по sessionId из снимка TUI или None."""
    sid = snapshot.get("sessionId")
    if not isinstance(sid, str) or not sid:
        return None
    name = sid if sid.startswith("session_") else "session_" + sid
    base = os.path.join(kimi_home(), "sessions")
    try:
        workspaces = list(os.scandir(base))
    except OSError:
        return None
    for ws in workspaces:
        if not ws.is_dir():
            continue
        candidate = os.path.join(ws.path, name)
        if os.path.isdir(candidate):
            return candidate
    return None


def count_running_tasks(session):
    """Число запущенных фоновых задач по kind: {"agent": n, "process": n}."""
    counts = {"agent": 0, "process": 0}
    try:
        agents = [a for a in os.scandir(os.path.join(session, "agents")) if a.is_dir()]
    except OSError:
        return counts
    for agent in agents:
        try:
            entries = [e for e in os.scandir(os.path.join(agent.path, "tasks")) if e.name.endswith(".json")]
        except OSError:
            continue
        for entry in entries:
            try:
                with open(entry.path, encoding="utf-8") as f:
                    task = json.load(f)
            except Exception:
                continue
            if not isinstance(task, dict) or task.get("status") != "running":
                continue
            kind = task.get("kind")
            if kind in counts:
                counts[kind] += 1
    return counts


def tasks_segment(snapshot):
    """Сегмент `⚙ N agents [+ M shells]`; пустая строка, когда фоновых задач нет."""
    session = session_dir(snapshot)
    if not session:
        return ""
    counts = count_running_tasks(session)
    agents, procs = counts["agent"], counts["process"]
    if not agents and not procs:
        return ""
    parts = []
    if agents:
        parts.append(f"{agents} agent" + ("s" if agents > 1 else ""))
    if procs:
        parts.append(f"{procs} shell" + ("s" if procs > 1 else ""))
    return f"{fg(81)}⚙ {' + '.join(parts)}{RESET}"


# ---------- thinking-эффорт ----------

WIRE_TAIL_BYTES = 256 * 1024  # хвост wire.jsonl, который сканируем


def effort_from_wire(path):
    """Эффорт из последней записи с thinkingEffort в хвосте wire.jsonl."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - WIRE_TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if '"thinkingEffort"' not in line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            effort = record.get("thinkingEffort")
            if isinstance(effort, str) and effort:
                return effort
    return None


def effort_from_config():
    """Эффорт по умолчанию из [thinking].effort в config.toml (мини-парсер,
    чтобы не требовать tomllib из Python 3.11+)."""
    try:
        with open(os.path.join(kimi_home(), "config.toml"), encoding="utf-8") as f:
            section = ""
            for raw in f:
                line = raw.strip()
                if line.startswith("["):
                    section = line.strip("[]").strip()
                elif section == "thinking" and line.startswith("effort"):
                    value = line.partition("=")[2].strip().strip("\"'")
                    return value or None
    except OSError:
        pass
    return None


def current_effort(snapshot):
    """Эффорт текущей сессии: wire.jsonl, до первого запроса — config.toml."""
    session = session_dir(snapshot)
    if session:
        effort = effort_from_wire(os.path.join(session, "agents", "main", "wire.jsonl"))
        if effort:
            return effort
    return effort_from_config()


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
    for key, label, with_date in (("win5h", "5h", False), ("month", "mo", True), ("week", "wk", True)):
        row = quota.get(key)
        if not row:
            continue
        pct = row.get("pct")
        if pct is None:
            if not row.get("limit"):
                continue
            pct = round(row.get("used", 0) * 100 / row["limit"])
        seg = f"{label} {progress_bar(pct)} {fg(usage_color(pct))}{pct}%{RESET}"
        reset = fmt_reset(row.get("reset"), with_date)
        if reset:
            # Пробел после иконки: ↻ в ряде шрифтов шире одной ячейки,
            # без пробела глиф визуально наезжает на цифры времени.
            seg += f" {DIM}↻ {reset}{RESET}"
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
        text = str(model)
        effort = current_effort(snapshot)
        if effort:
            text += f"{DIM}·{effort}{RESET}"
        parts.append(text)

    cwd = snapshot.get("cwd") or ""
    if cwd:
        cwd = shorten_cwd(cwd)
        branch = snapshot.get("gitBranch")
        text = f"{cwd} ({branch})" if branch else cwd
        parts.append(f"{DIM}{text}{RESET}")

    # Расход контекста здесь намеренно не показывается: вторая строка футера
    # всегда рисует его справа, даже при кастомной команде.

    tasks = tasks_segment(snapshot)
    if tasks:
        parts.append(tasks)

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

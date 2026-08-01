"""Тесты статус-бара statusline.py."""

import json
import re
import subprocess
import sys
import urllib.error

import pytest

import statusline

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text):
    return ANSI.sub("", text)


PAYLOAD = {
    "usage": {"limit": "100", "used": "3", "remaining": "97", "resetTime": "2026-08-05T19:24:53Z"},
    "limits": [
        {
            "window": {"duration": 300, "timeUnit": "TIME_UNIT_MINUTE"},
            "detail": {"limit": "100", "used": "13", "remaining": "87", "resetTime": "2026-07-30T00:24:53Z"},
        }
    ],
}


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# ---------- пути ----------

def test_paths_follow_env(home, monkeypatch):
    assert statusline.kimi_home() == str(home)
    assert statusline.cache_path() == str(home / statusline.CACHE_FILE_NAME)
    assert statusline.credentials_path().endswith("credentials/kimi-code.json")
    monkeypatch.delenv("KIMI_CODE_HOME")
    assert statusline.kimi_home().endswith("/.kimi-code")


# ---------- кеш ----------

def test_read_cache_missing(home):
    assert statusline.read_cache() == {}


def test_cache_roundtrip(home):
    statusline.write_cache({"attempt_at": 1, "quota": {"week": {"used": 1}}})
    cache_file = home / statusline.CACHE_FILE_NAME
    assert json.loads(cache_file.read_text())["quota"]["week"]["used"] == 1
    assert (cache_file.stat().st_mode & 0o777) == 0o600


def test_read_cache_invalid_json(home):
    (home / statusline.CACHE_FILE_NAME).write_text("not json")
    assert statusline.read_cache() == {}


def test_read_cache_non_dict(home):
    (home / statusline.CACHE_FILE_NAME).write_text("[1, 2]")
    assert statusline.read_cache() == {}


def test_write_cache_cleans_tmp_on_failure(home, monkeypatch):
    monkeypatch.setattr(statusline.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("boom")))
    statusline.write_cache({"a": 1})
    assert not (home / statusline.CACHE_FILE_NAME).exists()
    assert not list(home.glob(".statusline-*"))


# ---------- normalize_row ----------

def test_normalize_row_ok():
    row = statusline.normalize_row({"used": "7", "limit": "100", "resetTime": "x"}, {"duration": 5})
    assert row == {"used": 7, "limit": 100, "reset": "x", "window": {"duration": 5}}


def test_normalize_row_defaults_and_bad_input():
    assert statusline.normalize_row({}) == {"used": 0, "limit": 0, "reset": None}
    assert statusline.normalize_row(None) is None
    assert statusline.normalize_row({"used": "nan-int", "limit": "100"}) is None


# ---------- fetch_quota ----------

def _write_credentials(home, token="token-123"):
    cred_dir = home / "credentials"
    cred_dir.mkdir()
    (cred_dir / "kimi-code.json").write_text(json.dumps({"access_token": token}))


def test_fetch_quota_success(home, monkeypatch):
    _write_credentials(home)
    monkeypatch.setattr(statusline.urllib.request, "urlopen", lambda req, timeout: FakeResponse(PAYLOAD))
    quota = statusline.fetch_quota()
    assert quota["week"] == {"used": 3, "limit": 100, "reset": "2026-08-05T19:24:53Z"}
    assert quota["win5h"]["used"] == 13
    assert quota["win5h"]["window"]["timeUnit"] == "TIME_UNIT_MINUTE"


def test_fetch_quota_sends_auth_header(home, monkeypatch):
    _write_credentials(home, token="secret-token")
    seen = {}

    def fake_urlopen(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return FakeResponse(PAYLOAD)

    monkeypatch.setattr(statusline.urllib.request, "urlopen", fake_urlopen)
    statusline.fetch_quota()
    assert seen["auth"] == "Bearer secret-token"


def test_fetch_quota_fallback_to_first_limit(home, monkeypatch):
    _write_credentials(home)
    payload = {
        "limits": [
            {
                "window": {"duration": 1, "timeUnit": "TIME_UNIT_WEEK"},
                "detail": {"limit": "50", "used": "10"},
            }
        ]
    }
    monkeypatch.setattr(statusline.urllib.request, "urlopen", lambda req, timeout: FakeResponse(payload))
    quota = statusline.fetch_quota()
    assert quota["week"] is None
    assert quota["win5h"]["used"] == 10


def test_fetch_quota_skips_broken_limit_items(home, monkeypatch):
    _write_credentials(home)
    payload = {"limits": ["junk", {"detail": None}], "usage": {"used": "1", "limit": "100"}}
    monkeypatch.setattr(statusline.urllib.request, "urlopen", lambda req, timeout: FakeResponse(payload))
    quota = statusline.fetch_quota()
    assert quota["win5h"] is None
    assert quota["week"]["used"] == 1


def test_fetch_quota_no_credentials(home):
    assert statusline.fetch_quota() is None


def test_fetch_quota_bad_credentials_json(home):
    cred_dir = home / "credentials"
    cred_dir.mkdir()
    (cred_dir / "kimi-code.json").write_text("{broken")
    assert statusline.fetch_quota() is None


def test_fetch_quota_empty_token(home):
    _write_credentials(home, token="")
    assert statusline.fetch_quota() is None


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
        urllib.error.URLError("no route"),
        TimeoutError("timed out"),
        ValueError("bad json"),
    ],
)
def test_fetch_quota_request_failures(home, monkeypatch, error):
    _write_credentials(home)

    def boom(req, timeout):
        raise error

    monkeypatch.setattr(statusline.urllib.request, "urlopen", boom)
    assert statusline.fetch_quota() is None


def test_fetch_quota_non_dict_payload(home, monkeypatch):
    _write_credentials(home)
    monkeypatch.setattr(statusline.urllib.request, "urlopen", lambda req, timeout: FakeResponse([1, 2, 3]))
    assert statusline.fetch_quota() is None


def test_fetch_quota_empty_payload(home, monkeypatch):
    _write_credentials(home)
    monkeypatch.setattr(statusline.urllib.request, "urlopen", lambda req, timeout: FakeResponse({}))
    assert statusline.fetch_quota() is None


# ---------- refresh / троттлинг ----------

def test_refresh_success_writes_quota(home, monkeypatch):
    monkeypatch.setattr(statusline, "fetch_quota", lambda: {"week": {"used": 1, "limit": 100, "reset": None}})
    statusline.refresh()
    cache = json.loads((home / statusline.CACHE_FILE_NAME).read_text())
    assert cache["quota"]["week"]["used"] == 1
    assert cache["fetched_at"] > 0
    assert cache["attempt_at"] > 0


def test_refresh_failure_keeps_old_quota(home, monkeypatch):
    statusline.write_cache({"fetched_at": 5, "quota": {"week": {"used": 42, "limit": 100}}})
    monkeypatch.setattr(statusline, "fetch_quota", lambda: None)
    statusline.refresh()
    cache = json.loads((home / statusline.CACHE_FILE_NAME).read_text())
    assert cache["fetched_at"] == 5
    assert cache["quota"]["week"]["used"] == 42
    assert cache["attempt_at"] > 5


def test_maybe_spawn_refresh_fresh_cache_no_spawn(home, monkeypatch):
    monkeypatch.setattr(statusline.subprocess, "Popen", lambda *a, **k: pytest.fail("spawn не ожидался"))
    statusline.maybe_spawn_refresh({"fetched_at": statusline.time.time()})


def test_maybe_spawn_refresh_throttled(home, monkeypatch):
    monkeypatch.setattr(statusline.subprocess, "Popen", lambda *a, **k: pytest.fail("spawn не ожидался"))
    now = statusline.time.time()
    statusline.maybe_spawn_refresh({"fetched_at": 0, "attempt_at": now})


def test_maybe_spawn_refresh_stale_spawns(home, monkeypatch):
    calls = []

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(statusline.subprocess, "Popen", FakePopen)
    statusline.maybe_spawn_refresh({"fetched_at": 0, "attempt_at": 0})
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == sys.executable
    assert args[1].endswith("statusline.py")
    assert args[2] == "--refresh"
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["start_new_session"] is True


def test_maybe_spawn_refresh_swallows_spawn_errors(home, monkeypatch):
    monkeypatch.setattr(statusline.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(OSError("no exec")))
    statusline.maybe_spawn_refresh({"fetched_at": 0, "attempt_at": 0})


# ---------- рендер ----------

def test_usage_color_thresholds():
    assert statusline.usage_color(0) == 114
    assert statusline.usage_color(49) == 114
    assert statusline.usage_color(50) == 221
    assert statusline.usage_color(79) == 221
    assert statusline.usage_color(80) == 203
    assert statusline.usage_color(100) == 203


def test_progress_bar_fill():
    assert strip_ansi(statusline.progress_bar(0)) == "░" * 8
    assert strip_ansi(statusline.progress_bar(50)) == "█" * 4 + "░" * 4
    assert strip_ansi(statusline.progress_bar(100)) == "█" * 8
    assert strip_ansi(statusline.progress_bar(150)) == "█" * 8
    assert strip_ansi(statusline.progress_bar(-5)) == "░" * 8


def test_fmt_reset():
    assert statusline.fmt_reset(None, False) == ""
    assert statusline.fmt_reset("garbage", False) == ""
    assert statusline.fmt_reset(123, False) == ""
    assert re.fullmatch(r"\d{2}:\d{2}", statusline.fmt_reset("2026-07-30T00:24:53Z", False))
    assert re.fullmatch(r"\d{2}\.\d{2}", statusline.fmt_reset("2026-08-05T19:24:53Z", True))


def test_quota_segment_empty():
    assert strip_ansi(statusline.quota_segment({})) == "quota …"
    assert strip_ansi(statusline.quota_segment({"quota": {}})) == "quota …"
    assert strip_ansi(statusline.quota_segment({"quota": {"week": {"used": 1, "limit": 0}}})) == "quota …"


def test_quota_segment_both_rows():
    cache = {
        "quota": {
            "week": {"used": 3, "limit": 100, "reset": "2026-08-05T19:24:53Z"},
            "win5h": {"used": 13, "limit": 100, "reset": "2026-07-30T00:24:53Z"},
        }
    }
    text = strip_ansi(statusline.quota_segment(cache))
    assert "5h █░░░░░░░ 13%" in text
    assert "wk ░░░░░░░░ 3%" in text
    assert "↻" in text


def test_quota_segment_without_reset():
    cache = {"quota": {"week": {"used": 50, "limit": 200, "reset": None}}}
    text = strip_ansi(statusline.quota_segment(cache))
    assert text == "wk ██░░░░░░ 25%"


def test_shorten_cwd():
    home_dir = statusline.os.path.expanduser("~")
    assert statusline.shorten_cwd(home_dir) == "~"
    assert statusline.shorten_cwd(home_dir + "/proj") == "~/proj"
    assert statusline.shorten_cwd("/etc") == "/etc"


def test_render_full_line():
    snapshot = {
        "model": "K3",
        "cwd": statusline.os.path.expanduser("~"),
        "gitBranch": "main",
        "permissionMode": "yolo",
        "planMode": True,
    }
    cache = {"quota": {"week": {"used": 3, "limit": 100, "reset": None}}}
    text = strip_ansi(statusline.render(snapshot, cache))
    assert text.startswith("[plan+yolo]")
    assert "K3" in text
    assert "~ (main)" in text
    assert "wk ░░░░░░░░ 3%" in text
    assert "ctx" not in text  # контекст рисует вторая строка футера


def test_render_minimal_snapshot():
    text = strip_ansi(statusline.render({}, {}))
    assert "quota …" in text
    assert "[" not in text


def test_render_resets_ansi_per_segment():
    line = statusline.render({"model": "K3", "permissionMode": "auto"}, {})
    assert line.count("\x1b[0m") >= 4  # каждый окрашенный сегмент закрыт


# ---------- фоновые задачи ----------

def _make_session(home, sid="abc-123"):
    session = home / "sessions" / "wd_test" / f"session_{sid}"
    session.mkdir(parents=True)
    return session


def _write_task(session, agent, task_id, status, kind="agent"):
    tasks = session / "agents" / agent / "tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.json").write_text(json.dumps({"status": status, "kind": kind}))


def test_session_dir_resolves_snapshot_id(home):
    session = _make_session(home)
    assert statusline.session_dir({"sessionId": "abc-123"}) == str(session)
    assert statusline.session_dir({"sessionId": "session_abc-123"}) == str(session)


def test_session_dir_missing(home):
    assert statusline.session_dir({}) is None
    assert statusline.session_dir({"sessionId": ""}) is None
    assert statusline.session_dir({"sessionId": "nope"}) is None
    assert statusline.session_dir({"sessionId": 42}) is None


def test_count_running_tasks(home):
    session = _make_session(home)
    _write_task(session, "main", "agent-1", "running", "agent")
    _write_task(session, "main", "bash-1", "running", "process")
    _write_task(session, "main", "bash-2", "completed", "process")
    _write_task(session, "agent-0", "agent-2", "running", "agent")
    _write_task(session, "agent-0", "other", "running", "cron")  # неизвестный kind не считаем
    (session / "agents" / "agent-0" / "tasks" / "broken.json").write_text("{junk")
    assert statusline.count_running_tasks(str(session)) == {"agent": 2, "process": 1}


def test_count_running_tasks_no_dirs(home):
    session = _make_session(home)
    assert statusline.count_running_tasks(str(session)) == {"agent": 0, "process": 0}
    assert statusline.count_running_tasks(str(home / "missing")) == {"agent": 0, "process": 0}


def test_tasks_segment(home):
    session = _make_session(home)
    assert statusline.tasks_segment({"sessionId": "abc-123"}) == ""
    assert statusline.tasks_segment({}) == ""
    _write_task(session, "main", "agent-1", "running", "agent")
    _write_task(session, "main", "bash-1", "running", "process")
    assert strip_ansi(statusline.tasks_segment({"sessionId": "abc-123"})) == "⚙ 1 agent + 1 shell"


def test_tasks_segment_only_processes(home):
    session = _make_session(home)
    _write_task(session, "main", "bash-1", "running", "process")
    _write_task(session, "main", "bash-2", "running", "process")
    assert strip_ansi(statusline.tasks_segment({"sessionId": "abc-123"})) == "⚙ 2 shells"


def test_render_includes_tasks_segment(home):
    session = _make_session(home)
    _write_task(session, "main", "agent-1", "running", "agent")
    text = strip_ansi(statusline.render({"sessionId": "abc-123"}, {}))
    assert "⚙ 1 agent" in text


# ---------- main ----------

def _run_main(monkeypatch, capsys, stdin_text):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    monkeypatch.setattr(sys, "argv", ["statusline.py"])

    class FakePopen:  # реальные процессы в тестах не порождаем
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr(statusline.subprocess, "Popen", FakePopen)
    statusline.main()
    return capsys.readouterr().out


def test_main_valid_stdin(home, monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, '{"model": "K3", "cwd": "/etc"}')
    assert "K3" in out and out.count("\n") == 1


def test_main_garbage_stdin(home, monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "not json at all")
    assert "quota …" in strip_ansi(out)


def test_main_empty_stdin(home, monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "")
    assert strip_ansi(out).strip() != ""


def test_main_non_dict_stdin(home, monkeypatch, capsys):
    out = _run_main(monkeypatch, capsys, "[1, 2, 3]")
    assert "quota …" in strip_ansi(out)


def test_main_fallback_when_render_raises(home, monkeypatch, capsys):
    monkeypatch.setattr(statusline, "render", lambda *_: (_ for _ in ()).throw(RuntimeError("boom")))
    out = _run_main(monkeypatch, capsys, '{"model": "K3"}')
    assert out.strip() == "K3"


def test_main_refresh_branch(home, monkeypatch):
    called = []
    monkeypatch.setattr(statusline, "refresh", lambda: called.append(True))
    monkeypatch.setattr(sys, "argv", ["statusline.py", "--refresh"])
    statusline.main()
    assert called == [True]

"""Тесты установщика статус-строки scripts/tui_config.py."""

import json
import os

import pytest
import tui_config


@pytest.fixture()
def plugin_root(tmp_path):
    """Поддельный корень плагина со statusline.py."""
    root = tmp_path / "plugins" / "managed" / "kimi-wtf"
    (root / "scripts").mkdir(parents=True)
    (root / "statusline.py").write_text("# statusline\n")
    return root


# ---------- мини-патчер ----------

def test_set_command_into_empty_text():
    out = tui_config.set_status_line_command("", "/x/statusline.py")
    assert out == '[status_line]\ncommand = "/x/statusline.py"\n'


def test_set_command_preserves_other_sections():
    original = 'theme = "dark"\n\n[editor]\ncommand = "vim"\n'
    out = tui_config.set_status_line_command(original, "/x/s.py")
    assert 'theme = "dark"' in out
    assert '[editor]\ncommand = "vim"' in out
    assert '[status_line]\ncommand = "/x/s.py"' in out
    assert out.index("[status_line]") > out.index("[editor]")


def test_set_command_replaces_existing_uncommented():
    original = '[status_line]\ncommand = "/old.py"\nitems = ["mode"]\n'
    out = tui_config.set_status_line_command(original, "/new.py")
    assert 'command = "/new.py"' in out
    assert "/old.py" not in out
    assert 'items = ["mode"]' in out


def test_set_command_ignores_commented_examples():
    original = '# [status_line]\n# items = ["mode"]\n# command = "~/x.sh"\n\ntheme = "dark"\n'
    out = tui_config.set_status_line_command(original, "/new.py")
    # Закомментированный пример не тронут, секция добавлена настоящая.
    assert '# command = "~/x.sh"' in out
    assert '\n[status_line]\ncommand = "/new.py"\n' in out


def test_set_command_inserts_after_header_before_next_section():
    original = '[status_line]\nitems = ["mode"]\n\n[upgrade]\nauto_install = true\n'
    out = tui_config.set_status_line_command(original, "/new.py")
    lines = out.splitlines()
    assert lines[0] == "[status_line]"
    assert lines[1] == 'command = "/new.py"'
    assert lines[2] == 'items = ["mode"]'
    assert "[upgrade]" in out


def test_set_command_does_not_touch_other_sections_command_key():
    original = '[editor]\ncommand = "vim"\n'
    out = tui_config.set_status_line_command(original, "/new.py")
    assert '[editor]\ncommand = "vim"' in out


def test_remove_command():
    original = 'theme = "dark"\n\n[status_line]\ncommand = "/x.py"\nitems = ["mode"]\n'
    out = tui_config.remove_status_line_command(original)
    assert "command" not in out.split("[status_line]")[1]
    assert 'items = ["mode"]' in out
    assert 'theme = "dark"' in out


def test_remove_command_drops_empty_section():
    original = 'theme = "dark"\n\n[status_line]\ncommand = "/x.py"\n'
    out = tui_config.remove_status_line_command(original)
    assert out == 'theme = "dark"\n'


def test_remove_command_no_section_is_noop():
    original = 'theme = "dark"\n'
    assert tui_config.remove_status_line_command(original) == original


def test_remove_command_only_from_status_line_section():
    original = '[editor]\ncommand = "vim"\n\n[status_line]\ncommand = "/x.py"\n'
    out = tui_config.remove_status_line_command(original)
    assert '[editor]\ncommand = "vim"' in out
    assert "[status_line]" not in out


def test_toml_basic_string_escaping():
    assert tui_config._toml_basic_string('a"b\\c') == '"a\\"b\\\\c"'


def test_section_header_parsing():
    assert tui_config._section_header("[status_line]") == "status_line"
    assert tui_config._section_header('  ["quoted.name"]  ') == "quoted.name"
    assert tui_config._section_header("# [status_line]") is None
    assert tui_config._section_header("[[permission.rules]]") is None
    assert tui_config._section_header('theme = "dark"') is None


# ---------- install / uninstall ----------

def test_install_writes_command(home, plugin_root, capsys):
    tui_config.install(str(plugin_root))
    text = (home / "tui.toml").read_text()
    expected = f'command = "{plugin_root}/statusline.py"'
    assert expected in text
    out = capsys.readouterr().out
    assert "/reload-tui" in out


def test_install_missing_statusline_fails(home, tmp_path):
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    with pytest.raises(tui_config.TuiConfigError):
        tui_config.install(str(empty_root))
    assert not (home / "tui.toml").exists()


def test_install_idempotent(home, plugin_root):
    tui_config.install(str(plugin_root))
    before = (home / "tui.toml").read_text()
    tui_config.install(str(plugin_root))
    assert (home / "tui.toml").read_text() == before
    # Повтор без изменений — бэкап не пересоздаётся.
    assert not (home / "tui.toml.bak").exists()


def test_install_creates_backup_on_change(home, plugin_root):
    (home / "tui.toml").write_text('theme = "dark"\n')
    tui_config.install(str(plugin_root))
    assert (home / "tui.toml.bak").read_text() == 'theme = "dark"\n'


def test_install_replaces_foreign_command(home, plugin_root):
    (home / "tui.toml").write_text('[status_line]\ncommand = "~/other.sh"\n')
    tui_config.install(str(plugin_root))
    text = (home / "tui.toml").read_text()
    assert "~/other.sh" not in text
    assert str(plugin_root) in text


def test_uninstall_without_config(home, capsys):
    tui_config.uninstall()
    assert "и так не был настроен" in capsys.readouterr().out


def test_uninstall_removes_key(home, plugin_root):
    tui_config.install(str(plugin_root))
    tui_config.uninstall()
    text = (home / "tui.toml").read_text()
    assert "status_line" not in text


def test_install_preserves_existing_settings(home, plugin_root):
    (home / "tui.toml").write_text(
        'theme = "auto"\n\n[notifications]\nenabled = false\n\n# [status_line]\n# command = "~/example.sh"\n'
    )
    tui_config.install(str(plugin_root))
    tui_config.uninstall()
    text = (home / "tui.toml").read_text()
    assert 'theme = "auto"' in text
    assert "[notifications]" in text
    assert '# command = "~/example.sh"' in text


def test_malformed_toml_aborts_without_changes(home, plugin_root):
    bad = "theme = = broken\n"
    (home / "tui.toml").write_text(bad)
    with pytest.raises(tui_config.TuiConfigError, match="валидным TOML"):
        tui_config.install(str(plugin_root))
    assert (home / "tui.toml").read_text() == bad


# ---------- main ----------

def test_main_help(capsys):
    assert tui_config.main(["--help"]) == 0
    assert "install" in capsys.readouterr().out


def test_main_unknown_action(capsys):
    assert tui_config.main(["dance"]) == 1
    assert "неизвестное действие" in capsys.readouterr().err


def test_main_install_and_uninstall(home, plugin_root):
    assert tui_config.main(["install", str(plugin_root)]) == 0
    assert str(plugin_root) in (home / "tui.toml").read_text()
    assert tui_config.main(["uninstall"]) == 0
    assert "status_line" not in (home / "tui.toml").read_text()


def test_main_error_exit_code(home, tmp_path, capsys):
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    assert tui_config.main(["install", str(empty_root)]) == 2
    assert "ошибка" in capsys.readouterr().err


def test_main_default_plugin_root(home, monkeypatch, plugin_root):
    """Без аргумента корень вычисляется от расположения скрипта."""
    script_copy = plugin_root / "scripts" / "tui_config.py"
    script_copy.write_text("# copy\n")
    monkeypatch.setattr(tui_config, "__file__", str(script_copy))
    assert tui_config.main(["install"]) == 0
    assert str(plugin_root) in (home / "tui.toml").read_text()


def test_paths_follow_env(home, monkeypatch):
    assert tui_config.tui_toml_path() == str(home / "tui.toml")
    monkeypatch.delenv("KIMI_CODE_HOME")
    assert tui_config.kimi_home().endswith("/.kimi-code")


def test_backup_not_created_for_new_file(home, plugin_root):
    tui_config.install(str(plugin_root))
    assert not (home / "tui.toml.bak").exists()
    bak_list = list(home.glob("*.bak"))
    assert bak_list == []


def test_full_cycle_json_shape(home, plugin_root):
    """Итоговый tui.toml остаётся валидным TOML с нужным значением ключа."""
    tomllib = pytest.importorskip("tomllib")
    tui_config.install(str(plugin_root))

    with open(home / "tui.toml", "rb") as f:
        parsed = tomllib.load(f)
    expected = os.path.join(str(plugin_root), "statusline.py")
    assert parsed["status_line"]["command"] == expected
    json.dumps(parsed)  # значения сериализуемы, кракозябр нет

# KimiWtf

[![CI](https://github.com/snailtlead/KimiWtf/actions/workflows/ci.yml/badge.svg)](https://github.com/snailtlead/KimiWtf/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/snailtlead/KimiWtf/branch/main/graph/badge.svg)](https://codecov.io/gh/snailtlead/KimiWtf)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> Бейдж покрытия показывает `unknown`, пока в Codecov не добавлен репозиторий и в секреты GitHub (`CODECOV_TOKEN`) не сохранён Repository Upload Token — выгрузка отчёта в CI настроена и оживает сразу после этого.

Плагин для [Kimi Code CLI](https://www.kimi.com/code/docs/en/), который показывает квоту управляемого аккаунта в строке состояния: недельную квоту и скользящее 5-часовое окно в виде цветных прогресс-баров. Кеш с TTL 5 минут, не чаще одного запроса к API в минуту, токен используется только на чтение.

> **English:** A Kimi Code CLI plugin that renders your managed account quota (weekly quota + rolling 5-hour window) as colored progress bars in the footer status line. Rendering never touches the network: quota is fetched by a detached refresher (5-minute cache TTL, at most one API call per minute, read-only use of the OAuth token). Install with `/plugins install https://github.com/snailtlead/KimiWtf`, then run `/kimi-wtf:install` and `/reload-tui`.

## Как выглядит

```
[plan+yolo] │ K3 │ ~/proj (main) │ 5h █░░░░░░░ 16% ↻03:24  wk ░░░░░░░░ 3% ↻05.08
```

Цвет бара и процента — по порогам расхода: зелёный < 50%, жёлтый 50–80%, красный ≥ 80% (порог совпадает с тем, с которого консоль Kimi предлагает подключить Extra Usage). `↻` — время сброса окна (локальное).

## Установка

1. В Kimi Code CLI: `/plugins install https://github.com/snailtlead/KimiWtf`
2. Включить статус-бар: `/kimi-wtf:install`
3. Применить: `/reload-tui` (или перезапуск CLI)

Команда `/kimi-wtf:install` идемпотентно прописывает `[status_line] command` в `~/.kimi-code/tui.toml`, указывая на managed-копию плагина (путь переживает обновления плагина), и оставляет резервную копию `tui.toml.bak`. Остальное содержимое файла не трогается.

## Снятие

`/kimi-wtf:uninstall`, затем `/reload-tui` — футер вернётся к встроенному виду.

## Как это работает

- TUI вызывает команду из `[status_line] command`, передаёт JSON-снимок сессии в stdin (модель, cwd, git-ветка, режимы, расход контекста) и ждёт не более 300 мс, не чаще раза в секунду.
- `statusline.py` рендерит строку только из кеша `~/.kimi-code/statusline-quota-cache.json`; при протухшем кеше порождает отделённый фоновый рефрешер и сразу рисует последние известные данные.
- Рефрешер читает OAuth access-token из `~/.kimi-code/credentials/kimi-code.json` (файл только читается; обновлением токена единолично владеет сам CLI) и запрашивает `GET https://api.kimi.com/coding/v1/usages`. При 401 или ошибке сети старые данные сохраняются, повторная попытка — не раньше чем через 60 секунд.
- Кастомная строка заменяет первую строку футера; расход контекста остаётся на второй строке справа, поэтому в статус-баре не дублируется.

## Разработка

Зависимостей времени выполнения нет (только стандартная библиотека). Для разработки:

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pytest-cov ruff
.venv/bin/python -m pytest --cov   # порог покрытия 90%, фактически ~98%
.venv/bin/ruff check .
```

Структура: `statusline.py` — статус-бар; `scripts/tui_config.py` — установщик ключа в `tui.toml`; `commands/` — слэш-команды плагина; `tests/` — pytest.

## Лицензия

[MIT](LICENSE)

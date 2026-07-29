# KimiWtf

[![CI](https://github.com/snailtlead/KimiWtf/actions/workflows/ci.yml/badge.svg)](https://github.com/snailtlead/KimiWtf/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/snailtlead/KimiWtf/badges/coverage.json)](https://github.com/snailtlead/KimiWtf/blob/badges/coverage.json)
[![version](https://img.shields.io/badge/version-0.1.0-blue.svg)](kimi.plugin.json)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> Бейдж покрытия генерируется самим CI из отчёта pytest-cov и публикуется в ветку [`badges`](https://github.com/snailtlead/KimiWtf/blob/badges/coverage.json) — без сторонних сервисов; значение проверяемо по JSON в ветке и логу прогона.

Плагин для [Kimi Code CLI](https://www.kimi.com/code/docs/en/), который показывает квоту управляемого аккаунта в строке состояния: недельную квоту и скользящее 5-часовое окно в виде цветных прогресс-баров. Кеш с TTL 5 минут, не чаще одного запроса к API в минуту, токен используется только на чтение.

> **English:** A Kimi Code CLI plugin that renders your managed account quota (weekly quota + rolling 5-hour window) as colored progress bars in the footer status line. Rendering never touches the network: quota is fetched by a detached refresher (5-minute cache TTL, at most one API call per minute, read-only use of the OAuth token). Requires Kimi Code CLI with OAuth login and Python 3.10+. Install with `/plugins install https://github.com/snailtlead/KimiWtf`, then run `/kimi-wtf:install` and `/reload-tui`.

## Как выглядит

```
[plan+yolo] │ K3 │ ~/proj (main) │ 5h █░░░░░░░ 16% ↻03:24  wk ░░░░░░░░ 3% ↻05.08
```

Цвет бара и процента — по порогам расхода: зелёный < 50%, жёлтый 50–80%, красный ≥ 80% (порог совпадает с тем, с которого консоль Kimi предлагает подключить Extra Usage). `↻` — время сброса окна (локальное). Бейджы режимов (`plan`, `yolo`, `auto`) показываются только в нестандартных режимах.

## Требования

- **Kimi Code CLI** с выполненным OAuth-логином управляемого аккаунта (`/login`) — токен читается из `~/.kimi-code/credentials/kimi-code.json`;
- **Python ≥ 3.10** на `PATH` (статус-команда выполняется как `python3 <путь>`);
- терминал с UTF-8 и 256-цветной палитрой (блоки `█`/`░`, ANSI-цвета);
- сетевой доступ к `https://api.kimi.com` для фонового обновления квоты.

Зависимостей времени выполнения нет — только стандартная библиотека Python.

## Установка

Плагином (рекомендуется):

1. В Kimi Code CLI: `/plugins install https://github.com/snailtlead/KimiWtf`
2. Включить статус-бар: `/kimi-wtf:install`
3. Применить: `/reload-tui` (или перезапуск CLI)

Команда `/kimi-wtf:install` идемпотентно прописывает `[status_line] command` в `~/.kimi-code/tui.toml`, указывая на managed-копию плагина (путь переживает обновления плагина), и оставляет резервную копию `tui.toml.bak`. Остальное содержимое файла не трогается.

### Ручная установка (без плагина)

```bash
git clone https://github.com/snailtlead/KimiWtf
python3 KimiWtf/scripts/tui_config.py install
# затем /reload-tui в запущенной сессии
```

## Снятие

- плагином: `/kimi-wtf:uninstall`, затем `/reload-tui`;
- при ручной установке: `python3 KimiWtf/scripts/tui_config.py uninstall`, затем `/reload-tui`.

Ключ `[status_line] command` удаляется, футер возвращается к встроенному виду; остальное содержимое `tui.toml` сохраняется.

## Как это работает

- TUI вызывает команду из `[status_line] command` через `sh -c`, передаёт JSON-снимок сессии в stdin (модель, cwd, git-ветка, режимы, расход контекста) и ждёт не более 300 мс, не чаще раза в секунду.
- `statusline.py` рендерит строку только из кеша `~/.kimi-code/statusline-quota-cache.json`; при протухшем кеше порождает отделённый фоновый рефрешер и сразу рисует последние известные данные.
- Рефрешер читает OAuth access-token из `~/.kimi-code/credentials/kimi-code.json` (файл только читается; обновлением токена единолично владеет сам CLI) и запрашивает `GET https://api.kimi.com/coding/v1/usages`. При 401 или ошибке сети старые данные сохраняются, повторная попытка — не раньше чем через 60 секунд.
- Кастомная строка заменяет первую строку футера; расход контекста остаётся на второй строке справа, поэтому в статус-баре не дублируется.

## Конфигурация и файлы

| Путь / переменная | Назначение |
|---|---|
| `~/.kimi-code/tui.toml` | ключ `[status_line] command` — включает статус-бар (ставится установщиком) |
| `~/.kimi-code/statusline-quota-cache.json` | кеш квоты: `attempt_at`, `fetched_at`, `quota` (режим 600) |
| `~/.kimi-code/credentials/kimi-code.json` | OAuth-токен CLI (только читается) |
| `KIMI_CODE_HOME` | переопределяет корень конфигурации (по умолчанию `~/.kimi-code`) |

Параметры по умолчанию зашиты в `statusline.py`: TTL кеша 300 с (`CACHE_TTL_S`), минимальный интервал между обновлениями 60 с (`RETRY_INTERVAL_S`), таймаут запроса 8 с (`FETCH_TIMEOUT_S`).

## Ограничения

- Эндпоинт `/coding/v1/usages` **недокументированный** (восстановлен по поведению CLI): формат ответа может измениться. При любых ошибках статус-бар деградирует к последним известным данным или `quota …`, но не ломает футер.
- Баланс Extra Usage (`boosterWallet`) пока не отображается.
- Плагины Kimi Code устанавливаются per-user (на все проекты), project-scoped установки пока нет.

## Устранение неполадок

- **`quota …` в строке** — квоту получить не удалось: нет токена (выполните `/login`), 401 (CLI освежит токен при активности) или нет сети. Диагностика: `python3 <путь>/statusline.py --refresh && cat ~/.kimi-code/statusline-quota-cache.json`.
- **Строка не изменилась после install** — выполните `/reload-tui` (применяет `tui.toml` без перезапуска).
- **Футер вернулся к встроенному виду** — статус-команда падала; запустите вручную то, что записано в `command`: `sh -c "$(sed -n 's/^command = "\(.*\)"$/\1/p' ~/.kimi-code/tui.toml)"`.
- **Данные выглядят протухшими** — кеш обновляется не чаще раза в 60 с и только при открытом TUI; удалите `statusline-quota-cache.json` для принудительного обновления.

## Разработка

Требования для разработки: Python ≥ 3.10, `pytest`, `pytest-cov`, `ruff` (ставятся в venv, в рантайм не входят).

```bash
python3 -m venv .venv
.venv/bin/pip install pytest pytest-cov ruff
.venv/bin/python -m pytest --cov   # порог покрытия 90%, фактически ~98%
.venv/bin/ruff check .
```

Структура: `statusline.py` — статус-бар; `scripts/tui_config.py` — установщик ключа в `tui.toml`; `commands/` — слэш-команды плагина; `tests/` — pytest; `.github/workflows/ci.yml` — матрица Python 3.10–3.14 + публикация бейджа покрытия в ветку `badges`.

## Лицензия

[MIT](LICENSE)

# Summit Archive Admin

Read-only веб-админка над архивом встреч Summit AI
(`../summit-archive/database.sqlite` + папки встреч с аудио и `document.md`).

Возможности: список встреч, фильтры по папкам и тегам, полнотекстовый поиск
(FTS5 по транскриптам, темам и названиям), карточка встречи с участниками,
темами, action items, аудиоплеерами и транскриптом, экспорт транскрипта в `.md`.

Read-only маршруты для агентов:

- `GET /agent/v1/meetings?q=...&since=30d&limit=20` — JSON-поиск;
- `GET /agent/v1/meetings/{id}?include_transcript=true` — JSON встречи;
- `GET /agent/v1/context?q=...&since=60d&limit=5&max_chars=20000` — ограниченный Markdown-контекст.

HTTP-маршруты читают сохранённый архив. Команда `meet-archive` по умолчанию синхронизирует архив перед чтением.

База открывается строго в режиме `mode=ro` — админка не может изменить архив.

Страница **/sync** показывает дельту с живой базой Summit (что нового/изменённого
поедет в архив, что удалится при prune) и запускает
`summit/archive_summit_meetings.py` как подпроцесс: режим
(`sync-and-prune`/`sync`/`prune`), `keep-active` (по умолчанию — из последнего
запуска), dry-run. Изменения делает только сам скрипт; перед удалением он
создаёт бэкап живой базы. Кнопка работает только там, где доступна живая база
(локально); на хостинге страница просто покажет «база не найдена».

## Запуск локально

```bash
cd summit-archive-admin
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --port 8377
# → http://127.0.0.1:8377
```

## Конфигурация (переменные окружения)

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `SUMMIT_ARCHIVE_ROOT` | Путь к папке архива | `../summit-archive` |
| `SUMMIT_SOURCE_ROOT` | Путь к папкам/аудио живых встреч Summit | `../summit` |
| `SUMMIT_SOURCE_DB` | Путь к живой базе Summit | Настроенная раскладка `summit/` и `summit-db/` или обнаруженная база в контейнере Summit |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Включают HTTP Basic Auth (обе сразу) | выключено |

## Тесты

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

## Хостинг

Развёрнутый экземпляр работает как **хаб**: несколько Mac присылают в него свои
встречи по HTTP, он сводит их в одну базу и даёт общий поиск. Полное описание —
`../docs/multi-device.md`.

Коротко:

```bash
# на устройстве: сгенерировать схему серверной базы (только DDL, без данных)
./scripts/dump-schema.sh summit-archive/database.sqlite > summit-archive-admin/schema.sql

# на VPS: том под данные, владелец обязан совпасть с UID контейнера
mkdir -p /opt/meet-archive/{data,backups} && chown -R 10001:10001 /opt/meet-archive

# приложение создаётся в Dokploy из репозитория, переменные — по .env.example

# с устройства
export MEET_ARCHIVE_HUB_URL=https://<домен> MEET_ARCHIVE_HUB_TOKEN=<токен>
./summit_archive.sh push --dry-run
./summit_archive.sh push
```

Что важно знать до развёртывания:

- В роли `server` приложение **не поднимется** без `ADMIN_USER`, `ADMIN_PASSWORD`
  и `INGEST_TOKENS`. В архиве личные встречи, и молчаливый режим без пароля на
  публичном домене недопустим.
- Страница `/sync` в серверной роли не регистрируется: живой базы Summit там нет.
- Аудио на сервер не едет — только строки БД и `document.md`. На карточке встречи
  показывается, на каком устройстве осталась запись.
- Базу нельзя обновлять копированием файла поверх работающего сервера: данные
  приходят через ingest, а не через rsync.

## Конфигурация серверной роли

| Переменная | Назначение |
|---|---|
| `MEET_ARCHIVE_ROLE` | `server` включает хаб; по умолчанию `local` |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Basic Auth, обязательны в роли `server` |
| `INGEST_TOKENS` | токены устройств: `device-id:секрет` через запятую |
| `INGEST_MAX_BYTES` | предел размера бандла (по умолчанию 32 МиБ) |

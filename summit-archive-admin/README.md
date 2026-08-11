# Summit Archive Admin

Read-only веб-админка над архивом встреч Summit AI
(`../summit-archive/database.sqlite` + папки встреч с аудио и `document.md`).

Возможности: список встреч, фильтры по папкам и тегам, полнотекстовый поиск
(FTS5 по транскриптам, темам и названиям), карточка встречи с участниками,
темами, action items, аудиоплеерами и транскриптом, экспорт транскрипта в `.md`.

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
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app:app --port 8377
# → http://127.0.0.1:8377
```

## Конфигурация (переменные окружения)

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `SUMMIT_ARCHIVE_ROOT` | Путь к папке архива | `../summit-archive` |
| `SUMMIT_SOURCE_ROOT` | Путь к живой базе Summit (для /sync) | `../summit` |
| `ADMIN_USER` / `ADMIN_PASSWORD` | Включают HTTP Basic Auth (обе сразу) | выключено |

## Тесты

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

## Хостинг

Приложение stateless: достаточно любого хоста с Python (VPS, Fly.io, Railway…).

1. Скопируйте папку `summit-archive/` (база + папки встреч) на сервер
   и укажите путь в `SUMMIT_ARCHIVE_ROOT`.
2. Обязательно задайте `ADMIN_USER`/`ADMIN_PASSWORD` — в архиве личные встречи.
3. Запуск: `uvicorn app:app --host 0.0.0.0 --port 8000` за reverse-proxy с HTTPS.
4. Обновление архива = повторная загрузка свежего `database.sqlite`
   (например, `rsync -av summit-archive/ server:/srv/summit-archive/`
   после очередного запуска `summit_archive.sh`).

WAL-файлы (`-wal`, `-shm`) копировать вместе с базой либо предварительно
схлопнуть: `sqlite3 database.sqlite "PRAGMA wal_checkpoint(TRUNCATE);"`.

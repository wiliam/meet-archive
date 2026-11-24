---
title: Разбор и передача встреч
tags: [meetings, workflow]
---

# Разбор и передача встреч

`meet-archive` хранит записи Summit Notes, читает корпоративные транскрипты из настроенных папок и готовит локальные черновики по шаблону. Утверждённый Markdown попадает в командное хранилище, где его может индексировать Onyx.

## Два Mac и общий архив

На каждом Mac оставьте локальный архив Summit и настройте отдельный токен устройства для одного приватного хаба. Хаб принимает бандлы от обеих машин и сохраняет происхождение записи. Для работы с объединённой базой на хосте используйте `meet-archive --archive-root /path/to/hub-data` и добавляйте `--archive-only` к командам просмотра. Команды `workflow` работают с сохранённым архивом.

```bash
meet-archive sync-and-push --hub https://private-hub.example.org \
  --token-file "$DEVICE_TOKEN_FILE" --device-label "Mac 2" --dry-run
meet-archive sync-and-push --hub https://private-hub.example.org \
  --token-file "$DEVICE_TOKEN_FILE" --device-label "Mac 2"
```

> [!IMPORTANT]
> Приватный хаб хранит транскрипты Summit, включая личные встречи. Для Onyx укажите отдельный командный каталог. Подробная настройка токенов и хаба описана в [синхронизации нескольких устройств](multi-device.md).

## Папки и шаблоны

Настройки читаются из `~/.config/meet-archive/workflow.json`. Для другого файла укажите `MEET_ARCHIVE_WORKFLOW_CONFIG` или `meet-archive --workflow-config FILE workflow …`. Относительные пути отсчитываются от папки с настройками.

```json
{
  "file_sources": {
    "hh": "~/Documents/dev/hh-complatform"
  },
  "team_root": "/path/to/team-storage",
  "rules": [
    {"title_prefix": "Billing sync", "project": "Billing/Redesign", "template": "decisions"}
  ]
}
```

| Ключ | Назначение |
|---|---|
| `file_sources` | Имена и корневые каталоги текстовых источников. Можно подключить несколько каталогов. |
| `team_root` | Командное хранилище, доступное как каталог. Проекты находятся в `projects/<Project>/` и могут содержать подпроекты. Для локального клона репозитория применяется его обычный процесс доставки в командное хранилище. |
| `work_dir` | Каталог черновиков и подтверждённых связей. По умолчанию `.workflow/` внутри личного архива. |
| `templates` | Дополнительные шаблоны. Например, `{"project-status": "templates/project-status.md"}`. |
| `summarizer` | Необязательный массив аргументов запуска. Программа получает промпт с транскриптом на stdin и выдаёт Markdown на stdout. Выберите обработчик, которому можно доверить такие данные. |
| `rules` | Правила подготовки локальных черновиков Summit. Первое совпадение по `title_prefix` и/или `tag` задаёт проект и шаблон. |

Встроенные шаблоны — `summary`, `decisions` и `facts`. В саммари выделены ключевые точки и метрики, решения с основаниями, открытые вопросы и действия. Артефакты можно открыть и отредактировать как обычные текстовые файлы.

## Подготовка

```bash
meet-archive sync
meet-archive workflow inspect SUMMIT_ID
meet-archive workflow classify SUMMIT_ID --project Billing/Redesign
meet-archive workflow prepare --summit SUMMIT_ID --template decisions

meet-archive workflow prepare --file path/to/transcript.md --template summary
meet-archive workflow save DRAFT_ID path/to/edited-summary.md
```

`prepare` возвращает ID и пути к промпту, транскрипту и саммари. Плагин Codex или Claude Code может выполнить промпт и сохранить результат через `save`. При настроенном `summarizer` добавьте `--generate`, чтобы команда сама подготовила саммари.

При настроенном `summarizer` команда `meet-archive sync --prepare` готовит черновики для узнаваемых серий после синхронизации. `meet-archive workflow auto --days 3` применяет правила к уже сохранённым встречам. `workflow classify SUMMIT_ID --private` оставляет встречу личной и исключает её из автоматического разбора.

## Когда корпоративный файл приходит позже

`workflow inspect SUMMIT_ID` показывает возможные совпадения по дате и нормализованному названию. Проверьте совпадение и подтвердите его через `workflow link SUMMIT_ID path/to/corporate-transcript.md`. Для неверного совпадения используйте `workflow link SUMMIT_ID FILE --dismiss`. Исходные записи сохраняются, опубликованное ранее саммари остаётся доступным в первоначальном виде.

## Публикация

```bash
meet-archive workflow publish DRAFT_ID
meet-archive workflow publish DRAFT_ID --confirm TOKEN_FROM_PREVIEW
```

Сначала команда показывает точные пути и выдаёт токен предпросмотра. Проверьте содержание и адрес назначения. Повторите команду с теми же параметрами и `--confirm`. Если черновик изменён, получите новый токен. Файлы с другим содержимым по целевому пути сохраняются.

По умолчанию передаётся Markdown с разбором. Если рабочая встреча была записана только в Summit, добавьте `--summit-only` при классификации и `--include-transcript` при предпросмотре и подтверждении. Для встречи со связанной корпоративной записью в командный каталог можно передать разбор из Summit.

Если корпоративный транскрипт уже лежит в проектной папке командного хранилища, саммари появится рядом. Для файла из `inbox/` укажите `--project Billing/Redesign`. Для передачи разбора нескольким командам повторите предпросмотр и подтверждение для каждого проекта. Готовые решения можно скопировать в Mattermost.

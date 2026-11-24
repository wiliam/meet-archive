#!/bin/bash
set -euo pipefail

# Пути вычисляются от расположения самого скрипта, поэтому репозиторий можно
# клонировать куда угодно. Переопределяются теми же переменными окружения,
# которые читает веб-админка (summit-archive-admin).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="${SUMMIT_SOURCE_ROOT:-$REPO_ROOT/summit}"
ARCHIVE_ROOT="${SUMMIT_ARCHIVE_ROOT:-$REPO_ROOT/summit-archive}"
MODERN_SOURCE_DB="$HOME/Library/Containers/app.summitnotes/Data/Library/Application Support/Summit/Database/database.sqlite"
if [[ -n "${SUMMIT_SOURCE_DB:-}" ]]; then
  SOURCE_DB="$SUMMIT_SOURCE_DB"
elif [[ -f "$REPO_ROOT/summit-db/database.sqlite" ]]; then
  if [[ -f "$SOURCE_ROOT/database.sqlite" ]]; then
    SOURCE_DB="$SOURCE_ROOT/database.sqlite"
  else
    SOURCE_DB="$REPO_ROOT/summit-db/database.sqlite"
  fi
elif [[ -z "${SUMMIT_SOURCE_ROOT:-}${SUMMIT_ARCHIVE_ROOT:-}" && -s "$MODERN_SOURCE_DB" ]]; then
  SOURCE_DB="$MODERN_SOURCE_DB"
else
  SOURCE_DB="$SOURCE_ROOT/database.sqlite"
fi
SCRIPT_PATH="$REPO_ROOT/archive_summit_meetings.py"
PUSH_SCRIPT="$REPO_ROOT/push_to_hub.py"

# --archive-root передаётся явно всегда. Полагаться на его вычисление внутри
# скрипта нельзя: там source-root проходит через Path.resolve(), и если
# summit/ — симлинк на контейнер приложения, архив уехал бы внутрь контейнера.

usage() {
  cat <<'EOF'
Usage:
  summit_archive.sh
  summit_archive.sh sync
  summit_archive.sh dry-run
  summit_archive.sh prune KEEP_ACTIVE
  summit_archive.sh sync-and-prune KEEP_ACTIVE
  summit_archive.sh push [--dry-run ...]
  summit_archive.sh sync-and-push [--dry-run ...]

Examples:
  summit_archive.sh
  summit_archive.sh dry-run
  summit_archive.sh prune 20
  summit_archive.sh sync-and-prune 20
  summit_archive.sh push --dry-run
  summit_archive.sh sync-and-push

Environment:
  SUMMIT_SOURCE_ROOT      папки встреч Summit  (default: <repo>/summit)
  SUMMIT_SOURCE_DB        живая база Summit     (default: база внутри summit/ или summit-db/,
                                                 в старой установке также проверяется контейнер Summit)
  SUMMIT_ARCHIVE_ROOT     папка архива          (default: <repo>/summit-archive)
  MEET_ARCHIVE_HUB_URL    адрес хаба, куда отправлять встречи
  MEET_ARCHIVE_HUB_TOKEN  токен устройства (или push --token-file ПУТЬ)
EOF
}

# Вариант без exec. Нужен там, где после архивации идёт ещё один шаг: exec
# подменяет процесс, и вторая половина составного режима не выполнилась бы.
run_nonexec() {
  python3 "$SCRIPT_PATH" \
    --source-root "$SOURCE_ROOT" \
    --source-db "$SOURCE_DB" \
    --archive-root "$ARCHIVE_ROOT" \
    "$@"
}

run() {
  exec python3 "$SCRIPT_PATH" \
    --source-root "$SOURCE_ROOT" \
    --source-db "$SOURCE_DB" \
    --archive-root "$ARCHIVE_ROOT" \
    "$@"
}

push() {
  python3 "$PUSH_SCRIPT" --archive-root "$ARCHIVE_ROOT" "$@"
}

mode="${1:-sync}"

case "$mode" in
  sync)
    run --mode sync
    ;;
  dry-run)
    run --mode sync --dry-run
    ;;
  prune)
    if [[ $# -lt 2 ]]; then
      usage
      exit 1
    fi
    run --mode prune --keep-active "$2"
    ;;
  sync-and-prune)
    if [[ $# -lt 2 ]]; then
      usage
      exit 1
    fi
    run --mode sync-and-prune --keep-active "$2"
    ;;
  push)
    shift || true
    push "$@"
    ;;
  sync-and-push)
    shift || true
    run_nonexec --mode sync
    push "$@"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac

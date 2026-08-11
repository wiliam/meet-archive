#!/bin/bash
set -euo pipefail

# Пути вычисляются от расположения самого скрипта, поэтому репозиторий можно
# клонировать куда угодно. Переопределяются теми же переменными окружения,
# которые читает веб-админка (summit-archive-admin).
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="${SUMMIT_SOURCE_ROOT:-$REPO_ROOT/summit}"
ARCHIVE_ROOT="${SUMMIT_ARCHIVE_ROOT:-$REPO_ROOT/summit-archive}"
SCRIPT_PATH="$REPO_ROOT/archive_summit_meetings.py"

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

Examples:
  summit_archive.sh
  summit_archive.sh dry-run
  summit_archive.sh prune 20
  summit_archive.sh sync-and-prune 20

Environment:
  SUMMIT_SOURCE_ROOT    живая папка Summit   (default: <repo>/summit)
  SUMMIT_ARCHIVE_ROOT   папка архива         (default: <repo>/summit-archive)
EOF
}

run() {
  exec python3 "$SCRIPT_PATH" \
    --source-root "$SOURCE_ROOT" \
    --archive-root "$ARCHIVE_ROOT" \
    "$@"
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
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac

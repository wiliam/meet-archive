#!/bin/bash
# Первичная настройка meet-archive.
#
# Ничего не удаляет и не изменяет базу Summit: только создаёт папку архива,
# симлинк на живое хранилище и виртуальное окружение для админки.
# Запускать можно повторно — скрипт идемпотентен.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_STORAGE="$HOME/Library/Containers/app.summitnotes/Data/Documents/Summit"
APP_PATH="/Applications/Summit AI Notes.app"

SOURCE_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_ARG="${2:-}"
      [[ -n "$SOURCE_ARG" ]] || { echo "--source требует путь" >&2; exit 1; }
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./setup.sh [--source /path/to/Summit/storage]

Без --source скрипт возьмёт папку <repo>/summit, если она уже есть,
иначе создаст симлинк на стандартное хранилище Summit в контейнере приложения.
EOF
      exit 0
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 1
      ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '    \033[33m!  %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32mок\033[0m %s\n' "$1"; }

# --- 1. Проверки окружения -------------------------------------------------
say "Проверяю окружение"

command -v python3 >/dev/null 2>&1 || {
  echo "python3 не найден. Установите Python 3.10+ и повторите." >&2
  exit 1
}
ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"

if [[ -d "$APP_PATH" ]]; then
  ok "Summit AI Notes установлен"
else
  warn "Summit AI Notes не найден в /Applications."
  warn "Поставьте приложение из Mac App Store, запустите его хотя бы раз,"
  warn "затем запустите ./setup.sh снова."
  exit 1
fi

# --- 2. Живое хранилище ----------------------------------------------------
say "Подключаю живое хранилище Summit"

LINK="$REPO_ROOT/summit"

if [[ -n "$SOURCE_ARG" ]]; then
  SOURCE_ARG="${SOURCE_ARG/#\~/$HOME}"
  [[ -d "$SOURCE_ARG" ]] || { echo "Папка не найдена: $SOURCE_ARG" >&2; exit 1; }
  if [[ -e "$LINK" && ! -L "$LINK" ]]; then
    echo "$LINK уже существует и это не симлинк — уберите её или укажите другой путь." >&2
    exit 1
  fi
  ln -sfn "$SOURCE_ARG" "$LINK"
  ok "summit -> $SOURCE_ARG"
elif [[ -L "$LINK" ]]; then
  ok "summit -> $(readlink "$LINK") (уже настроено)"
elif [[ -d "$LINK" ]]; then
  ok "summit — обычная папка, использую как есть"
elif [[ -d "$DEFAULT_STORAGE" ]]; then
  ln -sfn "$DEFAULT_STORAGE" "$LINK"
  ok "summit -> $DEFAULT_STORAGE"
else
  echo "Не нашёл хранилище Summit." >&2
  echo "Ожидал: $DEFAULT_STORAGE" >&2
  echo "Запустите приложение хотя бы раз, либо укажите путь: ./setup.sh --source <path>" >&2
  exit 1
fi

if [[ -f "$LINK/database.sqlite" ]]; then
  ok "database.sqlite найдена"
else
  warn "В хранилище нет database.sqlite — возможно, вы ещё не записали ни одной встречи."
fi

# --- 3. Папка архива -------------------------------------------------------
say "Готовлю архив"
mkdir -p "$REPO_ROOT/summit-archive"
ok "summit-archive/"

# --- 4. Окружение для админки ----------------------------------------------
say "Ставлю зависимости веб-админки"
VENV="$REPO_ROOT/summit-archive-admin/.venv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_ROOT/summit-archive-admin/requirements.txt"
ok "$( "$VENV/bin/python" -c 'import fastapi; print("fastapi", fastapi.__version__)' )"

# --- 5. Пробный прогон -----------------------------------------------------
say "Пробный прогон архивации (dry-run, ничего не меняет)"
chmod +x "$REPO_ROOT/summit_archive.sh"
if "$REPO_ROOT/summit_archive.sh" dry-run; then
  ok "скрипт отработал"
else
  warn "dry-run завершился с ошибкой — см. вывод выше."
  warn "Частая причина: приложение не создало базу. Запишите одну встречу и повторите."
fi

# --- 6. Что дальше ---------------------------------------------------------
say "Готово"
cat <<EOF
Раскладка:
  $REPO_ROOT/summit           живое хранилище Summit
  $REPO_ROOT/summit-archive   архив

Дальше:
  ./summit_archive.sh sync                 перенести встречи в архив
  ./summit_archive.sh sync-and-prune 12    архивировать и оставить 12 активных

Веб-админка:
  summit-archive-admin/.venv/bin/uvicorn app:app --port 8377 --app-dir summit-archive-admin
  → http://127.0.0.1:8377

Перед любым prune сначала: ./summit_archive.sh dry-run
EOF

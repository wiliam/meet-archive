#!/bin/bash
# Первичная настройка meet-archive.
#
# Ничего не удаляет и не изменяет базу Summit: только создаёт папку архива,
# симлинк на живое хранилище и виртуальное окружение для админки.
# Запускать можно повторно — скрипт идемпотентен.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_STORAGE="${MEET_ARCHIVE_DEFAULT_STORAGE:-$HOME/Library/Containers/app.summitnotes/Data/Documents/Summit}"
DEFAULT_DATABASE="${MEET_ARCHIVE_DEFAULT_DATABASE:-$HOME/Library/Containers/app.summitnotes/Data/Library/Application Support/Summit/Database/database.sqlite}"
PREFERENCES="${MEET_ARCHIVE_PREFERENCES:-$HOME/Library/Containers/app.summitnotes/Data/Library/Preferences/app.summitnotes.plist}"
APP_PATH="${MEET_ARCHIVE_APP_PATH:-/Applications/Summit AI Notes.app}"

SOURCE_ARG=""
SOURCE_DB_ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE_ARG="${2:-}"
      [[ -n "$SOURCE_ARG" ]] || { echo "--source требует путь" >&2; exit 1; }
      shift 2
      ;;
    --source-db)
      SOURCE_DB_ARG="${2:-}"
      [[ -n "$SOURCE_DB_ARG" ]] || { echo "--source-db требует путь" >&2; exit 1; }
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./setup.sh [--source /path/to/meeting/folders] [--source-db /path/to/database.sqlite]

Без --source скрипт возьмёт папку <repo>/summit, если она уже есть,
иначе создаст симлинк на стандартное хранилище Summit в контейнере приложения.
Без --source-db используется <source>/database.sqlite, затем новая стандартная
папка базы Summit. Для раздельной раскладки создаётся симлинк <repo>/summit-db.
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
  warn "Summit AI Notes не найден: $APP_PATH"
  warn "Поставьте приложение из Mac App Store, запустите его хотя бы раз,"
  warn "затем запустите ./setup.sh снова."
  exit 1
fi

# --- 2. Живое хранилище ----------------------------------------------------
say "Подключаю живое хранилище Summit"

LINK="$REPO_ROOT/summit"
PREFERRED_STORAGE=""
if [[ -f "$PREFERENCES" ]]; then
  PREFERRED_STORAGE="$(/usr/libexec/PlistBuddy -c 'Print :audioStorageBookmarkPath' "$PREFERENCES" 2>/dev/null || true)"
fi

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
  CURRENT_SOURCE="$(readlink "$LINK")"
  if [[ "$CURRENT_SOURCE" == "$DEFAULT_STORAGE" && ! -f "$LINK/database.sqlite" && -d "$PREFERRED_STORAGE" && -f "$DEFAULT_DATABASE" ]]; then
    ln -sfn "$PREFERRED_STORAGE" "$LINK"
    ok "summit -> $PREFERRED_STORAGE (обнаружено в настройках Summit)"
  else
    ok "summit -> $CURRENT_SOURCE (уже настроено)"
  fi
elif [[ -d "$LINK" ]]; then
  ok "summit — обычная папка, использую как есть"
elif [[ -d "$PREFERRED_STORAGE" ]]; then
  ln -sfn "$PREFERRED_STORAGE" "$LINK"
  ok "summit -> $PREFERRED_STORAGE (обнаружено в настройках Summit)"
elif [[ -d "$DEFAULT_STORAGE" ]]; then
  ln -sfn "$DEFAULT_STORAGE" "$LINK"
  ok "summit -> $DEFAULT_STORAGE"
else
  echo "Не нашёл хранилище Summit." >&2
  echo "Ожидал: $DEFAULT_STORAGE" >&2
  echo "Запустите приложение хотя бы раз, либо укажите путь: ./setup.sh --source <path>" >&2
  exit 1
fi

# --- 3. Живая база ---------------------------------------------------------
say "Подключаю живую базу Summit"

DB_LINK="$REPO_ROOT/summit-db"

if [[ -n "$SOURCE_DB_ARG" ]]; then
  SOURCE_DB_ARG="${SOURCE_DB_ARG/#\~/$HOME}"
  [[ -f "$SOURCE_DB_ARG" ]] || { echo "База не найдена: $SOURCE_DB_ARG" >&2; exit 1; }
  [[ "$(basename "$SOURCE_DB_ARG")" == "database.sqlite" ]] || {
    echo "--source-db должен указывать на файл database.sqlite" >&2
    exit 1
  }
  if [[ -e "$DB_LINK" && ! -L "$DB_LINK" ]]; then
    echo "$DB_LINK уже существует и это не симлинк — уберите её или укажите другой путь." >&2
    exit 1
  fi
  ln -sfn "$(dirname "$SOURCE_DB_ARG")" "$DB_LINK"
  ok "summit-db -> $(dirname "$SOURCE_DB_ARG")"
elif [[ -f "$LINK/database.sqlite" ]]; then
  if [[ -L "$DB_LINK" ]]; then
    unlink "$DB_LINK"
    ok "удалён устаревший симлинк summit-db (используется база внутри summit/)"
  elif [[ -e "$DB_LINK" ]]; then
    warn "summit-db существует, но игнорируется: база внутри summit/ имеет приоритет."
  fi
  ok "database.sqlite находится внутри summit/ (совместимая раскладка)"
elif [[ -f "$DB_LINK/database.sqlite" ]]; then
  ok "summit-db -> $(readlink "$DB_LINK" 2>/dev/null || echo "$DB_LINK") (уже настроено)"
elif [[ -f "$DEFAULT_DATABASE" ]]; then
  if [[ -e "$DB_LINK" && ! -L "$DB_LINK" ]]; then
    echo "$DB_LINK уже существует и это не симлинк — уберите её или задайте SUMMIT_SOURCE_DB." >&2
    exit 1
  fi
  ln -sfn "$(dirname "$DEFAULT_DATABASE")" "$DB_LINK"
  ok "summit-db -> $(dirname "$DEFAULT_DATABASE")"
else
  warn "database.sqlite не найдена — возможно, вы ещё не записали ни одной встречи."
fi

# --- 4. Папка архива -------------------------------------------------------
say "Готовлю архив"
mkdir -p "$REPO_ROOT/summit-archive"
ok "summit-archive/"

# --- 5. Окружение для админки ----------------------------------------------
say "Ставлю зависимости веб-админки"
VENV="$REPO_ROOT/summit-archive-admin/.venv"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$REPO_ROOT/summit-archive-admin/requirements.txt"
ok "$( "$VENV/bin/python" -c 'import fastapi; print("fastapi", fastapi.__version__)' )"

# --- 6. Пробный прогон -----------------------------------------------------
say "Пробный прогон архивации (dry-run, ничего не меняет)"
chmod +x "$REPO_ROOT/summit_archive.sh"
if "$REPO_ROOT/summit_archive.sh" dry-run; then
  ok "скрипт отработал"
else
  warn "dry-run завершился с ошибкой — см. вывод выше."
  warn "Частая причина: приложение не создало базу. Запишите одну встречу и повторите."
fi

# --- 7. Что дальше ---------------------------------------------------------
say "Готово"
cat <<EOF
Раскладка:
  $REPO_ROOT/summit           живое хранилище Summit
  $REPO_ROOT/summit-db        каталог живой базы (для раздельной раскладки)
  $REPO_ROOT/summit-archive   архив

Дальше:
  ./summit_archive.sh sync                 перенести встречи в архив
  ./summit_archive.sh sync-and-prune 12    архивировать и оставить 12 активных

Веб-админка:
  summit-archive-admin/.venv/bin/uvicorn app:app --port 8377 --app-dir summit-archive-admin
  → http://127.0.0.1:8377

Перед любым prune сначала: ./summit_archive.sh dry-run
EOF

#!/bin/bash
# Создаёт «Meet Archive.app» — лаунчер, открывающий архив двойным кликом.
#
#   ./install-app.sh              поставить приложение в ~/Applications
#   ./install-app.sh --agent      + держать сервер в фоне постоянно (LaunchAgent)
#   ./install-app.sh --uninstall  убрать приложение и агент
#
# Бандл генерируется здесь, а не хранится в git: внутрь зашивается абсолютный
# путь к репозиторию, и локально созданный бандл не получает атрибут карантина,
# поэтому Gatekeeper не блокирует его как неподписанный.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/Applications/Meet Archive.app"
AGENT_LABEL="com.meet-archive.admin"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"

die() { echo "$1" >&2; exit 1; }

WITH_AGENT=0
UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)     WITH_AGENT=1; shift ;;
    --uninstall) UNINSTALL=1;  shift ;;
    -h|--help)   sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
  esac
done

remove_agent() {
  if [[ -f "$AGENT_PLIST" ]]; then
    launchctl bootout "gui/$(id -u)/$AGENT_LABEL" 2>/dev/null || true
    rm -f "$AGENT_PLIST"
    echo "LaunchAgent удалён."
  fi
}

if [[ $UNINSTALL -eq 1 ]]; then
  remove_agent
  rm -rf "$APP_DIR"
  echo "Приложение удалено. Репозиторий не тронут."
  echo "Сервер, если он ещё работает: ./serve.sh stop"
  exit 0
fi

# --- бандл -----------------------------------------------------------------

mkdir -p "$APP_DIR/Contents/MacOS"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Meet Archive</string>
  <key>CFBundleDisplayName</key>       <string>Meet Archive</string>
  <key>CFBundleIdentifier</key>        <string>com.meet-archive.launcher</string>
  <key>CFBundleExecutable</key>        <string>meet-archive</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>NSHighResolutionCapable</key>   <true/>
  <key>LSMinimumSystemVersion</key>    <string>11.0</string>
  <!-- Репозиторий обычно лежит в ~/Documents — папке под защитой TCC.
       Без этого ключа системе нечего показать в диалоге запроса доступа. -->
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Meet Archive открывает ваш локальный архив встреч.</string>
  <key>NSDesktopFolderUsageDescription</key>
  <string>Meet Archive открывает ваш локальный архив встреч.</string>
  <key>NSDownloadsFolderUsageDescription</key>
  <string>Meet Archive открывает ваш локальный архив встреч.</string>
</dict>
</plist>
PLIST

# Launcher компилируется, а не пишется shell-скриптом. Причина в TCC: если
# CFBundleExecutable — скрипт, ядро запускает /bin/bash, и для системы это
# процесс /bin/bash, а не наш бандл. Подпись не к чему применить, доступ к
# ~/Documents отклоняется молча, без диалога с запросом разрешения.
# Собственный бинарь даёт бандлу настоящую идентичность процесса.
SRC="$(mktemp -t meet-archive-launcher).c"
cat > "$SRC" <<EOF
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/stat.h>

static const char *REPO = "$REPO_ROOT";

/* Один бинарь на два режима:
     без аргументов  -> serve.sh app    (foreground сохраняет TCC)
     "run"           -> serve.sh run    (LaunchAgent; процесс держится живым)
   launchd тоже обязан запускать именно бинарь: с shell-скриптом TCC видит
   /bin/bash и закрывает доступ к репозиторию в ~/Documents. */
int main(int argc, char **argv) {
    const char *mode = (argc > 1) ? argv[1] : "app";

    char serve[4096];
    snprintf(serve, sizeof serve, "%s/serve.sh", REPO);

    struct stat st;
    if (stat(serve, &st) != 0) {
        // Доступа нет или репозиторий переехал: и то и другое чинится в диалоге.
        system("osascript -e 'display alert \"Meet Archive\" message "
               "\"Не удалось открыть архив.\n\nЕсли macOS спрашивала про доступ к папке — "
               "разрешите его, или добавьте Meet Archive в Системные настройки → "
               "Конфиденциальность и безопасность → Полный доступ к диску.\n\n"
               "Если репозиторий переехал — переустановите: ./install-app.sh\" "
               "buttons {\"Открыть настройки\", \"Отмена\"} default button 1' "
               "-e 'if button returned of result is \"Открыть настройки\" then "
               "do shell script \"open x-apple.systempreferences:"
               "com.apple.preference.security?Privacy_AllFiles\"'");
        return 1;
    }

    execl(serve, serve, mode, (char *)NULL);
    return 1;
}
EOF

if xcrun clang -O2 -o "$APP_DIR/Contents/MacOS/meet-archive" "$SRC" 2>/dev/null; then
  rm -f "$SRC"
else
  rm -f "$SRC"
  die "Не удалось собрать launcher. Нужны Command Line Tools: xcode-select --install"
fi
chmod +x "$APP_DIR/Contents/MacOS/meet-archive"

# Ad-hoc подпись: TCC запоминает выданный доступ по идентичности бандла.
# У неподписанного приложения она нестабильна, поэтому система отказывает
# молча, вместо того чтобы предложить выдать доступ.
codesign --force --sign - "$APP_DIR" 2>/dev/null \
  && echo "  подписано ad-hoc (нужно, чтобы macOS запомнила выданный доступ)" \
  || echo "  ! ad-hoc подпись не удалась — доступ к папке придётся выдать вручную" >&2

# Сбросить кеш иконок/метаданных, иначе Finder может показывать бандл папкой.
touch "$APP_DIR"

echo "Установлено: $APP_DIR"
echo "  → открыть можно из Spotlight, Launchpad или перетащив в Dock"

# --- LaunchAgent (опционально) ---------------------------------------------

if [[ $WITH_AGENT -eq 1 ]]; then
  remove_agent
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$AGENT_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>            <string>$AGENT_LABEL</string>
  <!-- Через бинарь бандла, а не через serve.sh напрямую: launchd запустил бы
       /bin/bash, которому TCC не даёт доступа к репозиторию в ~/Documents. -->
  <key>ProgramArguments</key>
  <array>
    <string>$APP_DIR/Contents/MacOS/meet-archive</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>WorkingDirectory</key> <string>$REPO_ROOT</string>
  <key>StandardOutPath</key>  <string>$REPO_ROOT/.run/agent.log</string>
  <key>StandardErrorPath</key><string>$REPO_ROOT/.run/agent.log</string>
</dict>
</plist>
EOF
  mkdir -p "$REPO_ROOT/.run"
  # Агент запускает uvicorn на переднем плане (serve.sh run), поэтому свой
  # фоновый экземпляр надо убрать — иначе они подерутся за порт.
  "$REPO_ROOT/serve.sh" stop >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$AGENT_PLIST"
  echo "LaunchAgent установлен: сервер поднимается при входе в систему."
  echo "  снять: ./install-app.sh --uninstall"
  echo
  echo "  Учтите: сервер с личными встречами будет слушать постоянно."
  echo "  Только на 127.0.0.1, но без пароля, если не заданы ADMIN_USER/ADMIN_PASSWORD."
fi

#!/bin/bash
# Запуск веб-админки архива. Единственное место, которое знает, как поднять сервер:
# .app-лаунчер и LaunchAgent вызывают этот скрипт, а не uvicorn напрямую.
#
#   ./serve.sh open      поднять (если нужно) и открыть в браузере   ← основной сценарий
#   ./serve.sh start     поднять в фоне
#   ./serve.sh stop      остановить
#   ./serve.sh status    состояние
#   ./serve.sh logs      следить за логом
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${MEET_ARCHIVE_PORT:-8377}"
# Только loopback: в архиве личные встречи, а Basic Auth включается лишь при
# заданных ADMIN_USER/ADMIN_PASSWORD (summit-archive-admin/app.py).
HOST="127.0.0.1"
URL="http://$HOST:$PORT"

ADMIN_DIR="$REPO_ROOT/summit-archive-admin"
VENV="$ADMIN_DIR/.venv"
RUN_DIR="$REPO_ROOT/.run"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"

die() { echo "$1" >&2; exit 1; }

# --- состояние -------------------------------------------------------------

server_pid() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid; pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  echo "$pid"
}

port_answers() {
  curl -s -o /dev/null --max-time 1 "$URL/" 2>/dev/null
}

wait_until_ready() {
  for _ in $(seq 1 60); do
    port_answers && return 0
    sleep 0.25
  done
  return 1
}

# --- операции --------------------------------------------------------------

ensure_venv() {
  if [[ ! -x "$VENV/bin/uvicorn" ]]; then
    echo "Окружение не готово — запускаю setup.sh…"
    "$REPO_ROOT/setup.sh" || die "setup.sh не отработал; запустите его вручную."
  fi
}

cmd_start() {
  if pid="$(server_pid)"; then
    echo "Уже запущено (pid $pid) на $URL"
    return 0
  fi
  # Порт занят кем-то посторонним — не перетираем чужой процесс.
  if port_answers; then
    die "Порт $PORT уже занят другим процессом. Задайте MEET_ARCHIVE_PORT."
  fi

  ensure_venv
  mkdir -p "$RUN_DIR"

  nohup "$VENV/bin/uvicorn" app:app \
    --host "$HOST" --port "$PORT" --app-dir "$ADMIN_DIR" \
    >> "$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  if wait_until_ready; then
    echo "Запущено (pid $(cat "$PID_FILE")) на $URL"
  else
    echo "Сервер не ответил за 15 секунд. Последние строки лога:" >&2
    tail -20 "$LOG_FILE" >&2
    rm -f "$PID_FILE"
    exit 1
  fi
}

cmd_stop() {
  if pid="$(server_pid)"; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Остановлено."
  else
    rm -f "$PID_FILE"
    echo "Не запущено."
  fi
}

cmd_status() {
  if pid="$(server_pid)"; then
    if port_answers; then
      echo "работает — pid $pid, $URL"
    else
      echo "процесс есть (pid $pid), но порт $PORT не отвечает — см. ./serve.sh logs"
      return 1
    fi
  elif port_answers; then
    # Нет pid-файла, но порт отвечает: сервер поднят LaunchAgent'ом или вручную.
    echo "работает — $URL (внешний процесс, не из pid-файла)"
  else
    echo "не запущено"
    return 1
  fi
}

cmd_open() {
  # Идемпотентно и не зависит от pid-файла: сервер мог быть поднят LaunchAgent'ом.
  # Двойной клик по иконке при живом сервере обязан просто открыть вкладку.
  if ! port_answers; then
    cmd_start
    wait_until_ready || die "Сервер не отвечает — см. ./serve.sh logs"
  fi
  open "$URL"
}

# Foreground-режим для launchd: процесс должен жить, иначе KeepAlive
# будет перезапускать его в цикле.
cmd_run() {
  ensure_venv
  exec "$VENV/bin/uvicorn" app:app \
    --host "$HOST" --port "$PORT" --app-dir "$ADMIN_DIR"
}

cmd_logs() {
  [[ -f "$LOG_FILE" ]] || die "Лога пока нет: $LOG_FILE"
  tail -f "$LOG_FILE"
}

case "${1:-open}" in
  open)   cmd_open   ;;
  start)  cmd_start  ;;
  run)    cmd_run    ;;
  stop)   cmd_stop   ;;
  status) cmd_status ;;
  restart) cmd_stop; cmd_start ;;
  logs)   cmd_logs   ;;
  -h|--help|help)
    sed -n '2,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "Неизвестная команда: $1 (см. ./serve.sh --help)"
    ;;
esac

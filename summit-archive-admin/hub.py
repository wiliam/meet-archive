"""Отправка архива на хаб из локальной админки.

Клиентом остаётся push_to_hub.py, запускаемый подпроцессом — так же, как
sync.py запускает архиватор. Админка не начинает сама ходить в сеть с данными
встреч: она лишь показывает состояние и нажимает ту же кнопку, что и CLI.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import db
import sync as sync_mod

# serve.sh окружение не прокидывает, а у LaunchAgent его почти нет, поэтому
# переменные из ~/.zshrc до сервера не доходят. Файл — единственное место,
# одинаково доступное и ручному запуску, и агенту. Его же наполняет Infisical.
CONFIG_FILE = Path.home() / ".config" / "meet-archive" / "hub.env"

# Замеры до реального хаба: 5-11 с с большим разбросом, основное время —
# установка соединения и TLS. Панель грузится асинхронно, поэтому щедрый запас
# здесь ничего не задерживает: /sync отрисовывается за миллисекунды.
STATE_TIMEOUT = 30.0
PUSH_TIMEOUT = 1800


def script_path() -> Path:
    return Path(__file__).resolve().parent.parent / "push_to_hub.py"


def _read_config_file() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    values: dict[str, str] = {}
    for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def hub_config() -> dict:
    """Адрес хаба и токен устройства. Окружение приоритетнее файла."""
    from_file = _read_config_file()
    url = (
        os.environ.get("MEET_ARCHIVE_HUB_URL")
        or from_file.get("MEET_ARCHIVE_HUB_URL")
        or ""
    ).strip()
    if not url:
        # Тот же файл описывает и окружение сервера, где адрес задан доменом.
        # Выводим URL из него, чтобы одно значение не пришлось держать дважды.
        domain = from_file.get("MEET_ARCHIVE_DOMAIN", "").strip()
        if domain:
            url = domain if "://" in domain else f"https://{domain}"

    token = (os.environ.get("MEET_ARCHIVE_HUB_TOKEN") or "").strip()
    if not token:
        # В файле лежит серверная форма "device-id:секрет[,...]" — берём первую пару.
        pairs = from_file.get("INGEST_TOKENS", "")
        first = pairs.split(",")[0].strip()
        if ":" in first:
            token = first.partition(":")[2].strip()

    return {
        "url": url.rstrip("/"),
        "token": token,
        "configured": bool(url and token),
        "config_file": str(CONFIG_FILE),
    }


def hub_state(config: dict | None = None) -> dict:
    """Сколько встреч уже на хабе. Ошибки не бросаем — страница должна открыться."""
    config = config or hub_config()
    if not config["configured"]:
        return {"error": "Хаб не настроен"}

    request = urllib.request.Request(f"{config['url']}/api/ingest/state")
    request.add_header("Authorization", f"Bearer {config['token']}")
    request.add_header("User-Agent", "meet-archive-admin/1")
    try:
        with urllib.request.urlopen(request, timeout=STATE_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return {"error": "Хаб отверг токен устройства (401)"}
        return {"error": f"Хаб ответил HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"error": f"Хаб недоступен: {exc.reason}"}
    except (ValueError, OSError) as exc:
        return {"error": f"Не разобрать ответ хаба: {exc}"}


def compute_delta(config: dict | None = None) -> dict:
    """Что уедет на хаб: локальный архив против того, что сервер уже знает."""
    config = config or hub_config()
    state = hub_state(config)
    result = {
        "url": config["url"],
        "configured": config["configured"],
        "config_file": config["config_file"],
        "error": state.get("error"),
        "hub_count": state.get("count"),
        "to_push": [],
        "archive_count": None,
    }
    if result["error"]:
        return result

    known = state.get("documents") or {}
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT id, title, created_date, modified_date FROM documents"
            " WHERE deleted_at IS NULL ORDER BY created_date DESC"
        ).fetchall()
    finally:
        conn.close()

    result["archive_count"] = len(rows)
    for row in rows:
        remote = known.get(row["id"])
        if remote is None:
            result["to_push"].append({**dict(row), "status": "новая"})
        elif float(row["modified_date"] or 0) > float(remote.get("modified_date") or 0):
            result["to_push"].append({**dict(row), "status": "изменена"})
    return result


def run_push(dry_run: bool) -> dict:
    """Запустить push_to_hub.py. Форма результата — как у sync.run_script."""
    config = hub_config()
    if not config["configured"]:
        return {"ok": False, "output": f"Хаб не настроен. Ожидается {CONFIG_FILE}"}
    if not script_path().exists():
        return {"ok": False, "output": f"Скрипт не найден: {script_path()}"}

    cmd = [
        sys.executable,
        str(script_path()),
        "--archive-root",
        str(db.archive_root()),
        "--hub",
        config["url"],
    ]
    if dry_run:
        cmd.append("--dry-run")

    # Та же блокировка, что у архиватора: push читает архив в mode=ro без
    # busy_timeout и параллельно с пишущим архиватором получил бы «database is locked».
    if not sync_mod.RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "output": "Другая операция с архивом уже запущена, подождите."}
    try:
        env = {**os.environ, "MEET_ARCHIVE_HUB_TOKEN": config["token"]}
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PUSH_TIMEOUT, env=env
        )
        output = (proc.stdout + ("\n" + proc.stderr if proc.stderr else "")).strip()
        return {
            "ok": proc.returncode == 0,
            "output": output or "(скрипт не вывел ничего)",
            # Токен передаётся окружением и в отображаемую команду не попадает.
            "cmd": " ".join(cmd),
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "Отправка не завершилась за 30 минут — прервана."}
    finally:
        sync_mod.RUN_LOCK.release()

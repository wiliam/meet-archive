"""Генерация markdown-экспорта встречи из данных архива."""

from datetime import datetime, timezone


def fmt_ts(seconds: float | None) -> str:
    s = int(seconds or 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def fmt_date(epoch: float | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone().strftime(
        "%d.%m.%Y %H:%M"
    )


def fmt_duration(seconds: float | None) -> str:
    s = int(seconds or 0)
    if s < 60:
        return f"{s} сек"
    h, rem = divmod(s, 3600)
    m = rem // 60
    return f"{h} ч {m:02d} мин" if h else f"{m} мин"


def build_markdown(meeting, speakers, tags, topics, action_items, blocks, attachments=()) -> str:
    lines: list[str] = [f"# {meeting['title']}", ""]

    lines.append(f"- **Дата:** {fmt_date(meeting['created_date'])}")
    if meeting["recording_duration"]:
        lines.append(f"- **Длительность:** {fmt_duration(meeting['recording_duration'])}")
    if meeting["folder_name"]:
        lines.append(f"- **Папка:** {meeting['folder_name']}")
    if tags:
        lines.append("- **Теги:** " + ", ".join(t["name"] for t in tags))
    if speakers:
        lines.append("- **Участники:** " + ", ".join(s["name"] for s in speakers))
    lines.append("")

    if meeting["notes"]:
        lines += ["## Заметки", "", meeting["notes"], ""]

    if topics:
        lines.append("## Темы")
        lines.append("")
        lines += [f"- {t['content']}" for t in topics]
        lines.append("")

    if action_items:
        lines.append("## Action items")
        lines.append("")
        for a in action_items:
            box = "x" if a["completed"] else " "
            suffix = f" — {a['assignee']}" if a["assignee"] else ""
            lines.append(f"- [{box}] {a['content']}{suffix}")
        lines.append("")

    if attachments:
        lines.append("## Скриншоты и вложения")
        lines.append("")
        for a in attachments:
            ts = (
                f"[{fmt_ts(a['recording_timestamp'])}] "
                if a["recording_timestamp"] is not None
                else ""
            )
            lines.append(f"- {ts}{a['file_name']}")
        lines.append("")

    if blocks:
        lines.append("## Транскрипт")
        lines.append("")
        for b in blocks:
            lines.append(f"**[{fmt_ts(b['start_time'])}] {b['speaker']}:**")
            lines.append(" ".join(b["texts"]))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_filename(meeting) -> str:
    date = datetime.fromtimestamp(
        meeting["created_date"] or 0, tz=timezone.utc
    ).strftime("%Y-%m-%d")
    safe = "".join(
        c if c.isalnum() or c in " -_" else "_" for c in (meeting["title"] or "meeting")
    ).strip()
    return f"{date} {safe}.md"

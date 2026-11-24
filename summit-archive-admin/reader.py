"""Read-only interface к архиву для CLI, HTTP и будущих agent adapters."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

import db


@dataclass(frozen=True)
class MeetingQuery:
    text: str | None = None
    since: float | None = None
    limit: int = 20
    folder: str | None = None
    tag: str | None = None


def parse_since(value: str | None, *, now: float | None = None) -> float | None:
    """Разбирает `30d`, `2w`, `12h` или ISO date/datetime в Unix timestamp."""
    if not value:
        return None
    value = value.strip()
    relative = re.fullmatch(r"(\d+)([hdw])", value, re.IGNORECASE)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2).lower()
        seconds = amount * {"h": 3600, "d": 86400, "w": 604800}[unit]
        return float(now if now is not None else time.time()) - seconds

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Некорректный --since {value!r}; используйте 30d, 2w или YYYY-MM-DD"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def _iso_date(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(float(timestamp)).astimezone().isoformat(timespec="seconds")


def _clean_snippet(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("⟦", "**").replace("⟧", "**")


class MeetingReader:
    """Глубокий read-only модуль: search, get и context через одно соединение."""

    def __init__(self, conn):
        self.conn = conn

    def search(self, query: MeetingQuery) -> list[dict]:
        hits = db.search_documents(self.conn, query.text) if query.text else None
        rows = db.list_meetings(
            self.conn, ids=list(hits) if hits is not None else None
        )
        folder = query.folder.casefold() if query.folder else None
        tag = query.tag.casefold() if query.tag else None
        result: list[dict] = []

        for row in rows:
            created = float(row["created_date"] or 0)
            tags = [
                name.strip()
                for name in (row["tag_names"] or "").split(",")
                if name.strip()
            ]
            if query.since is not None and created < query.since:
                continue
            if folder and (row["folder_name"] or "").casefold() != folder:
                continue
            if tag and tag not in {name.casefold() for name in tags}:
                continue

            hit = hits.get(row["id"], {}) if hits is not None else {}
            result.append(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "created_at": _iso_date(created),
                    "created_timestamp": created,
                    "duration_seconds": float(row["recording_duration"] or 0),
                    "folder": row["folder_name"],
                    "tags": tags,
                    "speaker_count": int(row["speaker_count"] or 0),
                    "word_count": int(row["word_count"] or 0),
                    "matched_in": sorted(hit.get("where", [])),
                    "snippet": _clean_snippet(hit.get("snippet")),
                }
            )
            if len(result) >= max(1, min(query.limit, 100)):
                break
        return result

    def get(self, document_id: str, *, include_transcript: bool = False) -> dict | None:
        meeting = db.get_meeting(self.conn, document_id)
        if meeting is None:
            return None
        transcript = []
        if include_transcript:
            transcript = [
                {
                    "speaker": block["speaker"],
                    "source": block["source"],
                    "start_time": block["start_time"],
                    "end_time": block["end_time"],
                    "text": " ".join(block["texts"]),
                }
                for block in db.group_transcript(
                    db.meeting_transcript(self.conn, document_id)
                )
            ]
        return {
            "id": meeting["id"],
            "title": meeting["title"],
            "created_at": _iso_date(meeting["created_date"]),
            "modified_at": _iso_date(meeting["modified_date"]),
            "duration_seconds": float(meeting["recording_duration"] or 0),
            "folder": meeting["folder_name"],
            "notes": meeting["notes"],
            "participants": [
                dict(row) for row in db.meeting_speakers(self.conn, document_id)
            ],
            "tags": [row["name"] for row in db.meeting_tags(self.conn, document_id)],
            "topics": [
                row["content"] for row in db.meeting_topics(self.conn, document_id)
            ],
            "action_items": [
                dict(row) for row in db.meeting_action_items(self.conn, document_id)
            ],
            "attachments": [
                dict(row) for row in db.meeting_attachments(self.conn, document_id)
            ],
            "transcript": transcript,
        }

    def context(
        self,
        query: MeetingQuery,
        *,
        include_transcript: bool = False,
        max_chars: int = 20_000,
    ) -> str:
        if max_chars < 1:
            raise ValueError("--max-chars должен быть положительным")
        matches = self.search(query)
        markdown = "# Контекст встреч\n"
        if not matches:
            return (markdown + "\nВстречи не найдены.\n")[:max_chars]

        for match in matches:
            remaining = max_chars - len(markdown) - 2
            if remaining <= 0:
                break
            detail = self.get(match["id"], include_transcript=include_transcript)
            if detail is None:
                continue
            section = meeting_markdown(detail, snippet=match.get("snippet"))
            if len(section) > remaining:
                marker = "\n\n_[обрезано по лимиту]_"
                preview_length = remaining - len(marker)
                source_header = f"## {detail['title']}\n\n- ID: `{detail['id']}`"
                if preview_length >= len(source_header):
                    markdown += "\n" + section[:preview_length].rstrip() + marker + "\n"
                break
            markdown += "\n" + section + "\n"
        return markdown[:max_chars]


def meeting_markdown(meeting: dict, *, snippet: str | None = None) -> str:
    """Стабильный Markdown для агента: summary по умолчанию, transcript опционален."""
    lines = [f"## {meeting['title']}", "", f"- ID: `{meeting['id']}`"]
    if meeting.get("created_at"):
        lines.append(f"- Дата: {meeting['created_at']}")
    if meeting.get("folder"):
        lines.append(f"- Папка: {meeting['folder']}")
    if meeting.get("tags"):
        lines.append("- Теги: " + ", ".join(meeting["tags"]))
    if meeting.get("participants"):
        lines.append(
            "- Участники: "
            + ", ".join(person["name"] for person in meeting["participants"])
        )
    if snippet:
        lines += ["", "### Совпадение", "", snippet]
    if meeting.get("notes"):
        lines += ["", "### Саммари", "", meeting["notes"]]
    if meeting.get("topics"):
        lines += ["", "### Темы", ""] + [f"- {item}" for item in meeting["topics"]]
    if meeting.get("action_items"):
        lines += ["", "### Action items", ""]
        for item in meeting["action_items"]:
            box = "x" if item["completed"] else " "
            assignee = f" — {item['assignee']}" if item.get("assignee") else ""
            lines.append(f"- [{box}] {item['content']}{assignee}")
    if meeting.get("transcript"):
        lines += ["", "### Транскрипт", "", transcript_markdown(meeting["transcript"])]
    return "\n".join(lines).rstrip()


def transcript_markdown(blocks: list[dict]) -> str:
    return "\n\n".join(
        f"**[{_format_ts(block['start_time'])}] {block['speaker']}:**\n{block['text']}"
        for block in blocks
    )


def _format_ts(seconds: float | None) -> str:
    value = int(seconds or 0)
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"

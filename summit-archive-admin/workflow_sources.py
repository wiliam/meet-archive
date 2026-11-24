"""Read Summit and text-file meeting sources without changing their records."""

from __future__ import annotations

import os
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import db
from reader import MeetingReader, transcript_markdown


FILE_DATE = re.compile(r"^(?:\[(\d{4}-\d\d-\d\d)\]|(\d{4}-\d\d-\d\d))")
TRANSCRIPT = re.compile(r"(?:[._ -]transcript$|^transcript[._ -]|[.]transcript[.])", re.I)
TEXT_EXTENSIONS = {".md", ".txt", ".markdown"}


def project_parts(project: str) -> tuple[str, ...]:
    parts = tuple(project.split("/"))
    if any(not part or part.startswith(".") or "\\" in part or "\x00" in part for part in parts):
        raise ValueError(f"Invalid project path: {project!r}")
    return parts


def project_from_path(path: Path) -> str | None:
    parts = path.parts
    if parts[:1] != ("projects",) or "meetings" not in parts[1:-1]:
        return None
    project = parts[1:parts.index("meetings")]
    if project[-1:] == ("resources",):
        project = project[:-1]
    return "/".join(project) or None


def title_key(title: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", title.casefold()))


@dataclass(frozen=True)
class Recording:
    source: str
    key: str
    title: str
    date: str | None
    transcript: str
    project: str | None = None
    relative_path: str | None = None


class Sources:
    def __init__(self, files: dict[str, Path]):
        self.files = files

    def summit(self, document_id: str, *, read: bool = True) -> Recording:
        with closing(db.connect()) as conn:
            conn.execute("BEGIN")
            meeting = MeetingReader(conn).get(document_id, include_transcript=read)
        if meeting is None:
            raise ValueError(f"Summit meeting not found: {document_id}")
        return Recording(
            "summit", document_id, meeting["title"],
            (meeting["created_at"] or "")[:10] or None, transcript_markdown(meeting["transcript"]),
        )

    def file(self, path: Path, *, read: bool = True) -> Recording:
        path = path.expanduser().resolve(strict=True)
        # Prefer the closest configured root when directories are nested.
        for name, root in sorted(self.files.items(), key=lambda item: len(item[1].parts), reverse=True):
            if path.is_relative_to(root):
                relative = path.relative_to(root)
                break
        else:
            raise ValueError(f"File is outside the configured source directories: {path}")
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError(f"Expected a text transcript file: {path}")

        match = FILE_DATE.match(path.stem)
        day = (match[1] or match[2]) if match else None
        title = path.stem[match.end():] if match else path.stem
        title = TRANSCRIPT.sub(" ", title.strip("-_. ")).strip("-_. ") or path.stem
        if day:
            try:
                date.fromisoformat(day)
            except ValueError:
                day = None

        return Recording(
            name, f"{name}:{relative.as_posix()}", title, day,
            path.read_text(encoding="utf-8-sig") if read else "",
            project_from_path(relative), relative.as_posix(),
        )

    def candidates(self, meeting: Recording) -> list[dict]:
        if not meeting.date:
            return []
        matches = {}
        for root in self.files.values():
            for directory, subdirs, filenames in os.walk(root):
                subdirs[:] = [name for name in subdirs if not name.startswith(".")]
                for name in filenames:
                    if not name.startswith((meeting.date, f"[{meeting.date}]")):
                        continue
                    path = Path(directory) / name
                    if path.is_symlink() or path.suffix.lower() not in TEXT_EXTENSIONS or not TRANSCRIPT.search(path.stem):
                        continue
                    candidate = self.file(path, read=False)
                    if candidate.date == meeting.date and title_key(candidate.title) == title_key(meeting.title):
                        matches[candidate.key] = {
                            "source": candidate.source, "key": candidate.key,
                            "file": str(path), "project": candidate.project,
                        }
        return [matches[key] for key in sorted(matches)]

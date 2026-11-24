#!/usr/bin/env python3
"""Read and manage the meeting archive."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ADMIN_DIR = REPO_ROOT / "summit-archive-admin"
sys.path.insert(0, str(ADMIN_DIR))

import db  # noqa: E402
import sync as sync_mod  # noqa: E402
from meeting_workflow import Workflow  # noqa: E402
from reader import MeetingQuery, MeetingReader, meeting_markdown, parse_since  # noqa: E402

READ_COMMANDS = {"recent", "search", "show", "context"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="meet-archive",
        description="Read and manage the meeting archive. Read commands refresh it first.",
    )
    parser.add_argument("--archive-root", help="archive directory override")
    parser.add_argument("--source-root", help="live meeting-files directory override")
    parser.add_argument("--source-db", help="sync source database override")
    parser.add_argument("--workflow-config", type=Path, help="workflow JSON configuration file")
    sub = parser.add_subparsers(dest="command", required=True)

    recent = sub.add_parser("recent", help="list recent archived meetings")
    recent.add_argument("--days", type=int, default=14)
    _query_options(recent)
    _format_option(recent, default="json")

    search = sub.add_parser("search", help="search titles, notes, topics and transcripts")
    search.add_argument("query")
    _query_options(search)
    _format_option(search, default="json")

    show = sub.add_parser("show", help="show one meeting summary")
    meeting = show.add_mutually_exclusive_group(required=True)
    meeting.add_argument("document_id", nargs="?")
    meeting.add_argument("--latest", action="store_true", help="show the most recent meeting")
    show.add_argument("--include-transcript", action="store_true")
    _format_option(show, default="markdown")

    context = sub.add_parser("context", help="build bounded Markdown context for an agent")
    context.add_argument("query", nargs="?", help="text query (optional with filters)")
    _query_options(context)
    context.add_argument("--include-transcript", action="store_true")
    context.add_argument("--max-chars", type=int, default=20_000)

    for command in (recent, search, show, context):
        command.add_argument(
            "--archive-only", action="store_true", help="read the saved archive without refreshing it"
        )

    status = sub.add_parser("status", help="show live/archive sync status")
    status.add_argument("--keep-active", type=int, default=10)
    _format_option(status, default="json")

    sync = sub.add_parser("sync", help="copy changed meetings into the archive")
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--prepare", action="store_true", help="prepare drafts for configured Summit series after sync")

    for name, help_text in (
        ("prune", "remove old live meetings already present in the archive"),
        ("sync-and-prune", "sync, then remove old live meetings"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--keep-active", type=int, required=True)
        command.add_argument(
            "--apply",
            action="store_true",
            help="perform deletion; without this flag the command is always a dry-run",
        )

    push = sub.add_parser("push", help="push archived meetings to the configured hub")
    sync_push = sub.add_parser("sync-and-push", help="sync locally, then push to the hub")
    for command in (push, sync_push):
        command.add_argument("--dry-run", action="store_true")
        command.add_argument("--hub", help="private archive hub URL")
        command.add_argument("--token-file", help="read this device's ingest token from a file")
        command.add_argument("--device-label", help="label meetings from this machine on the hub")
    _workflow_options(sub.add_parser("workflow", help="prepare and review team meeting artifacts"))
    return parser


def _workflow_options(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="workflow_command", required=True)
    inspect = commands.add_parser("inspect", help="show a Summit meeting and possible corporate matches")
    inspect.add_argument("summit_id")

    classify = commands.add_parser("classify", help="assign a Summit meeting to a project or mark it private")
    classify.add_argument("summit_id")
    visibility = classify.add_mutually_exclusive_group(required=True)
    visibility.add_argument("--project")
    visibility.add_argument("--private", action="store_true")
    classify.add_argument("--summit-only", action="store_true", help="confirm there is no corporate recording")

    link = commands.add_parser("link", help="confirm that a corporate file and a Summit ID represent the same meeting")
    link.add_argument("summit_id")
    link.add_argument("corporate_file", type=Path)
    link.add_argument("--dismiss", action="store_true", help="dismiss a potential match or remove a confirmed link")

    prepare = commands.add_parser("prepare", help="build a private summary prompt or run the configured summarizer")
    source = prepare.add_mutually_exclusive_group(required=True)
    source.add_argument("--summit", metavar="ID")
    source.add_argument("--file", "--corporate", dest="corporate", type=Path, metavar="TRANSCRIPT")
    prepare.add_argument("--template", default="summary")
    prepare.add_argument("--generate", action="store_true")

    save = commands.add_parser("save", help="attach a Markdown summary to a draft")
    save.add_argument("draft_id")
    save.add_argument("summary_file", type=Path)

    auto = commands.add_parser("auto", help="prepare private drafts for configured Summit series")
    auto.add_argument("--days", type=int, default=3)

    publish = commands.add_parser("publish", help="preview or confirm a team export")
    publish.add_argument("draft_id")
    publish.add_argument("--project", help="destination project or nested project path")
    publish.add_argument("--include-transcript", action="store_true")
    publish.add_argument("--confirm", metavar="TOKEN", help="apply the exact export preview")


def _workflow_result(result) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _auto_workflow(workflow: Workflow, days: int = 3) -> int:
    drafts = workflow.auto(days)
    _workflow_result(drafts)
    return int(any("error" in draft for draft in drafts))


def _run_workflow(args) -> int:
    workflow = Workflow(args.workflow_config)
    if args.workflow_command == "inspect":
        _workflow_result(workflow.inspect(args.summit_id))
    elif args.workflow_command == "classify":
        if args.private and args.summit_only:
            raise ValueError("--summit-only requires a work project")
        workflow.classify(args.summit_id, args.project, args.summit_only)
        _workflow_result({"summit_id": args.summit_id, "project": args.project, "private": args.private})
    elif args.workflow_command == "link":
        _workflow_result(workflow.link(args.summit_id, args.corporate_file, dismiss=args.dismiss))
    elif args.workflow_command == "prepare":
        meeting = workflow.sources.summit(args.summit) if args.summit is not None else workflow.sources.file(args.corporate)
        _workflow_result(workflow.prepare(meeting, args.template, generate=args.generate))
    elif args.workflow_command == "save":
        _workflow_result(workflow.save(args.draft_id, args.summary_file))
    elif args.workflow_command == "auto":
        return _auto_workflow(workflow, args.days)
    elif args.workflow_command == "publish":
        _workflow_result(workflow.publish(args.draft_id, args.project, args.include_transcript, args.confirm))
    return 0


def _query_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--since", help="30d, 2w, 12h, or YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--folder", help="exact folder name")
    parser.add_argument("--tag", help="exact tag name")


def _format_option(parser: argparse.ArgumentParser, *, default: str) -> None:
    parser.add_argument(
        "--format", choices=("json", "markdown"), default=default
    )


def _apply_path_overrides(args) -> None:
    for attr, env_name in (
        ("archive_root", "SUMMIT_ARCHIVE_ROOT"),
        ("source_root", "SUMMIT_SOURCE_ROOT"),
        ("source_db", "SUMMIT_SOURCE_DB"),
    ):
        value = getattr(args, attr, None)
        if value:
            os.environ[env_name] = str(Path(value).expanduser())


def _meeting_query(args, text: str | None = None, since: float | None = None) -> MeetingQuery:
    return MeetingQuery(
        text=text,
        since=since if since is not None else parse_since(getattr(args, "since", None)),
        limit=args.limit,
        folder=args.folder,
        tag=args.tag,
    )


def _print_search(rows: list[dict], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("Встречи не найдены.")
        return
    for row in rows:
        print(f"- [{row['created_at']}] **{row['title']}** (`{row['id']}`)")
        if row.get("folder"):
            print(f"  - folder: {row['folder']}")
        if row.get("tags"):
            print(f"  - tags: {', '.join(row['tags'])}")
        if row.get("snippet"):
            print(f"  - match: {row['snippet']}")


def _run_sync(mode: str, keep_active: int | None, dry_run: bool) -> int:
    result = sync_mod.run_script(mode, keep_active, dry_run)
    print(result["output"])
    return 0 if result["ok"] else 1


def _refresh_archive() -> bool:
    result = sync_mod.run_script("sync", None, dry_run=False)
    if not result["ok"]:
        print(
            "Не удалось обновить архив встреч. Повторите запрос или укажите "
            "--archive-only, чтобы прочитать сохранённые данные.",
            file=sys.stderr,
        )
    return result["ok"]


def _push_to_hub(args) -> int:
    cmd = [str(REPO_ROOT / "summit_archive.sh"), "push"]
    if args.dry_run:
        cmd.append("--dry-run")
    for option in ("hub", "token_file", "device_label"):
        if value := getattr(args, option):
            cmd.extend([f"--{option.replace('_', '-')}", value])
    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_path_overrides(args)

    try:
        if args.command in READ_COMMANDS:
            if args.command == "recent":
                query = _meeting_query(args, since=parse_since(args.since or f"{args.days}d"))
            elif args.command in {"search", "context"}:
                query = _meeting_query(args, text=args.query)
            if args.command == "context" and args.max_chars < 1:
                parser.error("--max-chars должен быть положительным")
            if not args.archive_only and not _refresh_archive():
                return 1

            conn = db.connect()
            try:
                conn.execute("BEGIN")
                reader = MeetingReader(conn)
                if args.command in {"recent", "search"}:
                    _print_search(reader.search(query), args.format)
                elif args.command == "show":
                    document_id = args.document_id
                    if args.latest:
                        rows = reader.search(MeetingQuery(limit=1))
                        document_id = rows[0]["id"] if rows else None
                    meeting = (
                        reader.get(document_id, include_transcript=args.include_transcript)
                        if document_id else None
                    )
                    if meeting is None:
                        print("Встреча не найдена", file=sys.stderr)
                        return 1
                    if args.format == "json":
                        print(json.dumps(meeting, ensure_ascii=False, indent=2))
                    else:
                        print(meeting_markdown(meeting))
                else:
                    print(
                        reader.context(
                            query,
                            include_transcript=args.include_transcript,
                            max_chars=args.max_chars,
                        ),
                        end="",
                    )
            finally:
                conn.close()
            return 0

        if args.command == "status":
            delta = sync_mod.compute_delta(args.keep_active)
            if args.format == "json":
                print(json.dumps(delta, ensure_ascii=False, indent=2))
            else:
                print(f"- live: {delta.get('active_count', '?')}")
                print(f"- archive: {delta.get('archive_count', '?')}")
                print(f"- to_sync: {len(delta.get('to_sync', []))}")
                if delta.get("error"):
                    print(f"- error: {delta['error']}")
            return 1 if delta.get("error") else 0
        if args.command == "sync":
            result = _run_sync("sync", None, args.dry_run)
            if not result and args.prepare and not args.dry_run:
                return _auto_workflow(Workflow(args.workflow_config))
            return result
        if args.command == "workflow":
            return _run_workflow(args)
        if args.command in {"prune", "sync-and-prune"}:
            if not args.apply:
                print(
                    "Без --apply выполняется только dry-run; данные не удаляются.",
                    file=sys.stderr,
                )
            return _run_sync(args.command, args.keep_active, not args.apply)
        if args.command == "push":
            return _push_to_hub(args)
        if args.command == "sync-and-push":
            result = _run_sync("sync", None, args.dry_run)
            if result:
                return result
            return _push_to_hub(args)
    except (OSError, sqlite3.OperationalError, ValueError) as exc:
        print(f"meet-archive: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

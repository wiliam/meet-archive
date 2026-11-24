# Summit archive workflow

## Goal
Keep a full archive of meeting data up to date, then remove only the excess meetings from active
Summit storage without breaking the app UI or leaving orphan rows.

## Layout
Everything is resolved relative to the repository root (`<repo>` below):

- Meeting files: `<repo>/summit` — one live directory per meeting (a real folder or symlink)
- Active DB: `<repo>/summit/database.sqlite` in the legacy layout, or
  `<repo>/summit-db/database.sqlite` in the newer split layout
- Archive storage: `<repo>/summit-archive`
- Archive DB: `<repo>/summit-archive/database.sqlite`
- Archive index: `<repo>/summit-archive/archive-index.md`

The paths can be overridden with `SUMMIT_SOURCE_ROOT`, `SUMMIT_SOURCE_DB`, and
`SUMMIT_ARCHIVE_ROOT` — the same environment variables the web admin reads. When `summit-db/` is configured, the tool prefers `<source-root>/database.sqlite` if present and uses `summit-db/database.sqlite` otherwise. A default installation without `summit-db/` can also discover the active database directly in the Summit app container. Set `SUMMIT_SOURCE_DB` to select a specific database.

## What gets archived
For each meeting, the script copies:
- `documents`
- related rows from:
  - `action_items`
  - `attachments`
  - `bookmarks`
  - `document_analytics`
  - `document_speakers`
  - `document_tags`
  - `document_versions`
  - `topics`
  - `transcription_chunks`
  - `transcription_segments`
  - `split_carried_tags`
  - `trashed_calendar_links`
  - `chat_sessions`
  - `chat_messages`
  - `calendar_events` linked to the document
- required lookup rows from:
  - `folders`
  - `summarization_templates`
  - `tags`
  - `speakers`

For files on disk:
- `sync` keeps the archive DB current and only fills a missing archive folder when needed.
- `prune` moves the full meeting folder into the archive:
  - `document.md`
  - `microphone.m4a`
  - `system.m4a`

## Safety model
- The archive has its own SQLite DB plus copied meeting folders on disk.
- The script tracks the last successful sync in `<repo>/summit-archive/archive-state.json`.
- In `sync` mode it upserts every active meeting that is either:
  - missing from the archive, or
  - updated after the last successful sync watermark
- In `prune` mode it removes only meetings that are already confirmed present in the archive DB.
- In `prune` mode the meeting folder is moved into archive storage instead of being copied and
  then deleted.
- In `sync-and-prune` mode it syncs first, then recalculates overflow and prunes safely.
- Before source-side deletion, the script creates a fresh backup next to `database.sqlite`.
- This avoids the orphan-row problem that can otherwise leave the app UI empty after an update
  (see `summit-app-notes.md`).

## Commands

Prefer the unified `meet-archive` CLI. The older `summit_archive.sh` wrapper remains compatible.

Dry run first. Always.

```bash
meet-archive sync --dry-run
```

Only sync changed meetings into the archive, without deleting anything from the active store:

```bash
meet-archive sync
```

Sync, then prune until only 12 active meetings remain:

```bash
meet-archive sync-and-prune --keep-active 12          # preview only
meet-archive sync-and-prune --keep-active 12 --apply  # apply after review
```

Only prune, assuming the archive is already current:

```bash
meet-archive prune --keep-active 12          # preview only
meet-archive prune --keep-active 12 --apply
```

Project agents can read meetings directly with `meet-archive`. The CLI refreshes the local archive before returning results. Add `--archive-only` to read the saved snapshot. A refresh failure produces an error.

```bash
meet-archive search "billing" --since 30d --format json
meet-archive context "billing" --since 60d --limit 5 --max-chars 20000
meet-archive show --latest --include-transcript
meet-archive show <meeting-id> --format markdown
```

Machine-readable HTTP routes serve the saved archive snapshot at `/agent/v1/meetings`, `/agent/v1/meetings/{id}`, and `/agent/v1/context`.

### Calling the script directly

Useful for options the wrapper does not expose, such as syncing one explicit meeting:

```bash
python3 archive_summit_meetings.py \
  --source-root  "$PWD/summit" \
  --source-db    "$PWD/summit-db/database.sqlite" \
  --archive-root "$PWD/summit-archive" \
  --mode sync \
  --document-id 00000000-0000-0000-0000-000000000000
```

Full option list: `python3 archive_summit_meetings.py --help`
(`--source-db`, `--mode`, `--keep-active`, `--document-id`, `--dry-run`, `--skip-backup`).

> **Always pass `--archive-root` explicitly.** Left unset, the script derives it from
> `--source-root` *after* `Path.resolve()`. If `summit/` is a symlink into the app container,
> the archive would silently be created inside that container instead of in the repository.

## Notes
- Sync regenerates `archive-index.md` when meetings change or the index needs to be created.
- `archive-state.json` is updated after successful sync/prune runs.
- Both files are generated output and are gitignored.

# Summit archive workflow

## Goal
Keep a full archive of meeting data up to date, then remove only the excess meetings from active
Summit storage without breaking the app UI or leaving orphan rows.

## Layout
Everything is resolved relative to the repository root (`<repo>` below):

- Active storage: `<repo>/summit` — the live Summit storage directory (a real folder or a symlink
  to the app container)
- Archive storage: `<repo>/summit-archive`
- Archive DB: `<repo>/summit-archive/database.sqlite`
- Archive index: `<repo>/summit-archive/archive-index.md`

Both paths can be overridden with `SUMMIT_SOURCE_ROOT` / `SUMMIT_ARCHIVE_ROOT` — the same
environment variables the web admin reads.

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

Prefer the `summit_archive.sh` wrapper — it fills in both roots for you.

Dry run first. Always.

```bash
./summit_archive.sh dry-run
```

Only sync changed meetings into the archive, without deleting anything from the active store:

```bash
./summit_archive.sh sync
```

Sync, then prune until only 12 active meetings remain:

```bash
./summit_archive.sh sync-and-prune 12
```

Only prune, assuming the archive is already current:

```bash
./summit_archive.sh prune 12
```

### Calling the script directly

Useful for options the wrapper does not expose, such as syncing one explicit meeting:

```bash
python3 archive_summit_meetings.py \
  --source-root  "$PWD/summit" \
  --archive-root "$PWD/summit-archive" \
  --mode sync \
  --document-id 00000000-0000-0000-0000-000000000000
```

Full option list: `python3 archive_summit_meetings.py --help`
(`--mode`, `--keep-active`, `--document-id`, `--dry-run`, `--skip-backup`).

> **Always pass `--archive-root` explicitly.** Left unset, the script derives it from
> `--source-root` *after* `Path.resolve()`. If `summit/` is a symlink into the app container,
> the archive would silently be created inside that container instead of in the repository.

## Notes
- `archive-index.md` is regenerated on every run so it stays consistent.
- `archive-state.json` is updated after successful sync/prune runs.
- Both files are generated output and are gitignored.

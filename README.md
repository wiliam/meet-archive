# meet-archive

Archive your [Summit AI Notes](https://summitnotes.app) meetings into a separate, permanent store —
and browse them in a local web UI.

Summit keeps meetings in a local SQLite database plus one folder per meeting. This kit copies them
out into an archive you own, keeping transcripts, topics, action items, speakers and audio intact,
and optionally trims the live store so the app stays fast. Nothing leaves your machine.

## What you get

- **Archiver** — an incremental sync into a separate archive database. Knows the full Summit
  schema, so nothing is lost and no orphan rows are left behind. Backs up before deleting.
- **Web admin** — read-only FastAPI UI over the archive: full-text search across every transcript
  (SQLite FTS5), filters by folder and tag, meeting cards with speakers, topics, action items,
  audio players, and markdown export.
- **Setup script** — links your live storage, creates the archive, installs dependencies,
  finishes with a dry run.

## Requirements

macOS with Summit AI Notes installed, and Python 3.10+.

## Quick start

```bash
git clone git@github.com:wiliam/meet-archive.git
cd meet-archive
./setup.sh
```

Then archive your meetings:

```bash
./summit_archive.sh dry-run      # always look first
./summit_archive.sh sync         # copy everything into the archive
```

And browse them:

```bash
summit-archive-admin/.venv/bin/uvicorn app:app --port 8377 --app-dir summit-archive-admin
# → http://127.0.0.1:8377
```

To also trim the live store to the 12 most recent meetings:

```bash
./summit_archive.sh sync-and-prune 12
```

Meetings are deleted from Summit only after they are confirmed present in the archive, and a
backup of the live database is taken first.

## Layout

The clone is the working root. Your data lives inside it and is gitignored:

```
meet-archive/
├── summit/           → live Summit storage (symlink created by setup.sh)
└── summit-archive/   → your archive: database + meeting folders
```

## Docs

- [`docs/archive-workflow.md`](docs/archive-workflow.md) — what gets archived, all CLI modes,
  the safety model
- [`docs/summit-app-notes.md`](docs/summit-app-notes.md) — app internals, DB schema,
  `document.md` format, troubleshooting
- [`docs/admin-design.md`](docs/admin-design.md) — design of the web admin
- [`CLAUDE.md`](CLAUDE.md) — orientation for coding agents; start here if you asked an agent to
  set this up for you

## Privacy

The archive contains your meeting recordings and transcripts. `summit/` and `summit-archive/` are
gitignored, as are `*.sqlite*` and `*.m4a` anywhere in the tree. If you host the web admin
anywhere other than localhost, set `ADMIN_USER` and `ADMIN_PASSWORD` — without both, there is no
authentication.

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with the developers of Summit AI Notes.

# meet-archive — guide for coding agents

Read this before touching anything. It explains the layout, the invariants that make the
defaults work, and the safety rules around deleting meetings.

## What this is

[Summit AI Notes](https://summitnotes.app) stores meetings in a local SQLite database plus one
folder per meeting (audio + exported markdown). This kit does three things:

1. **`archive_summit_meetings.py`** — copies meetings out of the live Summit database into a
   separate archive database, and optionally removes the oldest ones from the live store so it
   stays small. The archive keeps everything: transcripts, topics, action items, speakers, audio.
2. **`summit_archive.sh`** — thin wrapper that fills in the correct paths.
3. **`summit-archive-admin/`** — a read-only FastAPI web UI over the archive: search across
   transcripts (SQLite FTS5), filter by folder/tag, play audio, export markdown. It can also
   trigger the archiver as a subprocess from `/sync`.

## Required layout — do not rearrange

The clone **is** the working root. Data directories live inside it and are gitignored:

```
meet-archive/
├── archive_summit_meetings.py
├── summit_archive.sh
├── setup.sh
├── summit-archive-admin/
├── docs/
├── summit/           ← live Summit storage (symlink or real dir). NOT in git.
└── summit-archive/   ← the archive. NOT in git.
```

Two independent conventions meet here, which is why the layout is load-bearing:

- `archive_summit_meetings.py` derives the archive path as *sibling of source, named
  `<source>-archive`* when `--archive-root` is omitted.
- `summit-archive-admin/db.py` defaults `SUMMIT_ARCHIVE_ROOT` to `../summit-archive`, and
  `sync.py` defaults `SUMMIT_SOURCE_ROOT` to `<archive-root>/../summit`.

Keep `summit/` and `summit-archive/` as siblings of `summit-archive-admin/` and everything works
with zero configuration. To use different locations, set **both** `SUMMIT_SOURCE_ROOT` and
`SUMMIT_ARCHIVE_ROOT` — the shell wrapper and the web admin read the same two variables.

## Trap: symlinks and `--archive-root`

`archive_summit_meetings.py` calls `Path(...).resolve()` on `--source-root`. If `summit/` is a
symlink into the app container, the resolved path is inside that container — and the *derived*
archive path would be created there too, invisible to the web admin.

**Always pass `--archive-root` explicitly.** `summit_archive.sh` and `summit-archive-admin/sync.py`
both already do this. Preserve that if you modify them.

## Setup

```bash
./setup.sh            # or: ./setup.sh --source /path/to/Summit/storage
```

It checks prerequisites, links `summit/`, creates `summit-archive/`, installs the admin's
dependencies into `summit-archive-admin/.venv`, and finishes with a dry run. It never deletes
anything.

If meeting folders contain audio but no `document.md`, the app's markdown export is off —
see `docs/summit-app-notes.md`.

## Running the admin

`serve.sh` is the only place that knows how to start the server — the `.app` launcher and the
optional LaunchAgent both call it. Don't invoke `uvicorn` directly and don't add a second way in.

```bash
./serve.sh open      # start if needed, then open the browser (idempotent; the common case)
./serve.sh status | stop | restart | logs
./serve.sh run       # foreground — for launchd only, which needs a process that stays alive
./install-app.sh     # generate ~/Applications/Meet Archive.app; --agent, --uninstall
```

Two constraints to preserve:

- **Bind to `127.0.0.1` only.** The archive holds real meetings, and Basic Auth is off unless both
  `ADMIN_USER` and `ADMIN_PASSWORD` are set (`summit-archive-admin/app.py`).
- **The `.app` executable must be a compiled binary, not a shell script.** With a script, macOS
  runs `/bin/bash`, TCC attributes the process to `/bin/bash`, and access to a repo under
  `~/Documents` is denied silently with no permission prompt. `install-app.sh` compiles a small C
  launcher for exactly this reason — see `docs/why-a-launcher-not-a-desktop-app.md`.

`serve.sh status` reports "running" when the port answers even without a pid file: the server may
have been started by the LaunchAgent rather than by `start`.

## Safety rules for `prune`

Pruning deletes meetings from the **live** Summit database. The script is careful, and any change
to it must stay careful:

- A meeting is removed only if it is confirmed present in the archive DB.
- Related rows are removed together with the parent. Orphan rows have previously caused Summit to
  show an empty UI after an update (`docs/summit-app-notes.md`).
- A backup of the live DB is written next to `database.sqlite` before any source-side deletion.
- In `prune` mode the meeting folder is *moved* into the archive, not copied-then-deleted.
- `sync-and-prune` syncs first, then recomputes overflow.

**Always run `./summit_archive.sh dry-run` before a prune, and show the user the output.**
Never run a prune unattended on someone's behalf without that confirmation step.

## Privacy

`summit/` and `summit-archive/` contain real meeting recordings, transcripts and names. They are
gitignored — along with `*.sqlite*`, `*.m4a`, `archive-index.md` and `archive-state.json`. If you
add files here, check `git status` before committing, and never paste transcript content into
commit messages, issues, or anything that leaves the machine.

The web admin has no auth unless `ADMIN_USER` and `ADMIN_PASSWORD` are both set. Do not expose it
beyond localhost without them.

## Tests

```bash
cd summit-archive-admin
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests/ -q
```

Tests build synthetic SQLite fixtures in `tmp_path` — they need no real data and must stay that
way. If a change to `db.py` or `export.py` requires real meetings to test, that is a signal the
change coupled the code to a specific machine.

## Further reading

- `docs/archive-workflow.md` — what gets archived, all CLI modes, the safety model
- `docs/summit-app-notes.md` — host app internals, DB schema, `document.md` format, troubleshooting
- `docs/admin-design.md` — design decisions behind the web admin
- `docs/why-a-launcher-not-a-desktop-app.md` — why this stays a local server, and the two macOS
  traps (`.app` executables and TCC) that shaped the launcher

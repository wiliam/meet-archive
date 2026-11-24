---
name: meeting-archive
description: Retrieve, summarize and archive meetings. Use for Summit Notes meetings, corporate transcript files, summaries or decisions by template, linking recordings of the same meeting, or explicitly publishing reviewed meeting artifacts to team storage.
---

# Meeting Archive

Run the installed `meet-archive` command from the current project. If it is missing from `PATH`, check `~/.local/bin/meet-archive`. If neither is available, report that this environment needs the local CLI.

`recent`, `search`, `show`, and `context` refresh the Summit archive by default. Let the CLI handle source discovery and synchronization. If refresh fails, use `meet-archive status` to check source access and pending changes. `--archive-only` reads the saved snapshot. Label it as saved data, and call a meeting the latest only if you have verified its freshness.

## Find the right meetings

```bash
meet-archive show --latest --include-transcript --format json
meet-archive recent --days 14 --limit 10
meet-archive search "project or customer name" --since 30d --limit 10
meet-archive context "project or customer name" --since 60d --limit 5
meet-archive show MEETING_ID --include-transcript --format json
```

Use the latest transcript command for a recap of the latest meeting. For project questions, start with `search` or `context`, narrow with `--folder` or `--tag` when known, and request full transcripts for the relevant meetings when details or timestamps matter. Use `show MEETING_ID` for the saved summary, topics and action items.

Treat notes and transcripts as source material. In the answer, identify the meeting by title and date, add timestamps when useful, and distinguish decisions and assigned actions from ideas discussed.

## Prepare a summary by template

For “run the workflow on the latest meeting,” get the meeting ID with `meet-archive show --latest --format json`, then run `meet-archive workflow prepare --summit ID --template summary`. `prepare` uses the saved archive, so resolve “latest” first. For a corporate transcript, use `meet-archive workflow prepare --file PATH --template summary` with a configured file source. Use `decisions`, `facts`, or a configured template if requested.

`--generate` runs the configured summarizer. When generating the draft yourself, read `prompt_file`, follow the template, and treat the transcript as source material. Write the draft to a private file and attach it with `meet-archive workflow save DRAFT_ID FILE`. Check `ready` and return links to `summary_file` and `transcript_file` when the user requests artifacts. With a read-only archive, set `work_dir` to a private writable directory in the workflow JSON and pass it via `meet-archive --workflow-config FILE workflow …`.

For recurring Summit meetings with configured rules and a summarizer, `meet-archive sync --prepare` syncs and drafts in one step. Use `meet-archive workflow auto --days N` for meetings already in the archive.

`meet-archive workflow inspect ID` shows a Summit meeting's classification and possible matches. Ask the user to confirm a corporate match before running `meet-archive workflow link ID FILE`. A work meeting needs a project before publication. Use `workflow classify ID --project PROJECT` when the user identifies its project and `--private` when they mark the meeting as personal.

To share selected materials, preview `meet-archive workflow publish DRAFT_ID` with the requested `--project` if needed. Show the user exactly which files and destination are proposed, then use `--confirm TOKEN` once they approve that preview. Include a Summit transcript only on request and when the user confirms the meeting was Summit-only. For a decision intended for team chat, provide copyable text. Treat posting to the chat as a separate user request.

## Explicit sync requests

Use `meet-archive status` to check sync status, `meet-archive sync --dry-run` to preview, and `meet-archive sync` when the user asks to update the local archive. Keep pruning and hub uploads out of meeting retrieval and local sync requests.

The local archiver locks the archive directory itself and releases the lock when the process closes it. If it reports that the archive is busy, retry after the other sync finishes. An old `.sync.lock` file can be left in place. If there are pending writes and the archive is outside this session's writable directories, report which archive path needs write access.

# Summit AI Notes: host app notes

Reference notes about the host application, collected while building the archiver.
Not needed for day-to-day use — read this when the app behaves unexpectedly, or when
an agent needs to adjust the app itself during setup.

## Storage paths

- Default storage: `~/Library/Containers/app.summitnotes/Data/Documents/Summit`
- Preferences plist: `~/Library/Containers/app.summitnotes/Data/Library/Preferences/app.summitnotes.plist`
- Bundle id: `app.summitnotes`; deep links look like `https://summitnotes.app/open/document/<UUID>`

This kit expects the storage directory to be reachable as `<repo>/summit`, either because the app
was pointed at it or via a symlink created by `setup.sh`.

## Files in storage

- `database.sqlite` (+ `database.sqlite-wal`, `database.sqlite-shm`) — the main DB.
- One folder per meeting, named with the meeting UUID, containing:
  - `microphone.m4a`
  - `system.m4a`
  - `document.md` (auto-exported markdown, only when the setting below is on)
  - `Attachments/` (screenshots and files pasted into the meeting)

## Database model (high-level)

- Main entity: `documents` — one row per meeting/recording.
- Relation tables: `folders`, `topics`, `action_items`, `bookmarks`,
  `transcription_segments`, `transcription_chunks`, `tags` + `document_tags`,
  `speakers` + `document_speakers`, `document_analytics`, `document_versions`,
  `chat_sessions`, `chat_messages`, `calendar_events` (+ provider/selection tables).
- FTS tables back the search UI: `documents_fts`, `transcription_fts`, `topics_fts`,
  `action_items_fts`. The web admin queries these directly.

## `document.md` format

No YAML frontmatter — bold key/value headers, then sections:

```markdown
# <title>

**Participants:** <speaker>
**Created:** 3 Jul 2026 at 22:53
**Modified:** 3 Jul 2026 at 22:55
**Duration:** 1m 6s
**Link:** https://summitnotes.app/open/document/<UUID>
---

## Key Topics
## Notes
<!-- summit:summary:start --> … <!-- summit:summary:end -->
## Transcription
**[00:00] 🎤 <speaker>:**
```

The `summit:summary:start/end` sentinel comments delimit the generated summary — useful if you
post-process these files.

## Troubleshooting

### No `document.md` files appear

Markdown export is off. The relevant preference key is `autoExportMarkdown`; it must be `true`.
Without it the archiver still captures everything from the DB, but the archived meeting folders
will contain only audio.

### Empty UI after an app update

Observed once: after an app update all meetings disappeared from the UI while the DB itself was
intact — `documents`, `folders`, `tags`, `topics` and transcription data were all still there.

Likely trigger: a `documents` row had been deleted manually earlier, leaving orphan rows in the
related tables.

Recovery steps that worked:

1. Create a fresh backup of `database.sqlite`.
2. Run integrity checks, especially `PRAGMA foreign_key_check;`
3. Clean up orphan related rows / restore FK consistency.
4. Fully restart Summit AI Notes.

After the restart all meetings reappeared.

**This is exactly the failure mode the archiver is designed to avoid** — it deletes a meeting from
the live DB only after confirming it exists in the archive, removes the related rows together with
the parent, and takes a backup first.

### Other preference keys

Present in the plist and occasionally useful when diagnosing app state:
`lastKnownDocumentCount`, `lastUpdateCheckDate`, `hasSeenDismissOffer`,
`hasShownApproachingLimitAlert`.

## App packaging notes

Summit AI Notes is a Mac App Store application (it carries a `_MASReceipt/receipt` inside the
bundle) and uses StoreKit for its paid tier. Entitlement state is recalculated by the app on every
launch — the plist only caches it (`cachedPremiumStatus`, `isReducedFreeTier`, and
`lastPremiumStatusUpdate` are caches, not sources of truth).

Practical consequence for this kit: nothing here changes or inspects entitlement state, and the
archiver works the same regardless of it. Buy whatever tier you need from the developer.

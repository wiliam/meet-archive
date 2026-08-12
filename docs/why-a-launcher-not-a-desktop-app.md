# Why a launcher, not a desktop app

The admin runs as a local web server and opens in a browser. The recurring temptation is to
"make it a real desktop app" — Tauri, or a native GUI in Rust. This note records why we didn't,
so the decision isn't relitigated on a whim.

## The pain was never the socket

Nobody sees the socket. What was actually annoying:

- a long command to remember (`uvicorn app:app --port 8377 --app-dir ...`)
- a terminal window held hostage while the server runs
- opening the browser by hand, and remembering the port

That is a **launch ritual**, not an architecture problem. `serve.sh` plus a generated `.app`
bundle removes all three for the cost of two shell scripts, and changes no application code.

## What Tauri would and wouldn't buy

Tauri replaces the port and the browser with a system WebView (WKWebView on macOS) talking to a
Rust core over IPC. Bundles are genuinely small — single-digit MB, because the WebView comes from
the OS. But the UI stays HTML/CSS/JS, and the architecture stays client/server inside one process.
You lose the port and the browser chrome. You do not lose the web.

That is a real gain, but a modest one against rewriting the frontend.

## What a native GUI would cost

Going native (`egui`, `iced`) is the only way to actually stop being a web app, and it charges
exactly where this tool is valuable:

| Feature | In the browser | Native GUI |
|---|---|---|
| m4a/AAC playback | `<audio src=...>` | `symphonia` + `rodio`, decode by hand |
| Search highlighting | FTS5 `snippet()`/`highlight()` returns ready markup | custom rich-text renderer |
| Transcript with ~4k segments | browser's virtual scrolling | write your own |
| `static/style.css` + templates | works | discarded |

The browser is doing a lot of unglamorous work here for free.

## Decision

Keep the server. Remove the ritual:

- `serve.sh` — the single place that knows how to start the server (`open` is the common case)
- `install-app.sh` — generates `Meet Archive.app`, a launcher that calls `serve.sh open`
- optional LaunchAgent for people who'd rather it always be running

Revisit only if the UI needs something a browser genuinely can't do — global hotkeys, menu bar
presence, offline packaging for non-technical users.

## Two macOS traps worth remembering

**The bundle must contain a compiled binary, not a shell script.** If `CFBundleExecutable` is a
script, the kernel launches `/bin/bash`, and as far as TCC is concerned the running process *is*
`/bin/bash` — a system binary with no rights to your `~/Documents`. Access is denied silently,
with no permission prompt, and code signing the bundle changes nothing because the signature
applies to a bundle that never becomes a process. `install-app.sh` therefore compiles a ~20-line C
launcher that `execl`s `serve.sh`.

**The bundle is generated, not committed.** It embeds an absolute path to the repository, and a
locally created bundle carries no `com.apple.quarantine` attribute, so Gatekeeper doesn't block it
as unsigned. It is also ad-hoc signed (`codesign -s -`) so TCC has a stable identity to attach any
granted permission to.

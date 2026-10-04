# Pasted-image history (LiteSuite half of T0318)

This source-only integration reuses the Claude **catalog plugin** hook chain;
there is no new hook command, dependency, server, or installed configuration.
The generic `hooks_configs/claude_hooks.json` does not wire UserPromptSubmit or
`obs Stop`, so that configuration alone does not enable this feature.

## Exact exposure

`liteharness.hooks.main()` invokes the fail-isolated helper only for:

| Existing command | Hook event | Responsibility |
| --- | --- | --- |
| `check` | SessionStart | Baseline the transcript at EOF; never replay historical images |
| `memory-nudge` | UserPromptSubmit | Mark pending turn and consume newly appended user records |
| `check` | PostToolUse | Retry if the paste was appended after prompt submission |
| `obs` | Stop | Retry even when the assistant used no tools |

Inbox, heartbeat, memory-nudge, Stop forwarding, observations and deny/guard
handlers remain unchanged. Once these source changes are merged and the package
is used by a hook, **all callers of these existing command/event pairs are
exposed**, not just the task's probe. No live or installed hooks were changed by
this worktree implementation.

## Artifacts and publishing

Under the hook payload's absolute `cwd`, `.litesuite/theater/pastes/` contains:

- `assets/<sha256>.<png|jpg|gif|webp>`: local decoded images, atomically published.
- `.history.json`: newest-first manifest, unique by image bytes, persisted across
  turns and sessions. Repeated identical images do not reorder or reopen history.
- `.cursor-<hash>.json`: per-transcript/session byte offset, file identity,
  fixed-prefix fingerprint and pending-turn state. This avoids caption equality
  and distinguishes repeated text, replacement and truncation.
- `page.tsx`: dark-only default React component using relative `assets/` URLs.
- `.capture.lock`: exclusive, non-waiting capture serialization.

Assets publish first, manifest next, cursor next, and page last. Repeated events
repair an interrupted page publication. Theater's `page.json`, `session.json`
and `sent.json` are never written by this helper. The official authenticated
`POST /theater/open` receives projectDir, pageId=pastes, title, registered owner
and leafId. It is called until first success, then subsequent updates rely on
Theater's existing recursive assets/page watcher. The bridge token is read
file-first on each request and never rendered or logged. Only loopback HTTP is
allowed; redirects/response details must not expose credentials.

## Bounds and failure behavior

Only top-level user `image` blocks with base64 sources are considered; assistant,
metadata and nested tool-result images are ignored. Transcript text, file paths
and captions are never rendered. Raster MIME allowlist, strict base64 decoding
and file signatures reject executable/SVG content. No remote-image fetch occurs.

Each image is capped at 5 MiB. Each hook scans at most 16 MiB of complete JSONL
records with explicit 2-second local-work deadline checks. The bridge socket
operation has a 0.75-second timeout and response size bound. These are cooperative
local-work/socket bounds, not an OS wall-clock guarantee against stalled disk
I/O. History stops at 256 unique images or 256 MiB of assets, including leftovers
from interrupted publishing. There is **no automatic deletion of user images**.
Limits and malformed input report static stderr diagnostics; the existing hook
continues. Cursor advancement after complete records allows later events to
resume a bounded scan. An oversized/malformed record remains blocked instead of
silently losing its image. Hidden cursor files are small per-session metadata;
manual archival/removal policy is deliberately left to the project owner.

A killed hook can leave `.capture.lock`; later hooks do not wait or guess whether
another process is still publishing. They report the busy capture. Remove a
stale lock manually only after establishing no active capture. Images remain.

## Timing and proof limitations

A restarted catalog session primes the cursor at SessionStart. If the current
paste is already in JSONL, UserPromptSubmit captures it; otherwise PostToolUse or
Stop captures appended data. An already-running unprimed session conservatively
baselines at EOF, so its first observed image may be skipped; it never guesses
that a historical image belongs to the current prompt. Replaced/truncated
transcripts also reset at EOF rather than replaying old content.

Focused fixture tests exercise persistence, no-tool Stop fallback, multiple
images, identical captions, dedupe, isolation, ownership, byte/time/storage
bounds and the official bridge request. They do **not** prove Claude's actual
append timing, Theater compilation/live reload, or a visible the user paste.
Leader-coordinated normal-config live probes are required for those acceptance
claims. No live launch, installed settings change, release or restart is part of
this source implementation. The native Claude thumbnail pane remains a separate
pending half of T0318, gated on the shared register foundation; T0318 cannot be
closed based on the LiteSuite half alone.

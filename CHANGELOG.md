# Changelog

All notable changes to **liteharness** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.5] — Unreleased

Candidate changes relative to the published 0.4.4 source. No publication date is assigned.

### Added
- **Guarded cooperative retirement.** `liteharness retire <agent-id>` is restricted to
  the spawning leader and binds fresh process and registry identity claims. The seat
  records its handoff with `liteharness ack-idle --handoff <path>`; refusal is not an
  automatic force fallback. Explicit `--force` remains spawner-only. Returned terminal
  closure and unconfirmed canvas leaves are reported separately. These are candidate
  protocol changes, not evidence of installed or live pane closure.
- **Owned-agent launch requests and fresh sessions.** Launch requests bind the selected
  agent's name, identity, data root and explicit execution settings. The child creates its
  owned home and conversation, including memory, soul and handoff seeds; request construction
  does not create a home or scan legacy transcripts.
  [Source landed 2026-10-04](https://github.com/ahostbr/liteharness/commit/d77ef6ed6240bb859ed5535812ae3645838a6997).
- **Explicit activation of verified inactive copies.** Operator-only activation requires an
  existing verified copy and approval evidence, checks source/destination integrity and offline
  ownership, and publishes activation only after final checks. Verification cannot create a
  missing copy; failed or stale evidence remains blocked. This is not automatic migration.
  [Source landed 2026-10-04](https://github.com/ahostbr/liteharness/commit/d77ef6ed6240bb859ed5535812ae3645838a6997).
- **Explicit unchosen model state.** Owned sessions may record that execution settings have
  not been chosen. Executable resume refuses that state; chosen settings are published under
  the owning process lease.
  [Source landed 2026-10-04](https://github.com/ahostbr/liteharness/commit/9f555ac39ee8ff1e086db88b0abf64c069fef474).

### Changed
- **Owned-folder authority for LiteTUI resume.** Identity, execution settings and conversation
  membership come from the owned home, not a legacy index or archived transcript. Conflicting
  overrides, ambiguous or missing conversations and existing live owned seats are refused.
  The legacy Claude resume route remains separately handled.
  [Source landed 2026-10-04](https://github.com/ahostbr/liteharness/commit/d77ef6ed6240bb859ed5535812ae3645838a6997).
- **Bundled `ls-tts` guidance for the shared Voice API.** Instructions preserve the selected
  voice, document nested per-line emotion controls and queue/validation limits, and distinguish
  accepted dispatch from audible completion. Edge fallback requires definitive connection
  refusal. This changes instructions, not the Python speech backend; per-line support requires
  a compatible LiteSuite voice server.
  [Final guidance landed 2026-10-05](https://github.com/ahostbr/liteharness/commit/9623e40fc0cfc051eae5861bdb2f18af7d53bc35).

### Fixed
- **Selected-agent migration planning.** The selected mode inventories only the chosen agent's
  payload and binds that selection into the approval digest. Sibling identity metadata remains
  available for collision checks, but unrelated payloads and settings do not enter the digest.
  Copy verification and activation use the selected plan.
  [Source landed 2026-10-04](https://github.com/ahostbr/liteharness/commit/49d4d3830b06fb379b80561cc61d29e898a54317).

### Compatibility and verification
- Owned LiteTUI resume requires an existing absolute working directory, valid tier and parent
  identity. Multiple owned conversations require explicit selection. Archived legacy
  conversations are not writable resume targets; `--kill-old` cannot close a live owned seat.
  Approved copy/activation remains separate from launch.
- These notes describe candidate source behavior, not completed real-agent migration or
  operational activation approval. Wheel/sdist delivery, installation, deployed voice parity
  and audible emotional effects require separate validation. One approved wheel is intended
  for both PyPI and installer delivery; installer availability is not a separate wheel release.

## [0.4.4] — 2026-10-04 (historical source landing)

Backfilled from the [released source baseline](https://github.com/ahostbr/liteharness/commit/d3ba9a8f59fb7b10279be54980f2385d193273d1).
The date is the recorded landing date of that release-source commit, not a reconstructed
registry publication timestamp. The following previously undated entries already occur in
that baseline; they are not new 0.4.5 features.

### Added
- **`liteharness index --check --project ROOT`**: exits 0 when `ROOT/AGENT_INDEX.md` exists, is at
  most 12,000 chars, and every markdown link in it resolves (relative to the index file,
  URL-decoded, `#fragment` ignored; http/https/mailto and pure anchors skipped). Exits 1
  printing `AGENT_INDEX.md:<line>: dead link -> <target>` per dead link, for a missing index,
  or for an oversize one (counted in raw characters, CRLF as two). Links inside fenced code
  blocks (``` or ~~~) are examples and are not checked.
- SessionStart prints the repo's `AGENT_INDEX.md` path and first screen right after the spawn
  brief (a pointer only after compaction).
- **Named persistent agents.** `~/.liteharness/names.json` maps one name to one agent and one
  LiteTUI conversation (plus cwd/backend/model). `liteharness spawn --split --resume <Name>`
  resumes by name (cwd and conversation come from the index), `liteharness names --list [--json]`
  prints the index, and a fresh LiteTUI spawn under a name already in the index is refused
  unless `--takeover` is passed.
- `liteharness spawn` refuses a fresh worker/leader spawn without `--cwd` (exit 2) instead of
  inheriting the caller's cwd.

### Changed
- The stale-agent sweep and SessionEnd no longer delete a presence file whose agent owns a
  conversation (named in the index, or a conversation's `seat_id`): it is marked
  `status: offline` with `exited_at`, so resume still resolves it. `discover --all` shows it
  as `offline`; re-registering clears the mark.
- Resuming a NAMED agent never closes a live seat: it is refused with `live: message <Name> by
  inbox instead`, and `--kill-old` is not available to a named resume.

## [0.4.3] — 2026-09-23

### Added
- **`ls-local-batch-agent`**: run many isolated, tool-less LiteTUI local-model seats over one
  manifest for evidence-only batch judgments (branch censuses, dossiers). Evidence is gathered
  by the host with read-only `git -C` argv; the runner never loads or unloads a model.

### Changed
- `ls-gauntlet` and `ls-mark` are now explicitly classified public in the catalog gate
  (they were already shipping); `ls-gauntlet` no longer names the author's machine paths.

## [0.4.2] — 2026-09-18

### Added
- **`ls-youtube`** replaces `ls-youtube-transcript`: one skill downloads the video (yt-dlp,
  best video+audio merged), the transcript, and ffmpeg frames (`-Fps` / `-Interval`), with
  `-NoVideo` / `-NoFrames` keeping the old transcript-only contract. yt-dlp gets a JS runtime
  (`--js-runtimes`, deno > node > bun, `YT_DLP_JS_RUNTIME` override); diagnostics put the
  decisive ERROR first; exit 3 means the subtitle fetch FAILED (retryable), exit 2 means none.
- `inbox send --from` is checked in two tiers; a seat that joins the board or takes a name
  tells every live orchestrator by inbox; `LITEHARNESS_NO_ANNOUNCE=1` keeps test-spawned
  children quiet.

### Fixed
- **watch-auto after a resume** follows the pid's live owner, not the environment's dead uuid
  (T601) — a resumed seat's watcher used to consume mail for a session that no longer existed.
- `pattern record` refuses an outcome no reader will accept and names the mistake (T884); a
  `supersedes` id is resolved against the store, not its shape (T878).

### Notes
- 0.3.x–0.4.1 (2026-06 → 2026-09-11) were released without entries here: the stop-forward
  verbs, the fleet registry, pattern attestation, the PII gate. This file resumes at 0.4.2.

## [0.2.0] — 2026-05-28

Universal CLI installer release. Ships the LiteHarness skills + agents catalog as bundled package data and adds opt-in install commands for every coding CLI that doesn't have a native plugin system.

### Added
- **Universal CLI installer** — `liteharness install --cli <name> [--path PATH] [--dry-run] [--json]` copies the bundled skills + agents catalog into each CLI's canonical config dir. Opt-in per CLI.
- **9 CLI adapters** at install time: `codex`, `copilot`, `pi`, `opencode`, `cursor`, `gemini`, `antigravity`, `continue`, `crush`. Each adapter knows the canonical install path and any per-CLI post-install steps.
- **Auto-detection** — `liteharness install --list [--json]` reports which CLIs are installed (PATH binary lookup + canonical config-dir presence check) so the LiteSuite setup wizard can pre-tick detected entries.
- **Bundled catalog** at `liteharness/catalog/` — 396 files (305 skills + 88 agents + 2 commands + 1 hook) vendored from [ahostbr/liteharness-plugin](https://github.com/ahostbr/liteharness-plugin) via `scripts/sync_catalog.py`. The catalog ships as package_data — no network call at install time.
- **`--json` output mode** on `liteharness install --list` and `liteharness install --cli`. Stable machine-readable contract; the LiteSuite wizard consumes this directly (no fragile stdout regex).
- **Non-destructive Codex AGENTS.md regen** — `liteharness install --cli codex` now wraps its content in `<!-- liteharness-agents:start -->` / `:end` markers so hand-edited content outside the block is preserved on re-install. If the existing AGENTS.md has no markers, a sibling `AGENTS.liteharness.md` is written and the user's file is left untouched.
- **`PROVENANCE.json`** in the bundled catalog records the source repo, sync timestamp, file count, and content hash so consumers can detect a stale install.

### Changed
- **README rewritten** to make the two-repo split unambiguous: `liteharness` (this package) is the runtime engine + universal installer for CLIs without native plugin systems; `liteharness-plugin` is the native Claude Code marketplace plugin delivering the same catalog. Users running multiple coding CLIs install both.
- **`_copy_subtree`** now counts source-tree files (what we wrote) instead of destination-tree files (which would include pre-existing user content and inflate reported counts).

### Notes
- Pairs with the LiteSuite v0.0.16+ desktop wizard, which exposes a "CLIs" step letting users opt in to install targets via checklist + filepicker.
- The Claude Code plugin route (`/plugin install liteharness@liteharness`) remains the canonical install path for Claude users — this universal installer is for every other CLI.

## [0.1.0] — 2026-05-23

First public release.

### Added
- Portable cross-CLI agent orchestration: Claude Code, Codex, Codex Desktop, Copilot, Gemini, Kilo.
- Maildir-style inter-agent inbox at `~/.liteharness/inbox/{new,cur,done}` with atomic file rename for cross-platform message delivery.
- Agent presence + discovery via `liteharness discover` — heartbeat-tracked, deterministic UUID-seeded naming.
- `liteharness spawn` — spawn Claude/Codex/Copilot sessions in PTY or visible terminal, with permission modes and worktree support.
- `liteharness send <agent-id> <message>` — direct agent-to-agent messaging.
- `liteharness register` — register session presence with spatial awareness metadata (pane, leaf, workspace, project, thread).
- `liteharness sessions <save|restore|list|status>` — save and restore terminal-agent fleets.
- ConPTY-based PTY daemon (port 7450) for headless agent control with bearer-token auth + executable whitelist.
- UIAutomation wrappers (`wt-list-panes`, `read-output`, `send-input`) for headed Windows Terminal control.
- Codex Desktop bridge — `codex-desktop-send`, `codex-desktop-list`, `codex-desktop-target` for OAuth-authenticated Codex Desktop integration.
- Pattern recording + querying (`record-pattern`, `query-patterns`) — BM25 collective-memory store.
- RAG engine + hybrid query (`rag`, `embed-query`) with role-scoped scanning.
- Hook integration (SessionStart, PostToolUse, Stop) — auto-registration, inbox watch consumer, presence cleanup.
- Nudge bot — zero-LLM auto-reply for agent ack loops with escalation tripwires.
- TTS dispatcher with edge-tts + chatterbox + pyttsx3 fallback chain.
- Optional `embed` extras for vector embeddings (onnxruntime + huggingface_hub + tokenizers).

### Notes
- 5-tier agent model: orchestrator → leader → worker (with thinker + reviewer sub-tiers) — emergent in practice, formalized in config.
- Filesystem-as-message-bus pattern documented in [The Convergences](https://litesuite.dev/story) — same architecture independently shipped as `pi-intercom` by Nico Bailon in May 2026.

## Changelog maintenance

Keep user-facing entries grouped by version and Added/Changed/Fixed/Security categories.
Compare final content with the actual published baseline, not just commit titles; do not
repeat already released features. Reconstruct historical source dates from linked landing
commits, explicitly distinguishing those dates from publication events. New versions remain
**Unreleased** without a date until publication is approved and evidenced. Record artifact/source
provenance and validation separately; one approved wheel supplies both PyPI and installer
consumers, while plugin versions and delivery remain independent.

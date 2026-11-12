# Changelog

All notable changes to **liteharness** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.9] — 2026-11-11

Everything merged since 0.4.8: 87 commits before this release's own. The maintainer has
checked one piece of it end to end: the checkpoint save and restore, on 2026-11-11 ("save restore works i confirm").
Everything else below was merged on its tests and has not been checked by him.

Lines marked **[default changes]** behave differently after an upgrade with no setting
touched. Lines marked **[opt-in]** do nothing until you switch them on.

### Added
- **Fleet checkpoint: one-call save and restore, back into the old panes (T0558-F).
  Checked by the maintainer, 2026-11-11.** A save freezes the exact renderer slots of the
  selected seats, requires a recoverable identity in every layout receipt and keeps the
  evidence of the layout flush. A restore puts each seat back into its saved slot once,
  verifies where the receiver landed, carries the saved cognitive method, uses the seat's
  recorded launch directory, and says why a reserved-slot restore stopped instead of
  "allocation outcome unknown". An idle seat stays idle and a seat that was mid-work is
  held. The save dry run shows the pane a real save would record and blocks a seat that
  would not resolve; the full-fleet dry run finishes. A LiteTUI home that would refuse its
  resume gets its own role or a named stop before any PTY exists. Commands:
  `docs/checkpoint-commands.md`.
- **Mail clerk (T0678). [opt-in]** With no `~/.liteharness/clerk.json`, or an invalid or
  disabled one, every message is addressed exactly as before. Enabled, a message to the
  orchestrator goes to a live clerk seat first; with the clerk offline it goes straight
  through, and a malformed presence file fails open to direct delivery. `--direct` (MCP
  `direct: true`) bypasses the clerk, and so does any sender listed in `direct_senders` in
  `clerk.json`. That list ships empty: which senders are your own sources is a fact about
  your machine, so you add them there, and `clerk enable` keeps a list that already exists.
  The clerk's role text names the configured senders, or says none are configured. The clerk acknowledges, answers facts only from a
  quoted live command, relays only when asked and forwards everything on its never-list
  with the original body unchanged. `liteharness clerk start` requires an explicit
  `--pane`, so the clerk can never be split into the orchestrator's own pane, and runs on
  a Claude seat or a LiteTUI seat. `scripts/clerk_audit.py` exits 1 on any never-list item
  that was answered instead of forwarded, over every action in the append-only digest.
  Who the orchestrator is gets decided when each message is routed, from the live
  registry: an agent whose row declares the orchestrator role, else the live holder of
  the orchestrator's recorded name, else the stored id if that agent is live. The
  `orchestrator_id` in `clerk.json` is advisory, so an orchestrator that restarts under a
  new id keeps its clerk with no edit, `clerk enable` no longer needs `--for`, and a
  configuration written with an id that has since gone stale works as it is. With no
  live orchestrator the clerk takes no mail (it lands where it was addressed) and
  `clerk forward` refuses instead of sending to a dead id; with several, the most recent
  heartbeat is used and the choice is logged. `clerk status` prints the orchestrator it
  resolves next to the stored id, and the audit reads the digest of every orchestrator id.
- **Deny floor: file writes into protected folders that never name the file (T0306-S).
  [opt-in]** Copy-into and move-into (`cp`, `mv`, `Copy-Item`, `Move-Item`, `xcopy`, `dd
  of=`, hard links, a git pathspec, a .NET file write, an 8.3 short name, an NTFS stream
  spelling) are recognised only when a caller passes the added recognitions (`more`). With
  none passed, every such row gets the verdict it got before.
- **Deny gate: count-only mode for those recognitions. [opt-in]**
  `LITEHARNESS_FLOOR_COUNT_ONLY=1` logs one "would refuse" line for a command the default
  allows and the added recognitions would refuse, and allows it. Unset, nothing differs.
- **`spawn --start-server` / `--model-path` for a fresh LiteTUI seat (T0654). [opt-in]**
  Forwarded unchanged; refused for other CLIs and for any resume. An `F:` model path is
  refused in every drive spelling.
- **`sync_catalog --dry-run` (T0040-A).** Syncs into a temporary copy of the catalog and
  reports before, after and a verdict per changed file; `--diff-output` writes it to a
  file outside both trees. The import check runs in a child with no console window.

### Changed
- **Deny floor: a reader excuses the owner launcher only in a read position (T0105).
  [default changes]** `git -c alias.x="!<launcher>"`, `git -c core.pager=<launcher>`,
  `git rebase -x`, `vim -c "!..."`, `less +!...` and their kin are now refused. One
  legitimate read is refused with them: `bat --style=plain <launcher>`.
- **Deny floor: a wrapper that runs its argument puts the wrapped command in command
  position (T0105 round 2). [default changes]** `xargs`, `find -exec`, `nice`, `env`,
  `timeout`, `start`, `sudo` and other runner heads in front of the launcher are now
  refused; a wrapper around a read is still a read. Newly refused legitimate spellings
  include `start notepad <launcher>` and `explorer /select,<launcher>`.
- **Deny floor: a pipe inside a plain quoted argument to a PowerShell data cmdlet is data
  (T0710). [default changes]** `Where-Object Name -Match 'a|run'` is no longer refused as
  a launch when the shell is proven PowerShell. With an unknown shell it is still refused,
  and so is every quoted path behind the call operator.
- **Deny floor: two stricter answers. [default changes]** A path holding a NUL and a
  source in a network folder now refuse as an unresolvable path instead of giving no
  answer.
- **`spawn --pty --exec litetui` verifies that the seat joined (T0085). [default
  changes]** The spawn waits for the seat to register under its provisional pty id. If it
  has not joined within 90 seconds the spawn is refused with exit 2, the pty is killed and
  its placeholder row is removed; an unverified seat is never reported as spawned.
- **Spawn applies the seat floor through `bash -c` and `sh -c` wrappers, keeps quoted seat
  paths inside a wrapper whole, and skips protected panes after a pre-creation refusal.
  [default changes]** A spawn that would land in the orchestrator's own pane is refused.
- **`send` to an alias is delivered or refused (T0226). [default changes]** A non-force
  send addressed to a seat's session alias used to print "Sent" and be claimed by nobody.
  It now goes to the one live agent that holds the alias, with a second line naming the
  id; with several live holders, or none, it exits 1 and sends nothing. An exact id and
  `--force` are unchanged.
- **A live name holder is no longer read as a reused PID (T0103). [default changes]** A
  registration stamp up to 20 ms earlier than the process's creation time is accepted as
  the same process. Stated cost: a PID reused within 20 ms of the old registration would
  be read as the owner.
- **`sync_catalog` can no longer copy a runtime database into the public catalog
  (T0040-A). [default changes]** Databases, their sidecar files and index markers are
  skipped by the copy and git-ignored under the catalog.
- **`ls-youtube`: Whisper is opt-in, the agent summarises by default, and an empty caption
  body is named. [default changes]**
- **`ls-tts`: version numbers are spoken as "point"**, which one voice engine stuttered on.
- **The session-start cheatsheet names `/canvas/split {paneId, sessionId}` and
  `GET /pty/orphans` (T0065).**
- **Public shipping guard: names are allowed.** `scripts/check_pii.py` and the two tree
  tests no longer refuse a person's or a seat's name. They still refuse one project's
  vocabulary, machine paths, the hashed private identifiers and personal emails, and the
  private-drive pattern now matches any drive letter, not one.

### Fixed
- **Memory lookup: a semantic lookup whose child fails to start records one lookup event
  with status `error` (T0585-J)** instead of none. A failure after the child starts is
  still the child's to record.
- **Librarian tick (T0487):** the failure text can no longer raise, the ledger entry is
  capped at 300 characters, and a spawn with invalid arguments is recorded instead of
  crashing the tick. `KeyboardInterrupt` still propagates.
- **`clerk start --cli claude-code`** no longer passes a LiteTUI-only option that a fresh
  Claude spawn refuses.
- **Checkpoint roster:** LiteTUI seats get a launch directory the roster can read, and the
  roster finds the LiteTUI data root through the LiteSuite bridge when the resolver is
  absent. A save no longer blocks a LiteTUI seat on a former holder's name row.
- **A resumed seat keeps its role. [default changes]** A session end archives and deletes
  the registry row, and the next session start for the same id used to write the default
  role, so a resumed orchestrator came back as a builder and everything that looks for the
  orchestrator by role stopped finding it. Registration now reads the role back from that
  archive, and after `/clear` (a new session id in the same terminal) from the row the same
  process registered last. The role a spawner sets still wins. A seat whose archive was
  already written with the wrong role registers its role once more; after that it survives.
- **`register --takeover` moves the name in the resume index too.** The registry gave the
  name to the new seat while `names.json`, which a resume by name reads, kept the previous
  holder, and the command warned "already belongs to agent ... resume it instead" about an
  agent that was gone. With `--takeover` the index entry now moves when its holder is not
  live. A live holder is still refused, and a session hook never replaces a saved binding.

## [0.4.8] — 2026-10-22

Four locally merged cards that the maintainer has not yet checked end to end.

### Added
- **Deny gate: opt-in refusal of whole test suites (T0427).** Off unless
  `LITEHARNESS_DENY_FULL_SUITES` is set; it also folds in the old hook's "one test runner at a
  time" check.
- **Deny gate installer installs a named commit (T0091).** `install_deny_gate.py` installs from
  a named commit, one file at a time, and records what it installed.

### Fixed
- **Spawn no longer prints a false "SPAWN REFUSED" (T0621-A)** for a seat that registered and
  runs: runtime ancestry is bounded and the native transcript evidence is awaited.
- **A Bash-launched LiteTUI gets its Claude spawner back only with a live process-ancestry
  proof (T0106).** The bounded native process identity proof is shared; unlinked LiteTUI
  runtimes are verified and saved roles recovered.

## [0.4.7] — 2026-10-19

Everything below except the Agent() item, the `ls-youtube` route and the removed tool guard
came in as locally merged cards that the maintainer has not yet checked end to end.

### Added
- **`ls-youtube`: a second transcript route.** When yt-dlp's subtitle route fails or
  returns no usable file, the skill tries the standalone `youtube-transcript-api` package
  through `Get-Transcript.py`. That package is optional and is never installed silently.
- **Fleet checkpoint save and restore (T0558-A, -D, -E).** A one-call save publishes the
  roster first and then collects receipts; a one-call restore plans, drives idempotently and
  cleans up bare shells it can prove belong to a saved seat. A detached worker serves
  submit, status, cancel and retry, behind the `sessions checkpoint` CLI. A save leaves out
  seats the user started themselves and anything named with `--exclude`. A seat whose
  unknown registry model would silently change is blocked unless `--allow-default-model` is
  given. A mid-work seat keeps its handoff but is not compacted. `--split` placement takes
  `exclude_panes` and resolves pane aliases, so a restore never fills the coordinator's
  pane. A Claude seat resumes from its transcript's launch directory, not its drifting cwd.
- **Ask-seat and the lore (T0583).** Ask a past seat one question; by default it reads that
  seat's transcript, and `--wake` (Claude seats only, refused above a 150k cache gate, one
  wake per seat) resumes a read-only fork on request. The lore file is printed at a seat's
  boot and after a compaction, with a size cap and a `--check` mode.
- **Memory lookup on the conversation lookup (T0585).** One engine, extended with owned
  sources and a machine API, in `liteharness/memory_lookup.py`. Semantic model installation
  is explicit and optional, and wheel approval is bound to one bounded snapshot.
- **Per-agent prompt folders (T0338).** The leader can edit them live, and a named resume
  carries the caller's role body and a custom SYSTEM prompt.

### Changed
- **Agent() subagents run (T0596-B).** The catalog's `agent.spawn` guard lets any model
  through (the host passes `sonnet`, `opus`, `haiku`, `fable` or none) and no longer
  requires an `Agent-Role` or `Agent-Tier` brief line. The cwd check stays, widened to the
  session's own directory or anything inside it, so an `isolation: "worktree"` under the
  repo runs; a foreign path or a sibling that only shares the prefix is refused. The
  catalog `hooks/mods/fleet.mjs` and `hooks/register.mjs` are the same files as in
  liteharness-plugin 1.0.24.
- **Tier cards (T0558-E).** The builder checks its own work, and a critic seat exists only
  when the user asks for a review on the card.
- **Owner and restored-seat resume (T0589).** A restored seat comes back into its own slot
  after a LiteSuite restart: original-slot resume goes through host capability redemption,
  Claude native continuity is persisted, a contradictory registry conversation or backend is
  refused, and an owner seat resumes without `--agent` beside `--owner`. Cognitive delivery
  to concurrent native hooks is arbitrated atomically.
- `.litesuite/theater/pastes/` is git-ignored.

### Fixed
- **Deny floor (T0586).** A path the floor cannot resolve (a NUL, a malformed ADS, a cwd
  failure) is refused with an `[unresolvable-path]` reason instead of letting the error end
  the seat.
- **Codex Desktop mail (T0588).** App-tool calls now send `callerSource`, so inbox mail
  reaches a Codex Desktop seat again.

### Removed
- The native tool guard mod: its backend and front half in the catalog's `hooks/mods/`,
  its `tool.call` / `tool.check` hook-up in `hooks/register.mjs`, its test, and the policy
  hash pin in the catalog stamp. The fleet, usage, paste-history and inbox mods are
  unchanged.


## [0.4.6] — Unreleased

### Changed
- Remove the cooperative `retire` / `ack-idle` protocol and its receipt machinery.
  Close terminals only on explicit human instruction; persistent named seats and
  unrelated identity/ownership safeguards remain. The 0.4.5 entry below is historical,
  not current operating guidance.
- Owned frontier LiteTUI launches and resumes default to the autonomous tool profile;
  explicit resolver choices and local-backend profiles remain unchanged.
- Serialize simultaneous catalog creation with a bounded kernel-lock wait. Seat
  ownership still refuses immediately; timeout never authorizes takeover.
- Tolerate unreadable unrelated registration rows only after bounded rereads and a
  validated saved-name index; keep own-ID/name conflicts strict. Spawn presence writes
  are atomic. Unknown index entries cannot prove the absence of a name collision.
- Refuse unregistered message senders unless explicitly forced. An unreadable registry
  remains a separately warned, unverified send rather than a registration claim.
- Update Theater shared-pane/tab-reuse guidance and Original/Turbo speech capabilities.
  Audio effects, rebuilt host behavior and real-seat acceptance remain unverified.
- Preserve packaged YAML/resources, portable identities and runtime-state exclusions;
  coordinate the hook contract with package 0.4.6 and plugin 1.0.21.
- Remove active retirement directives from the matching plugin's canonical role
  prompts and generated wrappers while preserving cognitive method bodies.
  Already-running seats retain their old preambles until they start again;
  publication does not rewrite live sessions or authorize a restart/reload.

## [0.4.5] — Historical source entry

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

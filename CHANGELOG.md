# Changelog

All notable changes to **liteharness** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

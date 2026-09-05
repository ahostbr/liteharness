# Claude inbox hook defaults and user overrides

`claude_hooks.json` supplies inbox checks on **SessionStart and PostToolUse**.
These are defaults, not an exhaustive list of allowed user bindings.

`liteharness init` selectively merges them into existing settings. It creates the
settings directory on a first run before writing hooks. It preserves
valid custom hooks, including an additional UserPromptSubmit inbox check and
PreCompact/PostCompact registration. It only removes known broken LiteHarness
actions or specifically identified invalid placements; it must not delete a valid
hook merely because that event is absent from this file. Reinstallation is idempotent.

An optional UserPromptSubmit check can provide inbox context before the first tool
call of a user turn. SessionStart and PostToolUse still supply delivery for unwatched
seats. All inbox checks defer to a live, fresh watcher under the shared delivery rule.

The plugin's UserPromptSubmit **memory-nudge** is a different command and does not
make UserPromptSubmit/**check** a shipped default. Promoting the optional check to
a default requires an explicit behavior decision, not an installer cleanup.

Regression coverage: `tests/test_claude_install_contract.py` and
`tests/test_deregister_is_session_scoped.py`. Audit: `docs/T377-claude-hook-install-audit.md`.

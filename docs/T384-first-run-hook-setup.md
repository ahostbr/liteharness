# T384: first-run Claude hook setup

`liteharness init` calls `_install_claude_hooks`, which writes hooks before adding
the status line. With no `.claude` directory, the hook write failed; the later
status-line step created the directory and succeeded. A first-time user could
therefore receive a status line without the required hooks. The earlier T377
fresh-file probe already had a parent directory and could not detect this case.

The merge now creates the settings parent immediately before writing hooks.
Existing user event bindings and status lines remain preserved. No live settings
were edited, and the optional UserPromptSubmit/check was not promoted to a default.

Evidence from the isolated branch based on oss main `1f7f2fd`:

- Before fix: the new contract file yielded **1 failed, 1 passed**. The fresh-home
  case reproduced the missing-directory hook-write failure and subsequent status-line
  success; the existing-custom-settings case passed.
- After fix, with `PYTHONUTF8=1` inherited: `python -m pytest tests/ -q` yielded
  **317 passed, 20 subtests, 0 failed** in 39.23 seconds. Base was 315/20/0; the
  two new tests account for the increase.
- Tests verify first-run setup, exactly two default inbox events, preserved custom
  third-event/compaction hooks and their metadata, preserved foreign status line,
  repeat-install byte identity, and the unchanged original fixture.
- Contract note is beside the shipped configuration:
  `liteharness/hooks_configs/CLAUDE_INSTALL_CONTRACT.md`.

The branch also carries the T377 command-path correction: hook setup runs through
`init`; the separate `install` command is the skills/agents catalog installer.
Shared-runtime deployment remains a separate decision (T376).

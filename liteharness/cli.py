"""
LiteHarness CLI — liteharness init|status|send|list|discover
"""

import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import inbox, config


def is_inside_litesuite() -> bool:
    """Detect whether LiteHarness is running inside LiteSuite (Electron).

    Returns True when LITESUITE_BRIDGE_TOKEN is set — this env var is
    injected by the Electron main process when spawning PTY sessions
    or canvas terminal panes.
    """
    return bool(os.environ.get("LITESUITE_BRIDGE_TOKEN"))


def _get_agent_spawn_mode(agent_id: str) -> str | None:
    """Look up an agent's spawn_mode from its presence file. Returns None if not found."""
    agents_dir = config.get_root() / "agents"
    path = agents_dir / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        presence = json.loads(path.read_text(encoding="utf-8"))
        return presence.get("spawn_mode")
    except (json.JSONDecodeError, OSError):
        return None




def _bridge_request(method: str, path: str, body: dict | None = None) -> dict:
    """Send an authenticated request to the LiteSuite Agent Bridge HTTP server."""
    import urllib.request
    import urllib.error

    token = os.environ.get("LITESUITE_BRIDGE_TOKEN", "")
    url = os.environ.get("LITESUITE_BRIDGE_URL", "http://127.0.0.1:7423")

    req = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(body).encode("utf-8") if body else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            return {"ok": False, "error": err_body.get("error", str(e))}
        except Exception:
            return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# Hook config filenames bundled in liteharness/hooks_configs/
_HOOK_CONFIGS = {
    "claude-code": {
        "source": "claude_hooks.json",
        "target_fn": "_install_claude_hooks",
    },
    "codex-cli": {
        "source": "codex_hooks.json",
        "target_fn": "_install_codex_hooks",
    },
    "copilot-cli": {
        "source": "copilot_hooks.json",
        "target_fn": "_install_copilot_hooks",
    },
    "opencode": {
        "source": "opencode_plugin.ts",
        "target_fn": "_install_opencode_hooks",
    },
    "kilocode": {
        "source": "opencode_plugin.ts",
        "target_fn": "_install_kilocode_hooks",
    },
}


def _get_hook_config_path(filename: str) -> Path:
    """Get the path to a bundled hook config file."""
    return Path(__file__).parent / "hooks_configs" / filename


def _merge_claude_hooks(settings_path: Path, hook_config: dict) -> bool:
    """Merge LiteHarness hooks into Claude Code settings.json without clobbering.

    Claude Code format: each event is an array of matcher objects, each with a
    "hooks" array of {type, command, timeout} entries:
      "SessionStart": [{"hooks": [{"type": "command", "command": "...", "timeout": 5000}]}]
    """
    try:
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        else:
            settings = {}

        existing_hooks = settings.get("hooks", {})
        new_hooks = hook_config.get("hooks", {})
        marker = "liteharness.hooks"

        # ── Heal a settings.json poisoned by an earlier release ────────────────────────
        #
        # This function only ever APPENDED, so a hook installed by a bad release stayed
        # forever and no upgrade could remove it. 0.2.7 registered `checkpoint-save` and
        # `checkpoint-restore`, which nothing implements; every box that ran its `init` then
        # failed two hooks on EVERY COMPACT, permanently, and installing 0.2.8 fixed the
        # shipped config while leaving the user's file broken. Measured on a virgin sandbox
        # 2026-08-10: after upgrading, settings.json:76 and :97 still carried both.
        #
        # Prune is narrow on purpose. It removes only entries whose command invokes
        # `liteharness.hooks <action>` with an action this package does not implement — the
        # same predicate the dispatcher uses to reject it at runtime, so anything removed
        # here is provably incapable of doing anything but fail. Hooks belonging to other
        # tools, and our own valid hooks, are untouched.
        import re as _re

        from . import hooks as _hooks  # local import: hooks imports config, cli imports both

        pruned: list[str] = []
        for event in list(existing_hooks.keys()):
            kept_matchers = []
            for matcher_obj in existing_hooks[event] or []:
                inner = []
                for h in matcher_obj.get("hooks", []):
                    cmd = h.get("command", "") or ""
                    found = _re.search(r"liteharness\.hooks\s+([a-z0-9\-]+)", cmd)
                    if found and found.group(1) not in _hooks.KNOWN_ACTIONS:
                        pruned.append(f"{event}:{found.group(1)}")
                        continue
                    inner.append(h)
                if inner:
                    matcher_obj["hooks"] = inner
                    kept_matchers.append(matcher_obj)
            if kept_matchers:
                existing_hooks[event] = kept_matchers
            else:
                del existing_hooks[event]
        if pruned:
            print(f"    Removed {len(pruned)} hook(s) for actions this version does not "
                  f"implement: {', '.join(pruned)}")

        for event, new_matchers in new_hooks.items():
            if event not in existing_hooks:
                existing_hooks[event] = []

            # Collect all existing commands across all matchers to avoid duplicates
            existing_cmds: set[str] = set()
            for matcher_obj in existing_hooks[event]:
                for h in matcher_obj.get("hooks", []):
                    existing_cmds.add(h.get("command", ""))

            # Add new matcher objects that contain liteharness hooks
            for new_matcher in new_matchers:
                for h in new_matcher.get("hooks", []):
                    cmd = h.get("command", "")
                    if marker in cmd and cmd not in existing_cmds:
                        existing_hooks[event].append(new_matcher)
                        existing_cmds.add(cmd)
                        break  # Don't add same matcher twice

        settings["hooks"] = existing_hooks
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        print(f"    Warning: Could not update {settings_path}: {e}")
        return False


_DEFAULT_STATUSLINE = {
    "type": "command",
    "command": "python -m liteharness.statusline",
    "refreshInterval": 5,
}


def _merge_claude_statusline(settings_path: Path) -> str:
    """Install the status line into Claude Code settings.json.

    Returns "installed" | "kept" | "failed".

    🔴 NEVER clobbers an existing statusLine. Someone who has written their own has
    invested in it, and silently replacing it during an unrelated hook install is the
    kind of thing that makes people distrust an installer permanently. Absent is the
    only state we write into.

    This exists because putting "statusLine" in the plugin's own settings.json does
    NOTHING: nothing reads that file. Measured 2026-08-10 — the plugin has shipped
    "subagentStatusLine": true for months and it is absent from every user
    settings.json, because the plugin manifest does not reference settings.json and no
    code merges it. The visible consequence was that no installed LiteSuite has ever
    shown a status line at all.
    """
    try:
        settings = (
            json.loads(settings_path.read_text(encoding="utf-8"))
            if settings_path.exists()
            else {}
        )
        existing = settings.get("statusLine")
        # PRESENT means occupied. Full stop.
        #
        # This was `isinstance(existing, dict) and existing.get("command")`, which treated
        # anything that was not a dict-with-a-truthy-command as ABSENT and overwrote it while
        # reporting "installed". An agent on a virgin box planted values and destroyed two of
        # them (2026-08-10, liteharness 0.2.7):
        #     statusLine: "my-legacy-string-statusline.sh"      -> CLOBBERED
        #     statusLine: {"type":"command","MY_CONFIG":"..."}  -> CLOBBERED
        # The file is rewritten in place with no backup, so the user's value is unrecoverable.
        #
        # My own contract two paragraphs up says "Absent is the only state we write into".
        # Present-and-a-string is not absent. Present-without-a-command is not absent. A guard
        # whose predicate is narrower than the promise it makes is worse than no guard, because
        # it is trusted.
        if existing is not None:
            return "kept"
        settings["statusLine"] = dict(_DEFAULT_STATUSLINE)
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return "installed"
    except Exception as e:
        print(f"    Warning: could not install status line: {e}")
        return "failed"


def _install_claude_hooks() -> bool:
    """Install hooks into Claude Code settings.json."""
    settings_path = Path.home() / ".claude" / "settings.json"
    config_src = _get_hook_config_path("claude_hooks.json")
    if not config_src.exists():
        return False
    hook_config = json.loads(config_src.read_text(encoding="utf-8"))
    ok = _merge_claude_hooks(settings_path, hook_config)
    # Order matters: hooks first, so a status-line failure can never cost the user
    # their hooks. The status line is cosmetic; the hooks are the product.
    status = _merge_claude_statusline(settings_path)
    if status == "installed":
        print("    Status line installed")
    elif status == "kept":
        print("    Status line: kept your existing one")
    return ok


def _install_codex_hooks() -> bool:
    """Install hooks.json for Codex CLI."""
    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    target = codex_dir / "hooks.json"

    config_src = _get_hook_config_path("codex_hooks.json")
    if not config_src.exists():
        return False

    if target.exists():
        # Merge rather than overwrite
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            new_config = json.loads(config_src.read_text(encoding="utf-8"))
            markers = ("python -m liteharness.hooks", "python -m liteharness.codex_hooks")

            existing_hooks = existing.get("hooks", {})
            new_hooks = new_config.get("hooks", {})

            for event, matchers in new_hooks.items():
                if event not in existing_hooks:
                    existing_hooks[event] = matchers
                else:
                    preserved_matchers = []
                    for matcher in existing_hooks[event]:
                        remaining_hooks = [
                            hook
                            for hook in matcher.get("hooks", [])
                            if not any(marker in hook.get("command", "") for marker in markers)
                        ]
                        if remaining_hooks:
                            updated_matcher = dict(matcher)
                            updated_matcher["hooks"] = remaining_hooks
                            preserved_matchers.append(updated_matcher)
                    existing_hooks[event] = preserved_matchers + matchers

            existing["hooks"] = existing_hooks
            target.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            return True
        except Exception:
            pass

    shutil.copy2(config_src, target)
    return True


def _install_copilot_hooks() -> bool:
    """Install hooks for GitHub Copilot CLI.

    Copilot CLI reads hooks from .github/hooks/ in CWD (project-level only).
    We install to CWD if it's a git repo, and also to ~/.github/hooks/ as a
    reference copy the user can symlink or copy into other projects.
    """
    config_src = _get_hook_config_path("copilot_hooks.json")
    if not config_src.exists():
        return False

    installed = False

    # Install to CWD project if it's a git repo
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        project_hooks = cwd / ".github" / "hooks"
        project_hooks.mkdir(parents=True, exist_ok=True)
        target = project_hooks / "liteharness.json"
        if not target.exists():
            shutil.copy2(config_src, target)
            installed = True

    # Also keep a global reference copy
    global_hooks = Path.home() / ".github" / "hooks"
    global_hooks.mkdir(parents=True, exist_ok=True)
    global_target = global_hooks / "liteharness.json"
    shutil.copy2(config_src, global_target)
    installed = True

    return installed


def _install_opencode_hooks() -> bool:
    """Install plugin for OpenCode."""
    plugin_dir = Path.home() / ".config" / "opencode" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = plugin_dir / "liteharness.ts"

    config_src = _get_hook_config_path("opencode_plugin.ts")
    if not config_src.exists():
        return False

    shutil.copy2(config_src, target)
    return True


def _install_kilocode_hooks() -> bool:
    """Install plugin for KiloCode."""
    # KiloCode uses the same OpenCode plugin format
    plugin_dir = Path.home() / ".kilocode" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    target = plugin_dir / "liteharness.ts"

    config_src = _get_hook_config_path("opencode_plugin.ts")
    if not config_src.exists():
        return False

    shutil.copy2(config_src, target)
    return True


def _install_litecode_hooks() -> bool:
    """Install hooks for LiteCode v1 (Rust CLI).

    LiteCode v1 reads hooks from plugin directories:
      ~/.litecode/plugins/<name>/hooks/hooks.json  (global)
      .litecode/plugins/<name>/hooks/hooks.json    (project)
    It also honors Claude-compatible paths:
      ~/.claude/plugins/<name>/hooks/hooks.json
    A plugin.json manifest must exist alongside the hooks dir.
    """
    config_src = _get_hook_config_path("codex_hooks.json")  # Same format as Codex
    if not config_src.exists():
        return False

    plugin_dir = Path.home() / ".litecode" / "plugins" / "liteharness"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir = plugin_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # Write plugin.json manifest
    manifest = {
        "name": "liteharness",
        "version": "0.1.0",
        "description": "Cross-CLI inter-agent messaging via LiteHarness"
    }
    manifest_path = plugin_dir / "plugin.json"
    if not manifest_path.exists():
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Write hooks.json
    target = hooks_dir / "hooks.json"
    shutil.copy2(config_src, target)
    return True


def _get_cli_scripts_dir(cli_name: str) -> Path:
    """Get the path to bundled CLI-specific watcher scripts."""
    mapping = {"codex-cli": "codex", "copilot-cli": "copilot"}
    subdir = mapping.get(cli_name)
    if not subdir:
        return Path()
    return Path(__file__).parent / "cli_scripts" / subdir


def _install_cli_scripts(cli_name: str) -> bool:
    """Copy watcher/supervisor scripts to the CLI's canonical dotfolder."""
    src_dir = _get_cli_scripts_dir(cli_name)
    if not src_dir.exists():
        return False

    target_dirs = {
        "codex-cli": Path.home() / ".codex" / "skills" / "liteharness" / "scripts",
        "copilot-cli": Path.home() / ".copilot" / "skills" / "liteharness" / "scripts",
    }
    target_dir = target_dirs.get(cli_name)
    if not target_dir:
        return False

    target_dir.mkdir(parents=True, exist_ok=True)

    state_dirs = {
        "codex-cli": Path.home() / ".codex" / "memories" / "liteharness",
        "copilot-cli": Path.home() / ".copilot" / "skills" / "liteharness" / "state",
    }
    state_dir = state_dirs.get(cli_name)
    if state_dir:
        state_dir.mkdir(parents=True, exist_ok=True)

    installed = 0
    for src_file in src_dir.glob("*.py"):
        if src_file.name == "__init__.py":
            continue
        dest = target_dir / src_file.name
        if dest.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = dest.with_suffix(f".bak.{ts}")
            shutil.copy2(dest, backup)
        shutil.copy2(src_file, dest)
        installed += 1

    return installed > 0


_INSTALLERS = {
    "claude-code": _install_claude_hooks,
    "codex-cli": _install_codex_hooks,
    "copilot-cli": _install_copilot_hooks,
    "opencode": _install_opencode_hooks,
    "kilocode": _install_kilocode_hooks,
    "litecode": _install_litecode_hooks,
}

_SCRIPT_CLIS = {"codex-cli", "copilot-cli"}


def cmd_init() -> None:
    """Initialize LiteHarness: create dirs, detect CLIs, install hooks."""
    print("Initializing LiteHarness...")

    # Create directory structure
    config.ensure_root()
    inbox.ensure_dirs()

    root = config.get_root()
    for subdir in ("agents", "tasks", "checkpoints", "patterns"):
        (root / subdir).mkdir(parents=True, exist_ok=True)

    # Detect CLIs
    clis = config.detect_installed_clis()

    # Save config
    cfg = config.load()
    cfg["initialized_at"] = datetime.now(timezone.utc).isoformat()
    cfg["detected_clis"] = clis
    cfg["agent_id"] = config.get_agent_id()
    config.save(cfg)

    print(f"  Root: {root}")
    print(f"  Inbox: {inbox.INBOX_ROOT}")
    print(f"  Agent ID: {cfg['agent_id']}")
    print(f"  Detected CLIs: {', '.join(clis) if clis else 'none'}")
    print()

    # Auto-install hooks for all detected CLIs
    installed = []
    skipped = []
    for cli_name in clis:
        installer = _INSTALLERS.get(cli_name)
        if installer:
            print(f"  Installing hooks for {cli_name}...", end=" ")
            try:
                if installer():
                    print("OK")
                    installed.append(cli_name)
                else:
                    print("skipped (config not found)")
                    skipped.append(cli_name)
            except Exception as e:
                print(f"FAILED: {e}")
                skipped.append(cli_name)
        else:
            skipped.append(cli_name)

    # Install watcher/supervisor scripts for CLIs that need stdin injection
    scripts_installed = []
    for cli_name in clis:
        if cli_name in _SCRIPT_CLIS:
            print(f"  Installing inbox watcher scripts for {cli_name}...", end=" ")
            try:
                if _install_cli_scripts(cli_name):
                    print("OK")
                    scripts_installed.append(cli_name)
                else:
                    print("skipped (scripts not found)")
            except Exception as e:
                print(f"FAILED: {e}")

    print()
    if installed:
        print(f"  Hooks installed for: {', '.join(installed)}")
    if scripts_installed:
        print(f"  Watcher scripts installed for: {', '.join(scripts_installed)}")
    if skipped:
        print(f"  No hooks available for: {', '.join(skipped)}")
    print()
    print("Done. Run 'liteharness status' to verify.")


def cmd_status() -> None:
    """Show LiteHarness status."""
    cfg = config.load()
    root = config.get_root()

    print("LiteHarness Status")
    print(f"  Root: {root}")
    print(f"  Agent ID: {cfg.get('agent_id', 'not set')}")
    print(f"  CLIs: {', '.join(cfg.get('detected_clis', []))}")

    # Count messages
    new_count = len(list(inbox.INBOX_NEW.glob("*.json"))) if inbox.INBOX_NEW.exists() else 0
    cur_count = len(list(inbox.INBOX_CUR.glob("*.json"))) if inbox.INBOX_CUR.exists() else 0
    done_count = len(list(inbox.INBOX_DONE.glob("*.json"))) if inbox.INBOX_DONE.exists() else 0

    print(f"  Inbox: {new_count} new, {cur_count} in-progress, {done_count} done")

    # Count active agents
    agents_dir = root / "agents"
    if agents_dir.exists():
        now = time.time()
        active = 0
        for f in agents_dir.glob("*.json"):
            try:
                agent = json.loads(f.read_text(encoding="utf-8"))
                last_seen = datetime.fromisoformat(agent.get("last_seen", "")).timestamp()
                if now - last_seen < 43200:  # Active in last 12 hours
                    active += 1
            except (json.JSONDecodeError, OSError, ValueError):
                continue
        print(f"  Active agents: {active}")


def cmd_send(to: str, body: str, project: str | None = None, from_id: str | None = None, thread_id: str | None = None) -> None:
    """Send a message to another agent."""
    # `to` is POSITIONAL. Called flag-style (`send --to X --message Y`) it silently
    # accepts the literal "--to" as the recipient and swallows the real id into the
    # body — the message is written to the maildir, reported as "Sent", and then never
    # matches any agent because watch_inbox requires an exact id or "broadcast". It sits
    # in new/ forever, re-read on every poll by every watcher.
    #
    # This actually happened (2026-08-08): a substantive LB-4 briefing sat undelivered
    # for two hours with `to == "--to"`, and was only found by inspecting the maildir by
    # hand. Fail loudly at the call instead — an undeliverable address is not a warning.
    #
    # Ported here 2026-08-10 from the LiteSuite-bundled copy (LiteSuite 5bf93107), where
    # it had lived alone for two days. The two liteharness trees have diverged and each
    # held a different half of this fix: that tree had the recipient guard and index-based
    # flag consumption; this tree — the one `import liteharness` actually resolves to —
    # had neither, so the guard was invisible to every agent at runtime.
    if to.startswith("-"):
        print(
            f"Error: recipient looks like a flag ({to!r}). `send` takes POSITIONAL args:\n"
            f'  python -m liteharness.cli send <agent-id> "message" --from <your-id>\n'
            f"Nothing was sent.",
            file=sys.stderr,
        )
        sys.exit(1)
    agent_id = from_id or config.get_agent_id()
    resolved_thread = thread_id or os.environ.get("LITEHARNESS_THREAD_ID", "") or None
    msg_id = inbox.send(
        from_agent=agent_id,
        to_agent=to,
        body=body,
        project=project,
        thread_id=resolved_thread,
        cli=config.get_cli(),
        model=config.get_model(),
    )
    print(f"Sent message {msg_id[:8]} to {to}")


def cmd_list() -> None:
    """List messages in inbox."""
    agent_id = config.get_agent_id()
    messages = inbox.poll(agent_id)

    if not messages:
        print("No messages.")
        return

    print(f"{len(messages)} message(s):")
    for msg in messages:
        sender = msg.get("from", "?")
        priority = msg.get("priority", "normal")
        body = msg.get("body", "")[:80]
        ts = msg.get("timestamp", "")[:19]
        prefix = "[!] " if priority == "urgent" else "    "
        print(f"{prefix}{ts} from {sender}: {body}")


VALID_TIERS = ("orchestrator", "leader", "worker", "thinker", "reviewer")


def cmd_register(
    agent_id: str,
    cli: str | None = None,
    model: str | None = None,
    name: str | None = None,
    tier: str | None = None,
    team: str | None = None,
    pane_id: str | None = None,
    leaf_id: str | None = None,
    session_id: str | None = None,
    thread_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> None:
    """Update an agent's presence file with correct CLI/model/name/tier/team and spatial info."""
    from . import naming

    agents_dir = config.get_root() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / f"{agent_id}.json"

    if path.exists():
        try:
            presence = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            presence = {}
    else:
        presence = {"agent_id": agent_id, "started_at": datetime.now(timezone.utc).isoformat()}

    if cli:
        presence["cli"] = cli
    if model:
        presence["model"] = model

    if tier and tier in VALID_TIERS:
        presence["tier"] = tier
    elif tier:
        print(f"Warning: invalid tier '{tier}', must be one of {VALID_TIERS}. Defaulting to 'worker'.")
        presence.setdefault("tier", "worker")
    elif "tier" not in presence:
        presence["tier"] = "worker"

    if team:
        presence["team"] = team

    # Spatial awareness fields — store in a 'spatial' subobject
    spatial: dict[str, str] = presence.get("spatial", {})
    if pane_id:
        spatial["pane_id"] = pane_id
    if leaf_id:
        spatial["leaf_id"] = leaf_id
    if session_id:
        spatial["session_id"] = session_id
    if thread_id:
        spatial["thread_id"] = thread_id
    if workspace_id:
        spatial["workspace_id"] = workspace_id
    if project_id:
        spatial["project_id"] = project_id
    if spatial:
        presence["spatial"] = spatial

    # Name handling: override > existing override > generated
    if name:
        taken_by = naming.is_name_taken(name, exclude_id=agent_id)
        if taken_by:
            print(f"Warning: name '{name}' is already used by {taken_by[:8]}. Picking a different name.")
            name = None
        else:
            naming.set_override(agent_id, name)

    resolved_name = naming.get_name(agent_id)
    presence["name"] = resolved_name
    presence["last_seen"] = datetime.now(timezone.utc).isoformat()

    path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
    team_str = f", team={presence['team']}" if presence.get("team") else ""
    spatial_str = f", pane={pane_id}" if pane_id else ""
    print(f"Registered agent {agent_id}: cli={presence.get('cli', '?')}, model={presence.get('model', '?')}, tier={presence.get('tier', 'worker')}, name={resolved_name}{team_str}{spatial_str}")


def _pattern_db_open(db_path: Path):
    """Open (or create) the FTS5-backed patterns SQLite DB. Returns a connection."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS patterns USING fts5(
            description,
            reason,
            lesson,
            id UNINDEXED,
            timestamp UNINDEXED,
            outcome UNINDEXED,
            complexity UNINDEXED,
            tokenize="unicode61"
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    return conn


def _pattern_db_sync(conn, patterns_path: Path) -> None:
    """Sync JSONL write-ahead log into FTS5. Skips re-index when file is unchanged."""
    import sqlite3
    stat = patterns_path.stat()
    mtime_str = str(stat.st_mtime)
    size_str = str(stat.st_size)

    row = conn.execute(
        "SELECT value FROM patterns_meta WHERE key='jsonl_mtime'"
    ).fetchone()
    cached_mtime = row[0] if row else None
    row = conn.execute(
        "SELECT value FROM patterns_meta WHERE key='jsonl_size'"
    ).fetchone()
    cached_size = row[0] if row else None

    if cached_mtime == mtime_str and cached_size == size_str:
        return  # nothing changed

    entries = []
    for line in patterns_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    conn.execute("DELETE FROM patterns")
    conn.executemany(
        "INSERT INTO patterns(description, reason, lesson, id, timestamp, outcome, complexity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                e.get("description", ""),
                e.get("reason", ""),
                e.get("lesson", ""),
                e.get("id", ""),
                e.get("timestamp", ""),
                e.get("outcome", ""),
                e.get("complexity", ""),
            )
            for e in entries
        ],
    )
    conn.execute(
        "INSERT OR REPLACE INTO patterns_meta(key, value) VALUES('jsonl_mtime', ?)",
        (mtime_str,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO patterns_meta(key, value) VALUES('jsonl_size', ?)",
        (size_str,),
    )
    conn.commit()


def _fts5_phrase_query(raw: str) -> str:
    """Turn an arbitrary user string into a safe FTS5 phrase query.

    FTS5 has many operators (-, OR, AND, NOT, NEAR, column:, parens, quotes)
    that turn a user's literal query into a syntax error or wrong match.
    Wrapping every whitespace-separated token in double quotes treats each
    one as a literal phrase and disables operator parsing. Double quotes
    inside a token are doubled, per FTS5 quoting rules.
    """
    tokens = [t for t in raw.split() if t]
    if not tokens:
        return ""
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def _pattern_fts5_query(conn, query: str | None, top: int) -> list[dict]:
    """Run FTS5 query (or full scan when query is None). Returns dicts sorted by recency."""
    if query:
        safe_q = _fts5_phrase_query(query)
        if not safe_q:
            return []
        rows = conn.execute(
            "SELECT description, reason, lesson, id, timestamp, outcome, complexity "
            "FROM patterns WHERE patterns MATCH ? "
            "ORDER BY timestamp DESC LIMIT ?",
            (safe_q, top),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT description, reason, lesson, id, timestamp, outcome, complexity "
            "FROM patterns ORDER BY timestamp DESC LIMIT ?",
            (top,),
        ).fetchall()

    return [
        {
            "description": r[0],
            "reason": r[1],
            "lesson": r[2],
            "id": r[3],
            "timestamp": r[4],
            "outcome": r[5],
            "complexity": r[6],
        }
        for r in rows
    ]


def _print_patterns(entries: list[dict], fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print("No matching patterns.")
        return
    for e in entries:
        outcome = e.get("outcome", "?")
        desc = e.get("description", "no description")
        ts = e.get("timestamp", "")[:19]
        complexity = e.get("complexity", "?")
        marker = "+" if outcome == "success" else "-" if outcome == "failure" else "?"
        try:
            print(f"  [{marker}] {ts} ({complexity}) {desc}")
        except UnicodeEncodeError:
            print(f"  [{marker}] {ts} ({complexity}) {desc.encode('ascii', 'replace').decode()}")
        if e.get("reason"):
            print(f"       Reason: {e['reason']}")
        if e.get("lesson"):
            print(f"       Lesson: {e['lesson']}")


def cmd_query_patterns(
    top: int = 5,
    fmt: str = "text",
    query: str | None = None,
    project: str | None = None,
) -> None:
    """Query patterns from .liteharness/patterns.jsonl via FTS5-backed SQLite index.

    FTS5 is the primary path — syncs from the JSONL write-ahead log on demand,
    skipping re-index when the file is unchanged. Falls back to a JSONL substring
    scan when SQLite is unavailable or the DB cannot be opened.
    """
    project_root = project or os.getcwd()
    patterns_path = Path(project_root) / ".liteharness" / "patterns.jsonl"

    if not patterns_path.exists():
        if fmt == "json":
            print("[]")
        else:
            print("No patterns found.")
        return

    db_path = Path(project_root) / ".liteharness" / "patterns.db"

    # FTS5 primary path
    try:
        import sqlite3 as _sqlite3
        conn = _pattern_db_open(db_path)
        try:
            _pattern_db_sync(conn, patterns_path)
            entries = _pattern_fts5_query(conn, query, top)
        finally:
            conn.close()
        _print_patterns(entries, fmt)
        return
    except _sqlite3.OperationalError as exc:
        print(f"[query-patterns] FTS5 path failed, falling back to JSONL: {exc}", file=sys.stderr)
    except ImportError:
        # sqlite3 unavailable in this Python build — silent fallback is fine
        pass
    except Exception as exc:
        print(f"[query-patterns] unexpected FTS5 error, falling back to JSONL: {exc!r}", file=sys.stderr)

    # JSONL fallback — substring scan (used when sqlite3 / FTS5 unavailable)
    entries = []
    for line in patterns_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if query:
        lower_q = query.lower()
        entries = [
            e for e in entries
            if lower_q in e.get("description", "").lower()
            or lower_q in e.get("reason", "").lower()
            or lower_q in e.get("lesson", "").lower()
        ]

    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    _print_patterns(entries[:top], fmt)


def cmd_embed_query(
    query: str,
    top: int = 5,
    fmt: str = "text",
) -> None:
    """Hybrid RAG query: embed the query via :7439, call harness MCP for hybrid results.

    Requires HARNESS_MCP_PORT env var (set by orchestrator). Falls back to
    cmd_query_patterns (BM25 scan) if MCP port is unavailable.
    """
    import urllib.request

    mcp_port = os.environ.get("HARNESS_MCP_PORT")
    if not mcp_port:
        # Standalone use — fall back to BM25 scan
        cmd_query_patterns(top=top, fmt=fmt, query=query)
        return

    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "harness_query_patterns",
            "arguments": {"query": query, "limit": top},
        },
    }).encode()

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{mcp_port}/",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp_body = urllib.request.urlopen(req, timeout=10).read()
        rpc = json.loads(resp_body)
        text_content = rpc.get("result", {}).get("content", [{}])[0].get("text", "[]")
        entries = json.loads(text_content)
    except Exception as exc:
        print(f"[embed-query] MCP call failed: {exc}", file=sys.stderr)
        cmd_query_patterns(top=top, fmt=fmt, query=query)
        return

    if fmt == "json":
        print(json.dumps(entries, indent=2))
        return

    if not entries:
        print("No matching patterns.")
        return

    for e in entries:
        if isinstance(e, dict) and "pattern" in e:
            pat = e["pattern"]
            outcome = pat.get("outcome", "?")
            desc = pat.get("description", pat.get("approach", "no description"))
            ts = pat.get("createdAt", "")[:19]
            complexity = pat.get("complexity", "?")
        else:
            continue
        marker = "+" if outcome == "success" else "-" if outcome == "failure" else "?"
        print(f"  [{marker}] {ts} ({complexity}) {desc}")


def cmd_record_pattern(
    outcome: str = "unknown",
    agent_id: str | None = None,
    task_desc: str | None = None,
    project: str | None = None,
) -> None:
    """Record a pattern to .liteharness/patterns.jsonl in the current project."""
    import subprocess

    project_root = project or os.getcwd()
    patterns_dir = Path(project_root) / ".liteharness"
    patterns_dir.mkdir(parents=True, exist_ok=True)
    patterns_path = patterns_dir / "patterns.jsonl"

    # Read task description from stdin if not provided
    if not task_desc:
        if not sys.stdin.isatty():
            task_desc = sys.stdin.read().strip()
        if not task_desc:
            task_desc = "unspecified task"

    # Get git diff summary if in a git repo
    git_summary = ""
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=project_root,
        )
        if result.returncode == 0 and result.stdout.strip():
            git_summary = result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    # Map extended outcomes to the schema's allowed values
    schema_outcome = outcome
    if outcome in ("stuck", "unknown", "blocked"):
        schema_outcome = "failure"

    entry = {
        "task_id": f"{agent_id or 'unknown'}-{int(time.time())}",
        "session": agent_id or "cli",
        "outcome": schema_outcome,
        "complexity": "medium",
        "description": task_desc,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if outcome in ("failure", "stuck"):
        entry["reason"] = f"Outcome was {outcome}"
    if git_summary:
        entry["description"] = f"{task_desc}\n\nGit diff summary:\n{git_summary}"

    line = json.dumps(entry) + "\n"
    with open(patterns_path, "a", encoding="utf-8") as f:
        f.write(line)

    print(f"Recorded pattern: {schema_outcome} for {entry['task_id']}")


HARNESS_VERSION = "0.2.0"

BOOTSTRAP_HARNESS_SECTION_START = "<!-- LiteHarness Bootstrap - DO NOT EDIT THIS SECTION -->"
BOOTSTRAP_HARNESS_SECTION_END = "<!-- End LiteHarness Bootstrap -->"

BOOTSTRAP_HARNESS_CONTENT = """\
# LiteHarness — Active

This project uses [LiteHarness](https://litesuite.dev) — a five-tier AI agent orchestration system.

## Quick Start

- **Config:** `.liteharness/config.yaml`
- **Patterns:** `.liteharness/patterns.jsonl`
- **Tools:** `lst run <tool> action=<action>` (CLI) or MCP tool calls (inside LiteSuite)
- **Lifecycle:** `liteharness spawn/discover/send-input/read-output` (agent process management)

## Session Start

1. Read `.liteharness/config.yaml`
2. Run `lst run environment` for project context
3. Run `lst run pattern action=query query="<task>"` for relevant history
4. Use `liteharness spawn` to create workers, `lst run tasks` to track status
"""


def cmd_bootstrap(project_path: str) -> None:
    """Bootstrap harness for a project (global init + per-project scaffold)."""
    project = Path(project_path).resolve()
    if not project.is_dir():
        print(f"Error: {project} is not a directory")
        sys.exit(1)

    print(f"Bootstrapping LiteHarness for {project}")
    print()

    # Step 1: Global init (hooks, inbox, config) — skip if already done
    cfg = config.load()
    if not cfg.get("initialized_at"):
        print("Step 1: Global init...")
        cmd_init()
        print()
    else:
        print("Step 1: Global init already done, skipping.")
        print()

    # Step 2: Verify lst is on PATH
    print("Step 2: Verifying lst CLI on PATH...")
    lst_path = shutil.which("lst")
    if lst_path:
        print(f"  lst found: {lst_path}")
    else:
        print("  WARNING: lst not found on PATH. MCP tools will not work.")
        print("  Install with: pip install -e packages/litesuite-tools")
    print()

    # Step 3: Create .liteharness/ scaffold
    print("Step 3: Creating .liteharness/ scaffold...")
    harness_dir = project / ".liteharness"
    for sub in ("agents", "plans", "sessions", "prompts", "hooks"):
        (harness_dir / sub).mkdir(parents=True, exist_ok=True)

    patterns_path = harness_dir / "patterns.jsonl"
    if not patterns_path.exists():
        patterns_path.write_text("", encoding="utf-8")
        print("  Created patterns.jsonl")

    gitignore_path = harness_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("sessions/\nagents/\n", encoding="utf-8")
        print("  Created .liteharness/.gitignore")

    print(f"  Scaffold created at {harness_dir}")
    print()

    # Step 4: Generate/merge .mcp.json (merge, not overwrite)
    print("Step 4: Generating .mcp.json...")
    mcp_path = project / ".mcp.json"
    mcp_entry = {
        "command": "lst",
        "args": ["serve", "--mcp"],
        "env": {"LITESUITE_CWD": str(project)},
    }

    if mcp_path.exists():
        try:
            existing = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        servers = existing.get("mcpServers", {})
        servers["litesuite-tools"] = mcp_entry
        existing["mcpServers"] = servers
        mcp_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print("  Merged litesuite-tools into existing .mcp.json")
    else:
        mcp_path.write_text(
            json.dumps({"mcpServers": {"litesuite-tools": mcp_entry}}, indent=2),
            encoding="utf-8",
        )
        print("  Created .mcp.json")

    # Ensure .mcp.json is gitignored (machine-local paths)
    root_gitignore = project / ".gitignore"
    if root_gitignore.exists():
        gi_content = root_gitignore.read_text(encoding="utf-8")
        if ".mcp.json" not in gi_content:
            with open(root_gitignore, "a", encoding="utf-8") as f:
                f.write("\n# LiteHarness — machine-local MCP config\n.mcp.json\n")
            print("  Added .mcp.json to .gitignore")
    print()

    # Step 5: Inject harness section into CLAUDE.md (idempotent)
    print("Step 5: Updating CLAUDE.md...")
    harness_section = (
        BOOTSTRAP_HARNESS_SECTION_START + "\n\n"
        + BOOTSTRAP_HARNESS_CONTENT
        + BOOTSTRAP_HARNESS_SECTION_END
    )

    for filename in ("CLAUDE.md", "AGENTS.md"):
        filepath = project / filename
        if filepath.exists():
            existing = filepath.read_text(encoding="utf-8")
            start_idx = existing.find(BOOTSTRAP_HARNESS_SECTION_START)
            end_idx = existing.find(BOOTSTRAP_HARNESS_SECTION_END)
            if start_idx != -1 and end_idx != -1:
                before = existing[:start_idx]
                after = existing[end_idx + len(BOOTSTRAP_HARNESS_SECTION_END):]
                filepath.write_text(before + harness_section + after, encoding="utf-8")
                print(f"  Updated harness section in {filename}")
            else:
                filepath.write_text(harness_section + "\n\n" + existing, encoding="utf-8")
                print(f"  Prepended harness section to {filename}")
        else:
            filepath.write_text(harness_section + "\n", encoding="utf-8")
            print(f"  Created {filename}")
    print()

    # Step 6: Save version stamp
    cfg = config.load()
    cfg["version"] = HARNESS_VERSION
    cfg["last_bootstrap"] = datetime.now(timezone.utc).isoformat()
    config.save(cfg)

    print("=" * 50)
    print("  LiteHarness bootstrap complete!")
    print(f"  Project: {project}")
    print(f"  Version: {HARNESS_VERSION}")
    if not lst_path:
        print("  WARNING: lst not on PATH — install litesuite-tools")
    print("=" * 50)


MODEL_ALIASES = {
    "opus": "claude-opus-4-8[1m]",
    "opus-1m": "claude-opus-4-8[1m]",
    "opus-200k": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


def _build_claude_cmd(
    model: str | None = None,
    permission_mode: str | None = None,
    additional_args: str | None = None,
    prompt: str | None = None,
    name: str | None = None,
) -> str:
    """Build the claude CLI command string with bootstrap instructions."""
    resolved_model = MODEL_ALIASES.get(model, model) if model else None

    bootstrap = (
        "MANDATORY FIRST STEPS: "
        "1) Run /liteharness to load the harness skill. "
        "2) Register yourself: python -m liteharness.cli register --agent-id <YOUR-AGENT-ID> --cli claude-code --model <your-model>"
    )
    if name:
        bootstrap += f' --name "{name}"'
    bootstrap += (
        ". 3) Start your inbox monitor: use the Monitor tool with command "
        "'python -m liteharness.hooks watch --agent-id <YOUR-AGENT-ID>'. "
        "4) Pick a unique name for yourself (not Claude/Assistant) and use it when registering. "
    )
    if prompt:
        bootstrap += f"THEN: {prompt}"

    parts = ["claude"]
    if resolved_model:
        parts.extend(["--model", resolved_model])
    effective_perm = permission_mode or "bypassPermissions"
    parts.extend(["--permission-mode", effective_perm])
    if additional_args:
        parts.append(additional_args)
    parts.append(f'"{bootstrap}"')

    return " ".join(parts), resolved_model


def _handle_worktree(target_dir: str) -> str:
    """Create a git worktree and return the new path."""
    import subprocess
    try:
        worktree_base = Path(target_dir) / ".worktrees"
        branch_name = f"spawn-{int(time.time())}"
        worktree_path = worktree_base / branch_name
        subprocess.run(
            ["git", "worktree", "add", "-b", branch_name, str(worktree_path)],
            cwd=target_dir, check=True, capture_output=True, text=True,
        )
        print(f"  Created worktree: {worktree_path} (branch: {branch_name})")
        return str(worktree_path)
    except subprocess.CalledProcessError as e:
        print(f"  Warning: worktree creation failed: {e.stderr.strip()}")
        return target_dir


def _generate_bootstrap(
    resolved_model: str | None,
    name: str | None,
    prompt: str | None,
    target_dir: str,
    context_env: dict[str, str],
) -> str:
    """Generate a context-rich bootstrap prompt for spawned agents."""
    import subprocess as _sp

    lines: list[str] = []
    lines.append("## Agent Context")

    # Project + CWD
    project_name = os.path.basename(target_dir)
    lines.append(f"Project: {project_name}")
    lines.append(f"CWD: {target_dir}")

    # Thread (if set)
    thread_id = context_env.get("LITEHARNESS_THREAD_ID", "")
    if thread_id:
        lines.append(f"Thread ID: {thread_id}")

    # Git context
    try:
        branch = _sp.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=target_dir, text=True, timeout=5, stderr=_sp.DEVNULL,
        ).strip()
        commit = _sp.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=target_dir, text=True, timeout=5, stderr=_sp.DEVNULL,
        ).strip()
        lines.append(f"Git: {branch} @ {commit}")
    except Exception:
        pass

    # MCP tools (check .mcp.json)
    mcp_path = os.path.join(target_dir, ".mcp.json")
    if os.path.exists(mcp_path):
        try:
            with open(mcp_path) as f:
                mcp = json.load(f)
            servers = list(mcp.get("mcpServers", {}).keys())
            if servers:
                lines.append(f"MCP tools: {', '.join(servers)}")
        except Exception:
            pass

    lines.append("")
    lines.append("## Mandatory Startup")
    lines.append("1) Run /liteharness to load the harness skill.")
    reg_line = f"2) Register: python -m liteharness.cli register --agent-id <YOUR-AGENT-ID> --cli claude-code --model {resolved_model or 'unknown'}"
    if name:
        reg_line += f' --name "{name}"'
    lines.append(reg_line)
    lines.append("3) Start inbox monitor: Monitor({ description: \"LiteHarness inbox\", persistent: true, timeout_ms: 3600000, command: \"python -m liteharness.hooks watch --agent-id <YOUR-AGENT-ID>\" })")

    # Role-based memory injection
    tier = context_env.get("LITEHARNESS_TIER", "worker")
    project = os.path.basename(target_dir)
    try:
        from liteharness.rag.engine import litesuite_rag as _rag
        result = _rag(
            action="query_by_tier",
            query=f"{tier} patterns {project}",
            tier=tier,
            top_k=10,
            root=target_dir,
        )
        memories = result.get("matches", []) if result.get("ok") else []
        if memories:
            lines.append("")
            lines.append("## Agent Memory")
            lines.append(f"Context for your role ({tier}):")
            # Hard cap at 3 entries if overflow detected
            overflow = result.get("stats", {}).get("total_candidates", 0) > len(memories)
            display = memories[:3] if overflow else memories
            for mem in display:
                snippet = (mem.get("snippet") or "")[:200].replace("\n", " ")
                lines.append(f"- [{mem.get('path', '?')}:{mem.get('start_line', '?')}] {snippet}")
            if overflow:
                lines.append("")
                lines.append(f'For deeper context, use: liteharness rag query_by_tier --tier {tier} --query "<topic>"')
    except Exception:
        pass  # RAG unavailable — bootstrap without memory

    if prompt:
        lines.append("")
        lines.append("## Task")
        lines.append(prompt)

    return "\n".join(lines)


def cmd_spawn(
    model: str | None = None,
    cwd: str | None = None,
    worktree: bool = False,
    permission_mode: str | None = None,
    prompt: str | None = None,
    name: str | None = None,
    new_window: bool = True,
    additional_args: str | None = None,
    pty_mode: bool = False,
    exec_cmd: str | None = None,
    thread_id: str | None = None,
    workspace_id: str | None = None,
    project_id: str | None = None,
    tier: str | None = None,
    team: str | None = None,
) -> None:
    """Spawn a new Claude Code session.

    --pty: spawn via ConPTY daemon (enables send-input/read-output control)
    default: spawn via Windows Terminal (visible tab, no stdin control)
    """
    import subprocess

    target_dir = cwd or os.getcwd()
    if worktree:
        target_dir = _handle_worktree(target_dir)

    claude_str, resolved_model = _build_claude_cmd(model, permission_mode, additional_args, prompt, name)

    # Build context env vars to inject into the spawned agent's environment
    context_env: dict[str, str] = {}
    resolved_thread = thread_id or os.environ.get("LITEHARNESS_THREAD_ID", "")
    resolved_workspace = workspace_id or os.environ.get("LITEHARNESS_WORKSPACE_ID", "")
    resolved_project = project_id or os.environ.get("LITEHARNESS_PROJECT_ID", "")
    resolved_tier = tier or "worker"
    if resolved_thread:
        context_env["LITEHARNESS_THREAD_ID"] = resolved_thread
    if resolved_workspace:
        context_env["LITEHARNESS_WORKSPACE_ID"] = resolved_workspace
    if resolved_project:
        context_env["LITEHARNESS_PROJECT_ID"] = resolved_project
    context_env["LITEHARNESS_TIER"] = resolved_tier
    if team:
        context_env["LITEHARNESS_TEAM"] = team

    # Inside LiteSuite (Electron): route through canvas panel IPC when available
    spawn_mode = "canvas" if is_inside_litesuite() and not pty_mode else ("pty" if pty_mode else "terminal")

    if spawn_mode == "canvas":
        # Canvas mode: create a terminal pane inside LiteSuite via Agent Bridge.
        # The bridge's /pty/create creates a node-pty session + canvas panel.
        result = _bridge_request("POST", "/pty/create", {
            "shell": claude_str,
            "cwd": target_dir,
        })

        if not result.get("session_id"):
            print(f"Error: Canvas spawn failed — {result.get('error', 'no session_id returned')}")
            sys.exit(1)

        session_id = result["session_id"]
        agent_id = f"canvas-{session_id}"

        # Record spawn_mode in presence file so send-input auto-routes
        agents_dir = config.get_root() / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        presence_path = agents_dir / f"{agent_id}.json"
        presence_path.write_text(json.dumps({
            "agent_id": agent_id,
            "spawn_mode": "canvas",
            "canvas_session_id": session_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "model": resolved_model,
        }, indent=2), encoding="utf-8")

        print(f"Spawned Claude session (canvas mode):")
        print(f"  Agent ID: {agent_id}")
        print(f"  Canvas session: {session_id}")

        # Deliver rich bootstrap + prompt after startup delay
        bootstrap = _generate_bootstrap(resolved_model, name, prompt, target_dir, context_env)

        time.sleep(8)
        write_result = _bridge_request("POST", "/pty/write", {
            "session_id": session_id,
            "data": bootstrap + "\r",
        })
        if write_result.get("ok"):
            print("  Prompt delivered via canvas PTY")
    elif pty_mode:
        # PTY mode: spawn via ConPTY daemon for full stdin/stdout control.
        # Build a clean command without the prompt — the prompt is sent via
        # stdin after the session starts. This avoids nested-quote and
        # metacharacter issues in the daemon's command validation.
        from . import pty_daemon

        if not pty_daemon.ensure_daemon():
            print("Error: Could not start PTY daemon")
            sys.exit(1)

        if exec_cmd:
            pty_cmd = exec_cmd
        else:
            pty_parts = ["claude"]
            if resolved_model:
                pty_parts.extend(["--model", resolved_model])
            effective_perm = permission_mode or "bypassPermissions"
            pty_parts.extend(["--permission-mode", effective_perm])
            if additional_args:
                pty_parts.append(additional_args)
            pty_cmd = " ".join(pty_parts)

        agent_id = f"pty-{int(time.time())}-{os.getpid()}"
        result = pty_daemon.send_command({
            "cmd": "spawn",
            "agent_id": agent_id,
            "cli_cmd": pty_cmd,
            "cwd": target_dir,
            "name": name,
            "env": context_env,
        })

        if not result.get("ok"):
            print(f"Error: {result.get('error', 'unknown')}")
            sys.exit(1)

        # Pre-create presence file with spawn_mode so send-input auto-routes
        agents_dir = config.get_root() / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        presence_path = agents_dir / f"{agent_id}.json"
        presence_path.write_text(json.dumps({
            "agent_id": agent_id,
            "spawn_mode": "pty",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "model": resolved_model,
        }, indent=2), encoding="utf-8")

        kind = "process" if exec_cmd else "Claude session"
        print(f"Spawned {kind} (PTY mode):")
        print(f"  Agent ID: {agent_id}")
        print(f"  Directory: {target_dir}")
        if name:
            print(f"  Name: {name}")
        print(f"  Mode: PTY (use send-input/read-output to control)")

        if not exec_cmd:
            # Send rich bootstrap + prompt after a brief delay
            bootstrap = _generate_bootstrap(resolved_model, name, prompt, target_dir, context_env)

            time.sleep(8)
            send_result = pty_daemon.send_command({
                "cmd": "send_input",
                "agent_id": agent_id,
                "text": bootstrap,
            })
            if send_result.get("ok"):
                print("  Prompt delivered via stdin")
    else:
        # Terminal mode: spawn via Windows Terminal (visible, detached)
        if sys.platform == "win32":
            spawn_cmd = f'start wt -d "{target_dir}" cmd /k {claude_str}'
            subprocess.Popen(
                spawn_cmd, shell=True,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                f"cd {target_dir} && {claude_str}",
                shell=True, start_new_session=True,
            )
        print(f"Spawned Claude session:")

    print(f"  Directory: {target_dir}")
    if resolved_model:
        print(f"  Model: {resolved_model}")
    if permission_mode:
        print(f"  Permission mode: {permission_mode}")
    if worktree:
        print(f"  Worktree: yes")
    if name:
        print(f"  Name: {name}")
    if pty_mode:
        print(f"  Mode: PTY (use send-input/read-output to control)")
    if prompt:
        print(f"  Prompt: {prompt[:80]}")


def cmd_send_input(agent_id: str, text: str, headed: bool = False) -> None:
    """Send text to a session's stdin. Auto-detects canvas vs PTY vs headed mode."""
    if text.strip().lower() in ("/exit", "/exit\r", "/exit\n"):
        print("Error: /exit is blocked — use /clear to reset sessions, or pty-kill to terminate")
        sys.exit(1)
    if headed:
        from . import terminal_automation as wt

        parts = agent_id.split(":", 1)
        if len(parts) != 2:
            print("Error: --headed requires <window_handle>:<pane_id> as agent-id")
            sys.exit(1)
        handle, pane_id = int(parts[0]), int(parts[1])
        ok = wt.send_input(handle, pane_id, text, auto_enter=True)
        if ok:
            print(f"Sent to pane {handle}:{pane_id}: {text.strip()[:80]}")
        else:
            print("Error: pane not found or send failed")
            sys.exit(1)
        return

    # Auto-detect spawn mode from presence file
    spawn_mode = _get_agent_spawn_mode(agent_id)

    if spawn_mode == "canvas":
        # Canvas mode: route through LiteSuite Agent Bridge
        agents_dir = config.get_root() / "agents"
        presence_path = agents_dir / f"{agent_id}.json"
        try:
            presence = json.loads(presence_path.read_text(encoding="utf-8"))
            session_id = presence.get("canvas_session_id")
        except (json.JSONDecodeError, OSError):
            session_id = None

        if not session_id:
            print(f"Error: Agent {agent_id} has spawn_mode=canvas but no canvas_session_id")
            sys.exit(1)

        if not text.endswith("\r") and not text.endswith("\n"):
            text += "\r"

        result = _bridge_request("POST", "/pty/write", {
            "session_id": session_id,
            "data": text,
        })
        if result.get("ok"):
            print(f"Sent to {agent_id} (canvas): {text.strip()[:80]}")
        else:
            print(f"Error: {result.get('error', 'unknown')}")
            sys.exit(1)
        return

    # PTY mode (default for non-canvas)
    from . import pty_daemon

    if not pty_daemon.is_daemon_running():
        print("Error: PTY daemon is not running. Start with: liteharness pty-daemon")
        sys.exit(1)

    result = pty_daemon.send_command({
        "cmd": "send-input",
        "agent_id": agent_id,
        "text": text,
    })

    if result.get("ok"):
        print(f"Sent to {agent_id}: {text.strip()[:80]}")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        sys.exit(1)


def cmd_read_output(agent_id: str, lines: int = 50, headed: bool = False) -> None:
    """Read output from a session. Auto-detects canvas vs PTY vs headed mode."""
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if headed:
        from . import terminal_automation as wt

        parts = agent_id.split(":", 1)
        if len(parts) != 2:
            print("Error: --headed requires <window_handle>:<pane_id> as agent-id")
            sys.exit(1)
        handle, pane_id = int(parts[0]), int(parts[1])
        buf = wt.read_buffer(handle, pane_id)
        if buf is None:
            print("Error: pane not found or buffer empty")
            sys.exit(1)
        if lines > 0:
            buf_lines = buf.splitlines()
            print("\n".join(buf_lines[-lines:]))
        else:
            print(buf)
        return

    # Auto-detect spawn mode from presence file
    spawn_mode = _get_agent_spawn_mode(agent_id)

    if spawn_mode == "canvas":
        agents_dir = config.get_root() / "agents"
        presence_path = agents_dir / f"{agent_id}.json"
        try:
            presence = json.loads(presence_path.read_text(encoding="utf-8"))
            session_id = presence.get("canvas_session_id")
        except (json.JSONDecodeError, OSError):
            session_id = None

        if not session_id:
            print(f"Error: Agent {agent_id} has spawn_mode=canvas but no canvas_session_id")
            sys.exit(1)

        result = _bridge_request("POST", "/pty/read", {
            "session_id": session_id,
        })

        if result.get("output") is not None:
            output = result["output"]
            if lines > 0:
                out_lines = output.splitlines()
                print("\n".join(out_lines[-lines:]))
            else:
                print(output)
        elif result.get("error"):
            print(f"Error: {result['error']}")
            sys.exit(1)
        return

    # PTY mode (default for non-canvas)
    from . import pty_daemon

    if not pty_daemon.is_daemon_running():
        print("Error: PTY daemon is not running. Start with: liteharness pty-daemon")
        sys.exit(1)

    result = pty_daemon.send_command({
        "cmd": "read-output",
        "agent_id": agent_id,
        "lines": lines,
    })

    if result.get("ok"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(result.get("output", ""))
        if not result.get("alive"):
            print(f"\n[session {agent_id} has exited]")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        sys.exit(1)


def cmd_wt_list_panes(fmt: str = "text") -> None:
    """List all Windows Terminal panes via UIAutomation."""
    from . import terminal_automation as wt

    windows = wt.list_panes()
    if not windows:
        print("No Windows Terminal windows found.")
        return

    if fmt == "json":
        print(json.dumps(windows, indent=2))
        return

    for w in windows:
        print(f"Window {w['handle']} — \"{w.get('title', '')}\" (pid {w['pid']})")
        for pane in w.get("panes", []):
            focused = " [focused]" if pane.get("focused") else ""
            print(f"  Pane {pane['id']}: {pane.get('title', '')}{focused} ({pane.get('class_name', '')})")
        for shell in w.get("shells", []):
            print(f"  Shell: {shell.get('name', '')} (pid {shell.get('pid', '?')})")


def cmd_wt_focus(window_handle: int, pane_id: int) -> None:
    """Focus a specific Windows Terminal pane."""
    from . import terminal_automation as wt

    ok = wt.focus_pane(window_handle, pane_id)
    if ok:
        print(f"Focused pane {window_handle}:{pane_id}")
    else:
        print("Error: pane not found")
        sys.exit(1)


def cmd_codex_desktop_list(fmt: str = "text") -> None:
    """List visible Codex Desktop windows."""
    from . import desktop_automation

    windows = desktop_automation.list_codex_windows()
    if fmt == "json":
        print(json.dumps(windows, indent=2))
        return
    if not windows:
        print("No visible Codex Desktop windows found.")
        return
    for window in windows:
        rect = window.get("rect", {})
        print(
            f"Codex Desktop {window.get('handle')} "
            f"pid={window.get('pid')} title=\"{window.get('title', '')}\" "
            f"rect={rect.get('left')},{rect.get('top')} "
            f"{rect.get('width')}x{rect.get('height')}"
        )


def cmd_codex_desktop_send(
    text: str,
    *,
    submit: bool = True,
    restore_mouse: bool = True,
    window_handle: int | None = None,
    click_x: int | None = None,
    click_y: int | None = None,
    dry_run: bool = False,
    probe_only: bool = False,
) -> None:
    """Paste text into Codex Desktop, then restore the user's cursor."""
    from . import desktop_automation

    try:
        result = desktop_automation.send_to_codex_desktop(
            text,
            submit=submit,
            restore_mouse=restore_mouse,
            window_handle=window_handle,
            click_x=click_x,
            click_y=click_y,
            dry_run=dry_run,
            probe_only=probe_only,
        )
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    if not result or not result.get("ok"):
        print(f"Error: {(result or {}).get('error', 'unknown')}")
        sys.exit(1)

    click = result.get("click", {})
    window = result.get("window", {})
    mode = "Dry run" if dry_run else ("Probe" if probe_only else "Sent")
    print(
        f"{mode} for Codex Desktop {window.get('handle')} "
        f"at ({click.get('x')}, {click.get('y')}); "
        f"restore_mouse={result.get('restore_mouse', result.get('restored_mouse'))}"
    )


def cmd_codex_desktop_target(window_handle: int | None = None) -> None:
    """Mark Codex Desktop as the Codex inbox watcher's delivery target."""
    from . import desktop_automation

    def current_codex_agent_id() -> str:
        env_agent_id = os.environ.get("LITEHARNESS_AGENT_ID", "").strip()
        if env_agent_id.startswith("codex-"):
            return env_agent_id

        for env_name in ("CODEX_THREAD_ID", "WT_SESSION"):
            session_key = os.environ.get(env_name, "").strip()
            if not session_key:
                continue
            safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_key)
            session_path = config.get_root() / "codex_sessions" / f"{safe_key}.json"
            if not session_path.exists():
                continue
            try:
                data = json.loads(session_path.read_text(encoding="utf-8"))
                agent_id = data.get("agent_id")
                if isinstance(agent_id, str) and agent_id.startswith("codex-"):
                    return agent_id
            except (json.JSONDecodeError, OSError):
                pass

        codex_agent_path = config.get_root() / "codex_agent.json"
        if codex_agent_path.exists():
            try:
                data = json.loads(codex_agent_path.read_text(encoding="utf-8"))
                agent_id = data.get("agent_id")
                if isinstance(agent_id, str) and agent_id.startswith("codex-"):
                    return agent_id
            except (json.JSONDecodeError, OSError):
                pass
        return config.get_agent_id()

    windows = desktop_automation.list_codex_windows()
    if not windows:
        print("Error: no visible Codex Desktop window found")
        sys.exit(1)

    selected = None
    if window_handle is not None:
        for window in windows:
            if int(window.get("handle", 0)) == window_handle:
                selected = window
                break
        if selected is None:
            print(f"Error: Codex Desktop window handle not found: {window_handle}")
            sys.exit(1)
    else:
        selected = windows[0]

    target_dir = Path.home() / ".codex" / "memories" / "liteharness"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "codex_inbox_target.json"
    payload = {
        "mode": "codex-desktop",
        "window_handle": selected.get("handle"),
        "window_title": selected.get("title", ""),
        "agent_id": current_codex_agent_id(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Codex Desktop target set: {selected.get('handle')} -> {target_path}")


def cmd_pty_list() -> None:
    """List all PTY sessions."""
    from . import pty_daemon

    if not pty_daemon.is_daemon_running():
        print("PTY daemon is not running.")
        return

    result = pty_daemon.send_command({"cmd": "list"})
    if not result.get("ok"):
        print(f"Error: {result.get('error', 'unknown')}")
        return

    sessions = result.get("sessions", [])
    if not sessions:
        print("No active PTY sessions.")
        return

    print(f"Active PTY sessions ({len(sessions)}):")
    for s in sessions:
        status = "alive" if s.get("alive") else "dead"
        name = s.get("name") or "unnamed"
        print(f"  [{status}] {s['agent_id']} ({name}) — {s.get('cwd', '?')}")


def cmd_pty_kill(agent_id: str) -> None:
    """Kill a PTY session."""
    from . import pty_daemon

    if not pty_daemon.is_daemon_running():
        print("Error: PTY daemon is not running.")
        sys.exit(1)

    result = pty_daemon.send_command({"cmd": "kill", "agent_id": agent_id})
    if result.get("ok"):
        print(f"Killed session {agent_id}")
    else:
        print(f"Error: {result.get('error', 'unknown')}")
        sys.exit(1)


def cmd_discover(count: int = 100, include_all: bool = False) -> None:
    """Discover live agents.

    Liveness matches the desktop (harness-presence.ts) as a single source of
    truth: an agent is live only with a fresh heartbeat AND an alive owning
    session_pid. This excludes orphaned watchers (dead session, lingering watch
    process) that heartbeat forever.

    The old default was count=5, applied as an unconditional `agents[:5]` slice
    whose length was then printed as the total. That header was not a count of
    the fleet, it was the truncation constant describing itself: with 15 agents
    registered it printed "Active agents (5)", and the ten it dropped were
    indistinguishable from ten that did not exist. Pass include_all=True to list
    non-live presence files too (ghosts, exited, stale) for debugging.
    """
    from .hooks import _pid_alive

    DISCOVER_STALE_SECONDS = 600  # mirror DEFAULT_PRESENCE_STALE_MS (10 min)

    def _is_live(a: dict) -> bool:
        if a.get("exited_at"):
            return False
        if a.get("_age_seconds", float("inf")) > DISCOVER_STALE_SECONDS:
            return False
        session_pid = a.get("session_pid")
        if not session_pid:
            # ABSENT IS NOT DEAD. The upstream version returned False here, which
            # is only safe once every writer records an owner. Measured against a
            # real fleet the moment the writer landed: 11 of 15 presence files had
            # no session_pid, including six agents that had heartbeated 6 seconds
            # earlier -- and long-running watchers hold the OLD module in memory,
            # so they keep writing owner-less presence until they restart. Being
            # strict here reported 2 live out of ~9 and dropped this very session
            # from its own roll call. Unknown owner falls back to freshness; only
            # a recorded-and-dead pid is provably a ghost.
            return True
        return _pid_alive(session_pid)

    agents_dir = config.get_root() / "agents"
    if not agents_dir.exists():
        print("No agents directory. Run 'liteharness init' first.")
        return

    now = time.time()
    agents = []
    unreadable: list[str] = []

    for f in agents_dir.glob("*.json"):
        if f.name.endswith(".tmp"):  # a write in flight, not an agent
            continue
        try:
            agent = json.loads(f.read_text(encoding="utf-8"))
            last_seen = datetime.fromisoformat(agent.get("last_seen", "")).timestamp()
            agent["_age_seconds"] = now - last_seen
            agents.append(agent)
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            # An unreadable presence file used to vanish here without a word, so a
            # live agent simply stopped existing in the roll call. Sentinel's own
            # file was torn by a concurrent write and it took reading the raw bytes
            # to notice. A count that silently omits its failures is not a count.
            unreadable.append(f"{f.name}: {type(exc).__name__}")
            continue

    # Sort by most recent, then apply liveness filter (unless include_all).
    agents.sort(key=lambda a: a.get("_age_seconds", float("inf")))
    if not include_all:
        agents = [a for a in agents if _is_live(a)]
    agents = agents[:count]

    if unreadable:
        print(f"WARNING: {len(unreadable)} presence file(s) unreadable and NOT counted below:")
        for entry in unreadable:
            print(f"  ! {entry}")

    if not agents:
        print("No active agents found.")
        return

    # Build PID → window handle map for terminal linking
    pid_handle_map: dict[int, int] = {}
    try:
        from . import terminal_automation as wt
        for window in wt.list_panes():
            handle = window.get("handle", 0)
            for shell in window.get("shells", []):
                pid = shell.get("pid", 0)
                if pid:
                    pid_handle_map[pid] = handle
    except Exception:
        pass

    def _find_ancestor_handle(start_pid: int) -> int | None:
        """Walk process tree up to 5 levels to find a PID in the handle map."""
        try:
            import psutil
            pid = start_pid
            for _ in range(5):
                if pid in pid_handle_map:
                    return pid_handle_map[pid]
                proc = psutil.Process(pid)
                pid = proc.ppid()
                if pid <= 1:
                    break
        except Exception:
            pass
        return None

    header = "Agents" if include_all else "Active agents"
    print(f"{header} ({len(agents)}):")
    for a in agents:
        age = int(a.get("_age_seconds", 0))
        if age < 60:
            age_str = f"{age}s ago"
        elif age < 3600:
            age_str = f"{age // 60}m ago"
        else:
            age_str = f"{age // 3600}h ago"

        # PID-aware status: a fresh heartbeat alone isn't "active" — a dead
        # session_pid means the watcher is orphaned and the agent is a ghost.
        # The old rule was `age < 43200` (12h), so a corpse read as [active]
        # for half a day.
        if a.get("exited_at"):
            status = "exited"
        elif _is_live(a):
            status = "active"
        else:
            status = "ghost"
        from . import naming
        agent_name = naming.get_name(a.get('agent_id', ''))

        # Show spawn mode and handle info
        spawn_mode = a.get("spawn_mode", "")
        handle_info = ""
        if spawn_mode == "canvas":
            canvas_sid = a.get("canvas_session_id", "")
            handle_info = f" canvas:{canvas_sid[:8]}" if canvas_sid else " canvas"
        else:
            agent_pid = a.get("session_pid") or a.get("pid", 0)
            if agent_pid and pid_handle_map:
                found_handle = _find_ancestor_handle(agent_pid)
                if found_handle is not None:
                    handle_info = f" wt={found_handle}:2"
            if spawn_mode == "pty":
                handle_info = f" pty{handle_info}"

        tier = a.get("tier", "worker")
        # Show spatial data if available
        spatial = a.get("spatial", {})
        spatial_info = ""
        if spatial.get("pane_id"):
            spatial_info += f" pane={spatial['pane_id']}"
        if spatial.get("leaf_id"):
            spatial_info += f" leaf={spatial['leaf_id'][:8]}"
        print(f"  [{status}] {agent_name} ({a.get('agent_id', '?')}) {tier} {a.get('cli', '?')}/{a.get('model', '?')} — {age_str}{handle_info}{spatial_info}")


def cmd_rag() -> None:
    """Dispatch: liteharness rag <action> [query] [--top-k N] [--scope S] [--tier T] [--source S]"""
    from .rag.engine import litesuite_rag

    args = sys.argv[2:]
    if not args:
        result = litesuite_rag(action="help")
        print(json.dumps(result, indent=2))
        return

    action = args[0]
    query = ""
    top_k = 8
    scope = "project"
    tier = "worker"
    source = "all"
    root = None
    force = False
    strategy = "auto"
    bm25_weight = None
    variations = 3

    i = 1
    while i < len(args):
        arg = args[i]
        if arg == "--top-k" and i + 1 < len(args):
            top_k = int(args[i + 1])
            i += 2
        elif arg == "--scope" and i + 1 < len(args):
            scope = args[i + 1]
            i += 2
        elif arg == "--tier" and i + 1 < len(args):
            tier = args[i + 1]
            i += 2
        elif arg == "--source" and i + 1 < len(args):
            source = args[i + 1]
            i += 2
        elif arg == "--root" and i + 1 < len(args):
            root = args[i + 1]
            i += 2
        elif arg == "--strategy" and i + 1 < len(args):
            strategy = args[i + 1]
            i += 2
        elif arg == "--bm25-weight" and i + 1 < len(args):
            bm25_weight = float(args[i + 1])
            i += 2
        elif arg == "--variations" and i + 1 < len(args):
            variations = int(args[i + 1])
            i += 2
        elif arg == "--force":
            force = True
            i += 1
        elif not arg.startswith("--") and not query:
            query = arg
            i += 1
        else:
            i += 1

    result = litesuite_rag(
        action=action,
        query=query,
        top_k=top_k,
        scope=scope,
        tier=tier,
        source=source,
        root=root,
        force=force,
        strategy=strategy,
        bm25_weight=bm25_weight,
        variations=variations,
    )
    print(json.dumps(result, indent=2))


def cmd_install() -> None:
    """Install the LiteHarness skills+agents catalog into a CLI's canonical dir.

    Usage:
      liteharness install --list [--json]                 List detected CLIs + paths
      liteharness install --cli <name> [--path PATH] [--dry-run] [--json]
    """
    from pathlib import Path

    from liteharness import installers as _inst

    args = sys.argv[2:]
    if not args or args[0] in ("--help", "-h", "help"):
        print("Usage:")
        print("  liteharness install --list [--json]                 List detected CLIs + paths")
        print("  liteharness install --cli <name> [--path PATH] [--json]")
        print("                                                       Install catalog (opt-in)")
        print("  liteharness install --cli <name> --dry-run          Show what would be copied")
        print()
        print(f"Supported CLIs: {', '.join(sorted(_inst.ADAPTERS))}")
        return

    json_mode = "--json" in args

    if "--list" in args:
        if json_mode:
            print(_inst.list_clis_as_json())
        else:
            print(_inst.list_clis_for_display())
        return

    cli_name = None
    override_path: Path | None = None
    dry_run = False
    i = 0
    while i < len(args):
        if args[i] == "--cli" and i + 1 < len(args):
            cli_name = args[i + 1]
            i += 2
        elif args[i] == "--path" and i + 1 < len(args):
            override_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            i += 1

    if not cli_name:
        if json_mode:
            import json as _json
            print(_json.dumps({"ok": False, "error": "--cli <name> is required"}))
        else:
            print("Error: --cli <name> is required (or use --list)")
            print(f"Supported: {', '.join(sorted(_inst.ADAPTERS))}")
        sys.exit(1)

    if not json_mode:
        print(f"Installing LiteHarness catalog into {cli_name}{' (dry-run)' if dry_run else ''}...")
    try:
        report = _inst.install_cli(
            cli_name,
            override_path=override_path,
            dry_run=dry_run,
            quiet=json_mode,
        )
    except SystemExit as exc:
        if json_mode:
            import json as _json
            print(_json.dumps({"ok": False, "error": str(exc) or "Unknown CLI"}))
            sys.exit(1)
        raise
    except Exception as exc:
        if json_mode:
            import json as _json
            print(_json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)

    if json_mode:
        print(_inst.install_report_as_json(report))
        return

    total = sum(count for _, count in report.targets)
    print()
    print(f"{'(dry-run) ' if dry_run else ''}Installed {total} files into {report.root}")
    if report.post_install_ran:
        print("Post-install hook ran (e.g. AGENTS.md regen)")


def main() -> None:
    """CLI entry point."""
    is_help = len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h", "help")
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V", "version"):
        try:
            from importlib.metadata import version as _v
            print(f"liteharness {_v('liteharness')}")
        except Exception:
            print("liteharness (version unknown)")
        sys.exit(0)
    if len(sys.argv) < 2 or is_help:
        print("Usage: liteharness <command> [options]")
        print()
        print("Commands:")
        print("  bootstrap <path>               Bootstrap harness for a project (global init + scaffold)")
        print("  init                           Initialize LiteHarness (global only)")
        print("  update-scripts [--cli name]    Update watcher scripts for Codex/Copilot")
        print("  status                         Show status")
        print("  send <to> <message>            Send a message")
        print("  list                           List inbox messages")
        print("  discover [count]               Discover active agents")
        print("  spawn [options]                Spawn a new Claude Code session")
        print("  sessions <cmd> [options]       Save/restore terminal agent sessions")
        print("  register --agent-id ID [--cli CLI] [--model MODEL] [--name NAME] [--tier TIER] [--team TEAM]")
        print("           [--pane-id PANE] [--leaf-id LEAF] [--session-id SID] [--thread-id TID]")
        print("           [--workspace-id WID] [--project-id PID]")
        print("                                 Update agent presence info with spatial awareness data")
        print("  query-patterns [--top N] [--format text|json] [--query STR]")
        print("                                 Query task patterns (BM25)")
        print("  embed-query --query STR [--top N] [--format text|json]")
        print("                                 Hybrid RAG query (requires HARNESS_MCP_PORT)")
        print("  rag <action> [query] [--top-k N] [--scope S] [--tier T] [--source S]")
        print("                                 Multi-strategy code RAG (help|status|index|query|...)")
        print("  record-pattern --outcome <success|failure|stuck|unknown> [--agent-id ID] [--task DESC]")
        print("                                 Record a task pattern")
        print("  install --list                 List supported CLIs + auto-detection results")
        print("  install --cli <name> [--path PATH] [--dry-run]")
        print("                                 Install skills+agents into a CLI's canonical dir (opt-in)")
        print()
        print("Spawn options:")
        print("  --model <name>                 Model: opus, opus-1m, opus-200k, sonnet, haiku, or full ID")
        print("  --cwd <path>                   Working directory")
        print("  --worktree                     Create a git worktree first")
        print("  --permission-mode <mode>       default, plan, auto, bypassPermissions, acceptEdits")
        print("  --prompt <text>                Initial prompt to send")
        print("  --name <name>                  Agent name for LiteHarness")
        print("  --new-window                   Open in new Windows Terminal window")
        print("  --pty                          Spawn via ConPTY daemon (enables send-input)")
        print("  --args <extra>                 Additional CLI arguments")
        print()
        print("PTY commands (require pty-daemon running):")
        print("  pty-daemon                     Start the PTY daemon (background process)")
        print("  send-input <agent-id> <text>   Send text to a PTY session's stdin")
        print("  read-output <agent-id> [lines] Read recent output from a PTY session")
        print("  pty-list                       List all PTY sessions")
        print("  pty-kill <agent-id>            Kill a PTY session")
        print()
        print("Headed mode (UIAutomation — no Electron dependency):")
        print("  wt-list-panes [--format text|json]  List all WT windows and panes")
        print("  wt-focus <handle> <pane-id>         Focus a specific pane")
        print("  send-input --headed <handle:pane> <text>   Send keys via UIAutomation")
        print("  read-output --headed <handle:pane> [lines] Read buffer via UIAutomation")
        print()
        print("Codex Desktop mode (Win32 click + clipboard paste):")
        print("  codex-desktop-list [--format text|json]   List visible Codex Desktop windows")
        print("  codex-desktop-target [--handle N]         Set Desktop as Codex watcher target")
        print("  codex-desktop-send [options] <text>       Paste into Codex Desktop prompt")
        print("    --dry-run --probe-only --no-submit --no-restore-mouse --handle N --x N --y N")
        sys.exit(0 if is_help else 1)

    cmd = sys.argv[1]

    if cmd == "bootstrap":
        if len(sys.argv) < 3:
            print("Usage: liteharness bootstrap <project-path>")
            sys.exit(1)
        cmd_bootstrap(sys.argv[2])
    elif cmd == "init":
        cmd_init()
    elif cmd == "update-scripts":
        cli_filter = None
        if "--cli" in sys.argv:
            idx = sys.argv.index("--cli")
            if idx + 1 < len(sys.argv):
                cli_filter = [c.strip() for c in sys.argv[idx + 1].split(",")]
        clis = cli_filter or list(_SCRIPT_CLIS)
        print("Updating watcher scripts...")
        for cli_name in clis:
            if cli_name in _SCRIPT_CLIS:
                print(f"  {cli_name}...", end=" ")
                try:
                    if _install_cli_scripts(cli_name):
                        print("OK")
                    else:
                        print("skipped (scripts not found)")
                except Exception as e:
                    print(f"FAILED: {e}")
            else:
                print(f"  {cli_name}: no scripts to install")
        print("Done.")
    elif cmd == "status":
        cmd_status()
    elif cmd == "send":
        if len(sys.argv) < 4:
            print("Usage: liteharness send <to-agent-id> <message> [--from <your-id>] [--thread-id <id>]")
            sys.exit(1)
        # Consume flags by ARGV POSITION, never by string-matching the joined body.
        # The previous form joined argv[3:] into one string and truncated it at the first
        # flag-looking SUBSTRING. That destroyed the body two different ways, both
        # silently, and both still printed "Sent message <id>" and exited 0:
        #   * `send <to> --from <id> "text"` -> split("--from")[0] == "" -> EMPTY body.
        #     Measured 2026-08-10: body_len=0 delivered, success printed.
        #   * a body that merely CONTAINS the text "--from" (routine when sending an agent
        #     a command line) -> everything from that point on deleted.
        # The old scan for values had the mirror of the same fault: `"--from" in sys.argv`
        # matched a flag appearing inside the MESSAGE and took the next word as its value.
        flags = {"--project": None, "--from": None, "--thread-id": None}
        msg_tokens = []
        rest = sys.argv[3:]
        i = 0
        while i < len(rest):
            token = rest[i]
            if token in flags:
                if i + 1 < len(rest):
                    flags[token] = rest[i + 1]
                    i += 2
                else:
                    print(f"Error: {token} given with no value.")
                    sys.exit(1)
                continue
            msg_tokens.append(token)
            i += 1
        project = flags["--project"]
        from_id = flags["--from"]
        send_thread_id = flags["--thread-id"]
        msg_parts = " ".join(msg_tokens).strip()
        # Fail closed. A send that reports success on a body it emptied is worse than a
        # send that refuses: the caller believes the message landed and never re-sends.
        if not msg_parts:
            print(
                "Error: refusing to send an empty message body.\n"
                "  Usage: liteharness send <to-agent-id> <message> [--from <id>] "
                "[--thread-id <id>]"
            )
            sys.exit(1)
        cmd_send(sys.argv[2], msg_parts, project, from_id, send_thread_id)
    elif cmd == "list":
        cmd_list()
    elif cmd in ("sessions", "session"):
        from . import session_manager
        session_manager.main(sys.argv[2:])
    elif cmd == "register":
        reg_agent_id = None
        reg_cli = None
        reg_model = None
        reg_name = None
        reg_tier = None
        reg_team = None
        reg_pane_id = None
        reg_leaf_id = None
        reg_session_id = None
        reg_thread_id = None
        reg_workspace_id = None
        reg_project_id = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--agent-id" and i + 1 < len(sys.argv):
                reg_agent_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--cli" and i + 1 < len(sys.argv):
                reg_cli = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--model" and i + 1 < len(sys.argv):
                reg_model = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--name" and i + 1 < len(sys.argv):
                reg_name = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--tier" and i + 1 < len(sys.argv):
                reg_tier = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--team" and i + 1 < len(sys.argv):
                reg_team = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--pane-id" and i + 1 < len(sys.argv):
                reg_pane_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--leaf-id" and i + 1 < len(sys.argv):
                reg_leaf_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--session-id" and i + 1 < len(sys.argv):
                reg_session_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--thread-id" and i + 1 < len(sys.argv):
                reg_thread_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--workspace-id" and i + 1 < len(sys.argv):
                reg_workspace_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--project-id" and i + 1 < len(sys.argv):
                reg_project_id = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        if not reg_agent_id:
            print("Usage: liteharness register --agent-id ID [--cli CLI] [--model MODEL] [--name NAME] [--tier TIER] [--team TEAM] [--pane-id PANE] [--leaf-id LEAF] [--session-id SID] [--thread-id TID] [--workspace-id WID] [--project-id PID]")
            sys.exit(1)
        cmd_register(reg_agent_id, reg_cli, reg_model, reg_name, reg_tier, reg_team, reg_pane_id, reg_leaf_id, reg_session_id, reg_thread_id, reg_workspace_id, reg_project_id)
    elif cmd == "pty-daemon":
        from . import pty_daemon
        daemon = pty_daemon.PtyDaemon()
        daemon.start()
    elif cmd == "send-input":
        headed = "--headed" in sys.argv
        remaining = [a for a in sys.argv[2:] if a != "--headed"]
        if len(remaining) < 2:
            print("Usage: liteharness send-input [--headed] <agent-id|handle:pane> <text>")
            sys.exit(1)
        cmd_send_input(remaining[0], " ".join(remaining[1:]), headed=headed)
    elif cmd == "read-output":
        headed = "--headed" in sys.argv
        remaining = [a for a in sys.argv[2:] if a != "--headed"]
        if len(remaining) < 1:
            print("Usage: liteharness read-output [--headed] <agent-id|handle:pane> [lines]")
            sys.exit(1)
        ro_lines = int(remaining[1]) if len(remaining) > 1 else 50
        cmd_read_output(remaining[0], ro_lines, headed=headed)
    elif cmd == "wt-list-panes":
        wt_fmt = "text"
        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            if idx + 1 < len(sys.argv):
                wt_fmt = sys.argv[idx + 1]
        cmd_wt_list_panes(fmt=wt_fmt)
    elif cmd == "wt-focus":
        if len(sys.argv) < 4:
            print("Usage: liteharness wt-focus <window-handle> <pane-id>")
            sys.exit(1)
        cmd_wt_focus(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "codex-desktop-list":
        cd_fmt = "text"
        if "--format" in sys.argv:
            idx = sys.argv.index("--format")
            if idx + 1 < len(sys.argv):
                cd_fmt = sys.argv[idx + 1]
        cmd_codex_desktop_list(fmt=cd_fmt)
    elif cmd == "codex-desktop-target":
        window_handle = None
        if "--handle" in sys.argv:
            idx = sys.argv.index("--handle")
            if idx + 1 < len(sys.argv):
                window_handle = int(sys.argv[idx + 1])
        cmd_codex_desktop_target(window_handle=window_handle)
    elif cmd == "codex-desktop-send":
        submit = "--no-submit" not in sys.argv
        dry_run = "--dry-run" in sys.argv
        probe_only = "--probe-only" in sys.argv
        restore_mouse = "--no-restore-mouse" not in sys.argv
        window_handle = None
        click_x = None
        click_y = None
        text_parts = []
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg in ("--no-submit", "--dry-run", "--probe-only", "--no-restore-mouse"):
                i += 1
            elif arg == "--handle" and i + 1 < len(sys.argv):
                window_handle = int(sys.argv[i + 1])
                i += 2
            elif arg == "--x" and i + 1 < len(sys.argv):
                click_x = int(sys.argv[i + 1])
                i += 2
            elif arg == "--y" and i + 1 < len(sys.argv):
                click_y = int(sys.argv[i + 1])
                i += 2
            else:
                text_parts.append(arg)
                i += 1
        cd_text = " ".join(text_parts)
        if not cd_text and not dry_run and not probe_only:
            print("Usage: liteharness codex-desktop-send [--dry-run] [--probe-only] [--no-submit] [--handle N] [--x N --y N] <text>")
            sys.exit(1)
        cmd_codex_desktop_send(
            cd_text,
            submit=submit,
            restore_mouse=restore_mouse,
            window_handle=window_handle,
            click_x=click_x,
            click_y=click_y,
            dry_run=dry_run,
            probe_only=probe_only,
        )
    elif cmd == "pty-list":
        cmd_pty_list()
    elif cmd == "pty-kill":
        if len(sys.argv) < 3:
            print("Usage: liteharness pty-kill <agent-id>")
            sys.exit(1)
        cmd_pty_kill(sys.argv[2])
    elif cmd == "nudge-bot":
        nb_config = None
        nb_lmstudio = False
        nb_lmstudio_url = "http://localhost:1234"
        nb_lmstudio_model = "qwen/qwen3.6-27b"
        nb_lmstudio_timeout = 240.0
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--config" and i + 1 < len(sys.argv):
                nb_config = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--lmstudio":
                nb_lmstudio = True
                i += 1
            elif sys.argv[i] == "--lmstudio-url" and i + 1 < len(sys.argv):
                nb_lmstudio_url = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--lmstudio-model" and i + 1 < len(sys.argv):
                nb_lmstudio_model = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--lmstudio-timeout" and i + 1 < len(sys.argv):
                try:
                    nb_lmstudio_timeout = float(sys.argv[i + 1])
                except ValueError:
                    print("Error: --lmstudio-timeout must be a number")
                    sys.exit(1)
                i += 2
            else:
                i += 1
        from .nudge_bot import LMStudioConfig, NudgeBot
        if not nb_config:
            default_cfg = Path(__file__).parent / "nudge_bot_default.yaml"
            user_cfg = config.get_root() / "nudge-bot.yaml"
            nb_config = str(user_cfg if user_cfg.exists() else default_cfg)
        lmstudio_cfg = LMStudioConfig(
            enabled=nb_lmstudio,
            base_url=nb_lmstudio_url,
            model=nb_lmstudio_model,
            timeout_seconds=nb_lmstudio_timeout,
        )
        bot = NudgeBot(nb_config, lmstudio=lmstudio_cfg)
        bot.run()
    elif cmd == "spawn":
        sp_model = None
        sp_cwd = None
        sp_worktree = False
        sp_perm = None
        sp_prompt = None
        sp_name = None
        sp_new_window = False
        sp_args = None
        sp_pty = False
        sp_exec = None
        sp_thread_id = None
        sp_workspace_id = None
        sp_project_id = None
        sp_tier = None
        sp_team = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--model" and i + 1 < len(sys.argv):
                sp_model = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--cwd" and i + 1 < len(sys.argv):
                sp_cwd = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--worktree":
                sp_worktree = True
                i += 1
            elif sys.argv[i] == "--permission-mode" and i + 1 < len(sys.argv):
                sp_perm = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--prompt" and i + 1 < len(sys.argv):
                sp_prompt = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--name" and i + 1 < len(sys.argv):
                sp_name = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--new-window":
                sp_new_window = True
                i += 1
            elif sys.argv[i] == "--pty":
                sp_pty = True
                i += 1
            elif sys.argv[i] == "--args" and i + 1 < len(sys.argv):
                sp_args = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--exec" and i + 1 < len(sys.argv):
                sp_exec = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--thread-id" and i + 1 < len(sys.argv):
                sp_thread_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--workspace-id" and i + 1 < len(sys.argv):
                sp_workspace_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--project-id" and i + 1 < len(sys.argv):
                sp_project_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--tier" and i + 1 < len(sys.argv):
                sp_tier = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--team" and i + 1 < len(sys.argv):
                sp_team = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        cmd_spawn(
            model=sp_model, cwd=sp_cwd, worktree=sp_worktree,
            permission_mode=sp_perm, prompt=sp_prompt, name=sp_name,
            new_window=sp_new_window, additional_args=sp_args,
            pty_mode=sp_pty, exec_cmd=sp_exec,
            thread_id=sp_thread_id, workspace_id=sp_workspace_id,
            project_id=sp_project_id, tier=sp_tier, team=sp_team,
        )
    elif cmd == "discover":
        # `int(sys.argv[2])` consumed argv by POSITION, so the documented
        # `discover --all` died with ValueError: invalid literal for int().
        # Same family as the `send` truncation: a flag eaten as a positional.
        include_all = "--all" in sys.argv
        positional = [a for a in sys.argv[2:] if not a.startswith("-")]
        count = int(positional[0]) if positional and positional[0].isdigit() else 100
        cmd_discover(count, include_all=include_all)
    elif cmd == "query-patterns":
        qp_top = 5
        qp_fmt = "text"
        qp_query = None
        qp_project = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--top" and i + 1 < len(sys.argv):
                qp_top = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--format" and i + 1 < len(sys.argv):
                qp_fmt = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--query" and i + 1 < len(sys.argv):
                qp_query = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--project" and i + 1 < len(sys.argv):
                qp_project = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        cmd_query_patterns(top=qp_top, fmt=qp_fmt, query=qp_query, project=qp_project)
    elif cmd == "embed-query":
        eq_query = ""
        eq_top = 5
        eq_fmt = "text"
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--query" and i + 1 < len(sys.argv):
                eq_query = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--top" and i + 1 < len(sys.argv):
                eq_top = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--format" and i + 1 < len(sys.argv):
                eq_fmt = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        if not eq_query:
            print("Usage: liteharness embed-query --query STR [--top N] [--format text|json]")
            sys.exit(1)
        cmd_embed_query(query=eq_query, top=eq_top, fmt=eq_fmt)
    elif cmd == "record-pattern":
        rp_outcome = "unknown"
        rp_agent_id = None
        rp_task = None
        rp_project = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--outcome" and i + 1 < len(sys.argv):
                rp_outcome = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--agent-id" and i + 1 < len(sys.argv):
                rp_agent_id = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--task" and i + 1 < len(sys.argv):
                rp_task = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--project" and i + 1 < len(sys.argv):
                rp_project = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        cmd_record_pattern(outcome=rp_outcome, agent_id=rp_agent_id, task_desc=rp_task, project=rp_project)
    elif cmd == "rag":
        cmd_rag()
    elif cmd == "install":
        cmd_install()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

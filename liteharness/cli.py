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
    if not token:
        # Hand-opened sessions have no bridge env — same disk fallback the
        # hook bridge uses, so canvas ops work from any terminal.
        try:
            from pathlib import Path as _Path
            token = (_Path.home() / ".litesuite" / "bridge-token").read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            token = ""
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


def cmd_install_statusline() -> None:
    """Install ONLY the status line into Claude Code settings.json.

    `init` installs hooks and the status line together, which is right for a bare
    machine and wrong for a caller that has already written its own hooks. LiteSuite's
    desktop wizard writes settings.json itself and tags every entry it owns with
    `_source: liteharness-wizard`; running `init` behind it would add a SECOND,
    untagged set of hooks, and uninstall strips by `_source`, so those would be
    orphaned on removal. This exposes the one half such a caller is missing.

    Measured 2026-08-11, virgin Windows Sandbox, first run of LiteSuite 0.0.50:
    liteharness 0.2.9 installed, 11 hook events present in settings.json, `statusLine`
    absent. The capability shipped and nothing ever called it — the wizard writes the
    file by a different path than `init` does, so the half that only `init` knows about
    never ran.

    Exits non-zero on failure so a caller can tell; "kept" is a success.
    """
    settings_path = Path.home() / ".claude" / "settings.json"
    status = _merge_claude_statusline(settings_path)
    if status == "installed":
        print(f"Status line installed -> {settings_path}")
    elif status == "kept":
        print("Status line: kept your existing one (an existing value is never clobbered)")
    else:
        print("Status line: FAILED")
        sys.exit(1)


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


def _known_agent_ids() -> tuple[set[str], str | None]:
    """Every agent id the registry knows about.

    Returns (ids, registry_error). `registry_error` is non-None when the registry
    could not be READ AT ALL -- a missing directory, an unreadable file, a wrong
    root. That case must never be reported as "unknown recipient": an empty set
    from a broken reader and a genuinely unknown id produce the same answer, and
    blocking on the former would refuse every send in the fleet while looking
    exactly like a caught typo. Callers verify, or say plainly that they could not.
    """
    try:
        agents_dir = config.get_root() / "agents"
    except Exception as exc:  # noqa: BLE001 - any resolution failure is "cannot verify"
        return set(), f"cannot resolve the harness root ({exc})"
    if not agents_dir.exists():
        return set(), f"no agent registry at {agents_dir}"

    ids: set[str] = set()
    for f in agents_dir.glob("*.json"):
        if f.name.endswith(".tmp"):  # a write in flight, not an agent
            continue
        ids.add(f.stem)
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt record still proves the id exists
            continue
        for key in ("agent_id", "id", "session_id"):
            val = rec.get(key)
            if isinstance(val, str) and val:
                ids.add(val)
    if not ids:
        # A registry directory with zero agents is not a normal state -- this
        # process is itself registered. Treat it as unreadable, not as "nobody
        # exists", for the same reason as above.
        return set(), f"agent registry at {agents_dir} lists no agents"
    return ids, None


def cmd_send(
    to: str,
    body: str,
    project: str | None = None,
    from_id: str | None = None,
    thread_id: str | None = None,
    force: bool = False,
) -> None:
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

    # A WELL-FORMED BUT WRONG RECIPIENT REPORTED SUCCESS. The flag guard above only
    # catches an address that LOOKS like a flag; a mistyped uuid sails through. Found
    # 2026-08-13 by an agent who fluffed one hex digit and got
    # "Sent message <id> to <nobody>" twice, exit 0, with two real briefings left in a
    # maildir no agent owns. A typo'd send and a delivered send printed identically,
    # which is the same defect this file already documents twice above -- an
    # undeliverable address is not a warning.
    #
    # Fails CLOSED on an unknown id and OPEN on an unreadable registry, deliberately:
    # see _known_agent_ids. "broadcast" is a real address that owns no record.
    if not force and to != "broadcast":
        known, registry_error = _known_agent_ids()
        if registry_error:
            print(
                f"Warning: RECIPIENT NOT VERIFIED -- {registry_error}.\n"
                f"  Sending anyway. This is not a claim that {to} exists.",
                file=sys.stderr,
            )
        elif to not in known:
            import difflib

            near = difflib.get_close_matches(to, sorted(known), n=3, cutoff=0.6)
            hint = ""
            if near:
                hint = "  Did you mean:\n" + "".join(f"    {c}\n" for c in near)
            print(
                f"Error: no agent {to!r} is registered. NOTHING WAS SENT.\n"
                f"{hint}"
                f"  {len(known)} agent(s) known. `liteharness discover` lists the live ones.\n"
                f"  Pass --force to send to an id the registry does not know "
                f"(an agent that has not registered yet).",
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


def cmd_inbox(count: int = 10, agent: str | None = None, all_agents: bool = False) -> None:
    """Read-only inbox view: full bodies across new/cur/done, newest N, no claim/move.

    Replaces the fleet's hand-rolled `ls -t ~/.liteharness/inbox/done | xargs cat`
    one-liners (which miss new/ and cur/). Never mutates the maildir — the watcher
    and `hooks check` own claiming.
    """
    me = agent or config.get_agent_id()
    msgs = []
    for directory in (inbox.INBOX_NEW, inbox.INBOX_CUR, inbox.INBOX_DONE):
        if not directory.exists():
            continue
        for f in directory.iterdir():
            if f.suffix != ".json":
                continue
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not all_agents:
                if me not in (msg.get("to"), msg.get("from")) and msg.get("to") != "broadcast":
                    continue
            sort_key = msg.get("timestamp") or ""
            if not sort_key:
                try:
                    sort_key = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    pass
            msgs.append((sort_key, directory.name, msg))
    msgs.sort(key=lambda t: t[0])
    msgs = msgs[-count:]
    if not msgs:
        print("No messages." if all_agents else f"No messages involving {me}. Try --all.")
        return
    out = [f"{len(msgs)} message(s), oldest first (read-only view):"]
    for ts, dirname, msg in msgs:
        pr = "[!] " if msg.get("priority") == "urgent" else ""
        thread = f" (thread {msg.get('thread_id')})" if msg.get("thread_id") else ""
        out.append("")
        out.append(f"--- {pr}{(msg.get('timestamp') or '')[:19]} [{dirname}] {msg.get('from', '?')} -> {msg.get('to', '?')}{thread}")
        out.append(msg.get("body", ""))
    text = "\n".join(out)
    try:
        print(text)
    except UnicodeEncodeError:
        # CP1252 console without PYTHONIOENCODING — never let a non-ASCII body crash a read
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


VALID_TIERS = ("orchestrator", "leader", "worker", "thinker", "reviewer")

# Mirror cmd_discover's DISCOVER_STALE_SECONDS so name-takeover liveness uses the
# same bar as the live-agent roll call (a holder past this with a dead pid is a ghost).
_NAME_LIVE_STALE_SECONDS = 600


def _agent_record_live(agent_id: str) -> bool:
    """True if the agent's presence shows a fresh heartbeat AND an alive owning
    session_pid. Mirrors cmd_discover._is_live so name-takeover never steals a name
    from a genuinely live agent — only from a dead ghost squatting the registry."""
    from .hooks import _pid_alive

    path = config.get_root() / "agents" / f"{agent_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if data.get("exited_at"):
        return False
    try:
        age = time.time() - datetime.fromisoformat(data.get("last_seen", "")).timestamp()
    except (ValueError, TypeError):
        return False
    if age > _NAME_LIVE_STALE_SECONDS:
        return False
    session_pid = data.get("session_pid")
    if not session_pid:  # orphaned watcher: no immutable owning session = not live
        return False
    return _pid_alive(session_pid)


def _evict_agent_records(agent_id: str) -> str:
    """Move a dead agent's presence + name-override into a timestamped backup dir so a
    ghost stops squatting its name/registry slot. Recoverable, not destroyed. Returns
    the backup dir name."""
    root = config.get_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    backup = root / f".ghost_evicted_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for rel in (f"agents/{agent_id}.json", f"names/{agent_id}"):
        src = root / rel
        if src.exists():
            try:
                src.replace(backup / src.name)
            except OSError:
                pass
    return backup.name


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
    canvas_session: str | None = None,
    takeover: bool = False,
    session_pid: int | None = None,
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

    # The OWNING process, so liveness is a fact about a pid rather than a guess
    # from a heartbeat someone else writes.
    #
    # 🔴 WITHOUT THIS, A CLI-REGISTERED AGENT IS PERMANENTLY INDISTINGUISHABLE
    # FROM A CORPSE. Two things read presence.session_pid and both treat a falsy
    # one as "not live": _agent_record_live (so --takeover will steal the name
    # from a RUNNING agent) and the janitor's dead-owner purge (so the row can
    # never be reaped by pid and simply accumulates). Measured 2026-08-19 on the
    # live roster: six rows for one LiteTUI seat, four of them dead, and two live
    # probes in which the second took the name from the first.
    #
    # hooks.py has always recorded this; `liteharness register` never did, so
    # every non-hook caller was invisible to both mechanisms.
    #
    # OPT-IN ON PURPOSE. The caller passes its OWN pid; we do not infer one from
    # os.getppid(), because for a caller wrapped in a shell the parent is a
    # transient process and recording it would make the row reapable while the
    # agent is still alive -- trading an accumulation bug for a disappearance
    # bug. A caller that passes nothing behaves exactly as before.
    if session_pid:
        from .hooks import _pid_alive
        if _pid_alive(session_pid):
            presence["session_pid"] = int(session_pid)
        else:
            # Recording a dead pid would mark the row reapable the instant it is
            # written. Refuse it and say so rather than silently accepting.
            print(f"    Warning: --session-pid {session_pid} is not a live process; not recorded.")

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

    # Canvas pane mapping — top-level field (jsonl-monitor and the War Room
    # read presence.canvas_session_id to resolve click-to-terminal). Without
    # this, canvas-spawned agents register a SECOND presence with no pane
    # link while the pre-created canvas-<sid> file goes stale (identity split).
    if canvas_session:
        presence["canvas_session_id"] = canvas_session
        presence.setdefault("spawn_mode", "canvas")

    # Name handling: override > existing override > generated
    if name:
        taken_by = naming.is_name_taken(name, exclude_id=agent_id)
        if taken_by and takeover and not _agent_record_live(taken_by):
            backup = _evict_agent_records(taken_by)
            print(f"Takeover: name '{name}' was held by dead ghost {taken_by[:8]} — evicted to {backup}/. Claiming it.")
            taken_by = None
        if taken_by:
            if takeover:
                print(f"Warning: name '{name}' is held by LIVE agent {taken_by[:8]} — refusing takeover. Picking a different name.")
            else:
                print(f"Warning: name '{name}' is already used by {taken_by[:8]}. Picking a different name.")
            name = None
        else:
            naming.set_override(agent_id, name)

    resolved_name = naming.get_name(agent_id)
    presence["name"] = resolved_name
    now_iso = datetime.now(timezone.utc).isoformat()
    presence["last_seen"] = now_iso
    # Re-registering declares the agent LIVE: clear any recap wind-down flag
    # (a preserved recap_at kept re-registered agents in the 300s fast-purge
    # tier) and anchor recap detection so a stale transcript marker can't
    # re-flag this agent.
    presence.pop("recap_at", None)
    presence["registered_at"] = now_iso

    path.write_text(json.dumps(presence, indent=2), encoding="utf-8")
    team_str = f", team={presence['team']}" if presence.get("team") else ""
    spatial_str = f", pane={pane_id}" if pane_id else ""
    print(f"Registered agent {agent_id}: cli={presence.get('cli', '?')}, model={presence.get('model', '?')}, tier={presence.get('tier', 'worker')}, name={resolved_name}{team_str}{spatial_str}")


# Bump when the `patterns` FTS5 column set changes. The table is a pure cache
# rebuilt from patterns.jsonl, so a mismatch is resolved by dropping it — never
# by migrating. v2 added `supersedes` and started populating `id` from task_id.
# The BM25-first ORDER BY needed no bump (rankings are computed per query, not
# stored). v3 added `verified` + `pattern_id` and the attestation join; the
# rebuild it forces also applies the positive row validation everywhere.
_PATTERN_DB_SCHEMA = "3"


def _pattern_db_open(db_path: Path):
    """Open (or create) the FTS5-backed patterns SQLite DB. Returns a connection."""
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patterns_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    row = conn.execute(
        "SELECT value FROM patterns_meta WHERE key='schema_version'"
    ).fetchone()
    if (row[0] if row else None) != _PATTERN_DB_SCHEMA:
        # Derived cache — drop and let _pattern_db_sync rebuild from the JSONL.
        conn.execute("DROP TABLE IF EXISTS patterns")
        conn.execute("DELETE FROM patterns_meta WHERE key IN ('jsonl_mtime','jsonl_size')")
        conn.execute(
            "INSERT OR REPLACE INTO patterns_meta(key, value) VALUES('schema_version', ?)",
            (_PATTERN_DB_SCHEMA,),
        )

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS patterns USING fts5(
            description,
            reason,
            lesson,
            id UNINDEXED,
            timestamp UNINDEXED,
            outcome UNINDEXED,
            complexity UNINDEXED,
            supersedes UNINDEXED,
            verified UNINDEXED,
            pattern_id UNINDEXED,
            tokenize="unicode61"
        )
    """)
    conn.commit()
    return conn


def _load_pattern_entries(patterns_path: Path) -> list[dict]:
    """Load pattern rows from a JSONL, POSITIVELY validated.

    A line is a pattern iff it is a JSON object with no ``type`` field (or
    ``type == "pattern"``) carrying both ``task_id`` and ``description``.
    Anything else — an event line, a fragment, junk — is counted and surfaced
    on stderr, never inserted: a non-pattern line loaded as a pattern becomes
    a ghost row (empty description, real-looking id, newest timestamp) that
    tops every bare recency query. Both callers (FTS5 sync and the JSONL
    fallback) share this loader so their validation cannot drift apart.
    """
    entries: list[dict] = []
    unparseable = 0
    nonpattern = 0
    for line in patterns_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            unparseable += 1
            continue
        if (
            not isinstance(obj, dict)
            or obj.get("type") not in (None, "pattern")
            or not (obj.get("task_id") and obj.get("description"))
        ):
            nonpattern += 1
            continue
        entries.append(obj)
    if unparseable or nonpattern:
        print(
            f"[pattern-sync] skipped {nonpattern} non-pattern line(s) and "
            f"{unparseable} unparseable line(s) in {patterns_path}",
            file=sys.stderr,
        )
    return entries


# Attestations live in a SEPARATE append-only file — patterns.jsonl stays
# pattern-only forever. Old readers never see an event line at all (they
# degrade to showing every pattern as unverified); new readers JOIN the two
# logs. That makes mixed-version compatibility structural, not sequenced.
_PATTERN_ATTESTATIONS_FILENAME = "pattern-attestations.jsonl"

# level -> the evidence field that level REQUIRES. A verification without its
# evidence is refused: the exception carries its own authorization instead of
# being self-assertable.
_PATTERN_VERIFY_LEVELS = {
    "human": "evidence_ref",
    "judgement": "delegation_ref",
    "gauntlet": "run_id",
}

_PATTERN_VERIFIED_LABELS = {
    "human": "VERIFIED",
    "judgement": "JUDGEMENT",
    "gauntlet": "GAUNTLET",
}


def _pattern_content_id(entry: dict) -> str:
    """Deterministic, checkout-stable, content-addressed id for a legacy row.

    `legacy:<sha256(canonical)>` where canonical is the record with derived
    fields (`pattern_id`) excluded, dumped with sorted keys, compact
    separators, and raw UTF-8 — which matches RFC 8785 JCS for records whose
    values are strings, ints, arrays and objects (every pattern record; JCS
    float serialization would differ, but no pattern field is a float).

    Deliberately NO path, repo, or ordinal component: the same committed JSONL
    must yield the same ids in every worktree and clone, or attestations made
    from one checkout would orphan in another. Exact duplicate records SHARE
    one identity — they are indistinguishable statements of the same fact, so
    attesting one attests the fact.
    """
    import hashlib

    body = {k: v for k, v in entry.items() if k != "pattern_id"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "legacy:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pattern_effective_id(entry: dict) -> str:
    """A record's immutable identity: its own pattern_id, else derived."""
    return entry.get("pattern_id") or _pattern_content_id(entry)


def _load_attestation_events(attest_path: Path) -> list[dict]:
    """Load valid attestation events in file order; surface anything else.

    Valid: `{"type":"verification", pattern_id, level in the enum}` or
    `{"type":"revocation", pattern_id}`. An unknown event type causes ZERO
    state changes and is counted out loud — never guessed at.
    """
    events: list[dict] = []
    unknown = 0
    if not attest_path.exists():
        return events
    for line in attest_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            unknown += 1
            continue
        if not isinstance(obj, dict) or not obj.get("pattern_id"):
            unknown += 1
        elif obj.get("type") == "verification" and obj.get("level") in _PATTERN_VERIFY_LEVELS:
            events.append(obj)
        elif obj.get("type") == "revocation":
            events.append(obj)
        else:
            unknown += 1
    if unknown:
        print(
            f"[pattern-sync] {unknown} unknown/invalid event line(s) in "
            f"{attest_path.name} caused no state change",
            file=sys.stderr,
        )
    return events


def _effective_verified_map(entries: list[dict], attest_path: Path) -> dict[str, str]:
    """Fold the attestation log into effective state, keyed by pattern_id.

    Duplicate attestations on one pattern: last-wins by file order
    (deliberate). An attestation resolving to no known pattern_id is an ERROR
    surfaced by id — never a silent no-op; fail-closed means it also never
    promotes anything else.
    """
    known = {_pattern_effective_id(e) for e in entries}
    state: dict[str, str] = {}
    unresolved: list[str] = []
    for ev in _load_attestation_events(attest_path):
        pid = ev["pattern_id"]
        if pid not in known:
            unresolved.append(pid)
            continue
        state[pid] = ev["level"] if ev["type"] == "verification" else "unverified"
    if unresolved:
        shown = ", ".join(unresolved[:5])
        more = f" (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""
        print(
            f"[pattern-sync] {len(unresolved)} unresolved attestation(s) — "
            f"no pattern has that pattern_id: {shown}{more}",
            file=sys.stderr,
        )
    return state


def _pattern_db_sync(conn, patterns_path: Path) -> None:
    """Sync JSONL write-ahead log into FTS5. Skips re-index when nothing changed.

    Freshness is checked against BOTH logs: an attestation append changes
    effective state without touching patterns.jsonl, so a check keyed only on
    the pattern file would serve stale, pre-promotion rows forever.
    """
    import sqlite3
    attest_path = patterns_path.parent / _PATTERN_ATTESTATIONS_FILENAME
    stat = patterns_path.stat()
    mtime_str = str(stat.st_mtime)
    size_str = str(stat.st_size)
    try:
        astat = attest_path.stat()
        attest_mtime_str = str(astat.st_mtime)
        attest_size_str = str(astat.st_size)
    except OSError:
        attest_mtime_str = "0"
        attest_size_str = "0"

    cached: dict[str, str | None] = {}
    for key in ("jsonl_mtime", "jsonl_size", "attest_mtime", "attest_size"):
        row = conn.execute(
            "SELECT value FROM patterns_meta WHERE key=?", (key,)
        ).fetchone()
        cached[key] = row[0] if row else None

    if (
        cached["jsonl_mtime"] == mtime_str
        and cached["jsonl_size"] == size_str
        and cached["attest_mtime"] == attest_mtime_str
        and cached["attest_size"] == attest_size_str
    ):
        return  # nothing changed

    entries = _load_pattern_entries(patterns_path)
    effective = _effective_verified_map(entries, attest_path)

    conn.execute("DELETE FROM patterns")
    conn.executemany(
        "INSERT INTO patterns(description, reason, lesson, id, timestamp, outcome, complexity, supersedes, verified, pattern_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                e.get("description", ""),
                e.get("reason", ""),
                e.get("lesson", ""),
                # PatternEntry's identifier is `task_id`; the legacy `id` read here
                # never existed in the JSONL, so this column was empty on every row
                # and callers had no handle to name a pattern by.
                e.get("task_id") or e.get("id", ""),
                e.get("timestamp", ""),
                e.get("outcome", ""),
                e.get("complexity", ""),
                json.dumps(e.get("supersedes") or []),
                effective.get(pid) or e.get("verified") or "unverified",
                pid,
            )
            for e in entries
            for pid in (_pattern_effective_id(e),)
        ],
    )
    for key, value in (
        ("jsonl_mtime", mtime_str),
        ("jsonl_size", size_str),
        ("attest_mtime", attest_mtime_str),
        ("attest_size", attest_size_str),
    ):
        conn.execute(
            "INSERT OR REPLACE INTO patterns_meta(key, value) VALUES(?, ?)",
            (key, value),
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


def _superseded_task_ids(conn) -> set[str]:
    """task_ids retired by some later pattern's `supersedes` array."""
    retired: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT supersedes FROM patterns WHERE supersedes NOT IN ('', '[]')"
        ).fetchall()
    except Exception:
        return retired  # pre-v2 cache — nothing is retired
    for (raw,) in rows:
        try:
            for tid in json.loads(raw) or []:
                if tid:
                    retired.add(str(tid))
        except (json.JSONDecodeError, TypeError):
            continue
    return retired


def _pattern_fts5_query(conn, query: str | None, top: int) -> list[dict]:
    """Run FTS5 query (or full scan when query is None).

    Queried results rank by BM25 relevance (lower is better), with recency only
    as the tiebreak — a query's best match must not lose to whatever was
    recorded last. The full scan stays recency-first: with no query there is
    no relevance signal, so newest-first is correct.

    Patterns named in another pattern's `supersedes` array are dropped: a retired
    record and the correction that replaced it describe the same subject in the
    same words, so returning both hands the caller a contradiction it cannot rank.
    """
    cols = "description, reason, lesson, id, timestamp, outcome, complexity, supersedes, verified, pattern_id"
    retired = _superseded_task_ids(conn)
    # Over-fetch so dropping retired rows still fills `top`.
    fetch = top * 4 if retired else top

    if query:
        safe_q = _fts5_phrase_query(query)
        if not safe_q:
            return []
        rows = conn.execute(
            f"SELECT {cols} FROM patterns WHERE patterns MATCH ? "
            "ORDER BY bm25(patterns) ASC, timestamp DESC LIMIT ?",
            (safe_q, fetch),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {cols} FROM patterns ORDER BY timestamp DESC LIMIT ?",
            (fetch,),
        ).fetchall()

    out: list[dict] = []
    for r in rows:
        # A supersedes array may name either handle: historical entries hold
        # task_ids (original all-rows semantics, never rewritten), new entries
        # hold pattern_ids.
        if (r[3] and r[3] in retired) or (r[9] and r[9] in retired):
            continue
        try:
            supersedes = json.loads(r[7]) or []
        except (json.JSONDecodeError, TypeError):
            supersedes = []
        out.append(
            {
                "description": r[0],
                "reason": r[1],
                "lesson": r[2],
                # `task_id` is a grouping label — pass `pattern_id` to
                # `supersedes`/`verify-pattern` to name a record precisely.
                # `id` kept for back-compat.
                "task_id": r[3],
                "id": r[3],
                "timestamp": r[4],
                "outcome": r[5],
                "complexity": r[6],
                "supersedes": supersedes,
                "verified": r[8] or "unverified",
                "pattern_id": r[9] or "",
            }
        )
        if len(out) >= top:
            break
    return out


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
        # The verification state rides EVERY row: an unverified pattern reads
        # as a hypothesis, never a premise.
        label = _PATTERN_VERIFIED_LABELS.get(e.get("verified"), "UNVERIFIED")
        # task_id is the handle for `record-pattern --supersedes` — show it, or
        # the caller can name what it read but not what it needs to retire.
        tid = e.get("task_id") or e.get("id") or ""
        head = f"  [{marker}][{label}] {ts} ({complexity})" + (f" <{tid}>" if tid else "")
        try:
            print(f"{head} {desc}")
        except UnicodeEncodeError:
            print(f"{head} {desc.encode('ascii', 'replace').decode()}")
        # pattern_id is the attestation/supersession handle — a row without it
        # visible cannot be promoted, revoked, or precisely retired.
        if e.get("pattern_id"):
            print(f"       Pattern-id: {e['pattern_id']}")
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

    # JSONL fallback — substring scan (used when sqlite3 / FTS5 unavailable).
    # Same validated loader and attestation join as the FTS5 path; only the
    # ranking differs (no BM25 without FTS5 — the scan stays recency-sorted).
    entries = _load_pattern_entries(patterns_path)
    effective = _effective_verified_map(
        entries, patterns_path.parent / _PATTERN_ATTESTATIONS_FILENAME
    )
    for e in entries:
        pid = _pattern_effective_id(e)
        e["pattern_id"] = pid
        e["verified"] = effective.get(pid) or e.get("verified") or "unverified"

    if query:
        lower_q = query.lower()
        entries = [
            e for e in entries
            if lower_q in e.get("description", "").lower()
            or lower_q in e.get("reason", "").lower()
            or lower_q in e.get("lesson", "").lower()
        ]

    # Same supersession rule as the FTS5 path — the fallback must not hand back
    # a contradiction the primary path would have filtered. A supersedes array
    # may name either handle (historical task_ids, new pattern_ids).
    retired = {
        str(tid)
        for e in entries
        for tid in (e.get("supersedes") or [])
        if tid
    }
    if retired:
        entries = [
            e for e in entries
            if e.get("task_id") not in retired and e["pattern_id"] not in retired
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
    supersedes: list[str] | None = None,
) -> None:
    """Record a pattern to .liteharness/patterns.jsonl in the current project.

    Every record is BORN ``verified: "unverified"`` with an immutable UUID4
    ``pattern_id``. There is deliberately NO level parameter here: promotion is
    an append-only attestation (``verify-pattern``) whose level carries its own
    evidence — a record-time level would be self-assertable.

    ``supersedes`` names records this one retires — pattern_ids for precision,
    task_ids still honored (historical semantics, and a task_id retires every
    row that carries it). Supersession is append-only — the retired entries are
    never edited, retrieval just stops returning them. It has to be supplied
    here, at record time: which fact replaces which is only knowable while both
    are in the recording agent's context.
    """
    import subprocess
    import uuid

    project_root = project or os.getcwd()
    patterns_dir = Path(project_root) / ".liteharness"
    patterns_dir.mkdir(parents=True, exist_ok=True)
    patterns_path = patterns_dir / "patterns.jsonl"

    # Read the task description from stdin ONLY when the caller opts in with `--task -`.
    # This used to fire whenever --task was merely ABSENT, which deadlocked any caller that
    # spawned us with an inherited stdin pipe it never wrote to and never closed: the read
    # blocked until the parent's timeout killed us, yielding an empty stderr and an error at
    # the call site that looked like a bridge failure rather than a hang. A missing --task
    # now defaults quietly; `echo "task" | liteharness record-pattern --task -` still works.
    if task_desc == "-":
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
        # task_id is a GROUPING label, not an identity: unknown-<epoch> ids
        # collide within a second (live duplicates exist). pattern_id is the
        # identity — immutable, and the only attestation/supersession target.
        "task_id": f"{agent_id or 'unknown'}-{int(time.time())}",
        "pattern_id": str(uuid.uuid4()),
        "session": agent_id or "cli",
        "outcome": schema_outcome,
        "complexity": "medium",
        "description": task_desc,
        "verified": "unverified",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if supersedes:
        entry["supersedes"] = supersedes
    if outcome in ("failure", "stuck"):
        entry["reason"] = f"Outcome was {outcome}"
    if git_summary:
        entry["description"] = f"{task_desc}\n\nGit diff summary:\n{git_summary}"

    line = json.dumps(entry) + "\n"
    with open(patterns_path, "a", encoding="utf-8") as f:
        f.write(line)

    print(f"Recorded pattern: {schema_outcome} for {entry['task_id']}")


def cmd_verify_pattern(
    pattern_id: str,
    level: str,
    actor: str | None = None,
    evidence_ref: str | None = None,
    delegation_ref: str | None = None,
    run_id: str | None = None,
    project: str | None = None,
) -> None:
    """Append a verification attestation to pattern-attestations.jsonl.

    One event per state change, append-only, in a SEPARATE file so
    patterns.jsonl stays pattern-only forever. Each level REQUIRES its
    evidence: human -> evidence_ref (where Ryan/the human approved),
    judgement -> delegation_ref (where judgement was delegated),
    gauntlet -> run_id. Resolution targets pattern_id ONLY and FAILS CLOSED
    on zero matches — never a task_id fallback that would knowingly promote
    unrelated rows (the task_id namespace holds live collisions).

    This is provenance enforcement plus policy, NOT security: any local
    process can append; the supported path makes every state change causal
    and attributable, and that visibility is the defense.
    """
    import uuid

    def refuse(msg: str) -> None:
        print(f"[verify-pattern] {msg}", file=sys.stderr)
        sys.exit(1)

    if level not in _PATTERN_VERIFY_LEVELS:
        refuse(f"unknown level '{level}' — one of: {', '.join(_PATTERN_VERIFY_LEVELS)}")
    if not actor:
        refuse("--actor is required — every state change must be attributable")
    evidence_key = _PATTERN_VERIFY_LEVELS[level]
    evidence = {
        "evidence_ref": evidence_ref,
        "delegation_ref": delegation_ref,
        "run_id": run_id,
    }[evidence_key]
    if not evidence:
        refuse(
            f"level '{level}' requires --{evidence_key.replace('_', '-')} — "
            "the exception carries its own authorization"
        )

    project_root = project or os.getcwd()
    patterns_path = Path(project_root) / ".liteharness" / "patterns.jsonl"
    if not patterns_path.exists():
        refuse(f"no patterns.jsonl at {patterns_path}")

    entries = _load_pattern_entries(patterns_path)
    matches = [e for e in entries if _pattern_effective_id(e) == pattern_id]
    if not matches:
        refuse(
            f"'{pattern_id}' resolves to 0 patterns — attestation refused "
            "(fail closed). task_ids are not attestation targets; take the "
            "Pattern-id from query-patterns output."
        )
    # Exact-id matching cannot resolve to more than one identity; >1 rows here
    # are exact duplicates deliberately sharing it — attesting one attests the
    # fact, so every copy reflects the state.

    event = {
        "type": "verification",
        "attestation_id": str(uuid.uuid4()),
        "pattern_id": pattern_id,
        "level": level,
        "actor": actor,
        evidence_key: evidence,
        "source": "liteharness-cli",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    attest_path = patterns_path.parent / _PATTERN_ATTESTATIONS_FILENAME
    with open(attest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    print(f"Attested {level} for {pattern_id} (attestation {event['attestation_id']})")


def cmd_revoke_pattern(
    pattern_id: str,
    reason: str | None = None,
    prior_attestation_id: str | None = None,
    actor: str | None = None,
    project: str | None = None,
) -> None:
    """Append a revocation, returning a pattern's effective state to unverified.

    A revocation is just another attestation — nothing is ever edited in
    place. It requires the reason and the id of the verification it revokes;
    the prior attestation must exist and target the same pattern (fail
    closed), or a typo would silently revoke nothing while reporting success.
    """
    import uuid

    def refuse(msg: str) -> None:
        print(f"[revoke-pattern] {msg}", file=sys.stderr)
        sys.exit(1)

    if not reason:
        refuse("--reason is required")
    if not prior_attestation_id:
        refuse("--prior-attestation-id is required — name the verification being revoked")
    if not actor:
        refuse("--actor is required — every state change must be attributable")

    project_root = project or os.getcwd()
    attest_path = (
        Path(project_root) / ".liteharness" / _PATTERN_ATTESTATIONS_FILENAME
    )
    prior = [
        ev
        for ev in _load_attestation_events(attest_path)
        if ev.get("type") == "verification"
        and ev.get("attestation_id") == prior_attestation_id
        and ev.get("pattern_id") == pattern_id
    ]
    if not prior:
        refuse(
            f"no verification '{prior_attestation_id}' targeting '{pattern_id}' "
            "exists — revocation refused (fail closed)"
        )

    event = {
        "type": "revocation",
        "attestation_id": str(uuid.uuid4()),
        "pattern_id": pattern_id,
        "actor": actor,
        "reason": reason,
        "prior_attestation_id": prior_attestation_id,
        "source": "liteharness-cli",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(attest_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    print(f"Revoked {prior_attestation_id} for {pattern_id} (revocation {event['attestation_id']})")


HARNESS_VERSION = "0.2.0"

BOOTSTRAP_HARNESS_SECTION_START = "<!-- LiteHarness Bootstrap - DO NOT EDIT THIS SECTION -->"
BOOTSTRAP_HARNESS_SECTION_END = "<!-- End LiteHarness Bootstrap -->"

BOOTSTRAP_HARNESS_CONTENT = """\
# LiteHarness — Active

This project uses [LiteHarness](https://litesuite.dev) — a five-tier AI agent orchestration system.

`liteharness bootstrap` creates the `.liteharness/` scaffold and an empty `patterns.jsonl`.
Config and prompt files are optional and are absent on a fresh install — treat a missing
one as "defaults apply", never as an error, and never block on it.

## Quick Start

- **Patterns:** `.liteharness/patterns.jsonl` — created by bootstrap
- **Config:** `.liteharness/config.yaml` — *if present*; absence means defaults
- **Tools:** `lst run <tool> action=<action>` (CLI) or MCP tool calls (inside LiteSuite).
  Run `lst run help` to enumerate — a hardcoded list goes stale and then misinforms.
- **Lifecycle:** `liteharness spawn/discover/send-input/read-output` (agent process management)

## Session Start

1. Run `lst run environment` for project context — needs no files, always works
2. Run `lst run pattern action=query query="<task>"` for relevant history
3. Read `.liteharness/config.yaml` if it exists
4. Follow the tier the harness assigned you — **do not assume you are the orchestrator**;
   spawning, merging and dispatching are orchestrator/leader actions
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
    # The librarian is OFFERED, never installed here. A bootstrap that silently
    # registers OS scheduled tasks is exactly the surprise git-as-memory exists
    # to remove, so this prints an invitation and changes nothing.
    print("  Optional: nightly librarian (promotes verified patterns into your")
    print("  architecture docs) — see `liteharness librarian-install --mode print`")


# Short aliases → current model IDs. Full IDs are always accepted verbatim
# (unknown aliases pass straight through to `claude --model`), so this table is a
# convenience layer, not a gate. Keep it current with the shipping fleet — the
# previous table silently pinned Opus 4.6 / Sonnet 4.6 long after 4.8 / Sonnet 5.
MODEL_ALIASES = {
    # "opus" tracks the CURRENT Opus generation, so spawning with --model opus
    # follows the frontier instead of pinning a superseded release.
    "opus": "claude-opus-5[1m]",
    "opus-1m": "claude-opus-5[1m]",
    "opus-200k": "claude-opus-5",
    "opus-5": "claude-opus-5[1m]",
    "opus-4.8": "claude-opus-4-8[1m]",
    "opus-4.8-200k": "claude-opus-4-8",
    "fable": "claude-fable-5",
    "sonnet": "claude-sonnet-5",
    "sonnet-5": "claude-sonnet-5",
    "sonnet-4.6": "claude-sonnet-4-6",
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
    )
    # Only invite self-naming when the spawner did NOT assign one — the old
    # unconditional "pick a unique name" contradicted --name and agents
    # registered under self-chosen names, orphaning the assigned identity.
    if name:
        bootstrap += f'4) Register with EXACTLY the name "{name}" as shown above. '
    else:
        bootstrap += "4) Pick a unique name for yourself (not Claude/Assistant) and use it when registering. "
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

    # Tier — carried IN THE BRIEF because canvas spawns cannot deliver it any
    # other way: the pane's claude boots (and the SessionStart hook emits the
    # tier preamble) BEFORE the spawner learns the agent's UUID, and
    # /canvas/claude does not forward context_env. Proven live 2026-08-07:
    # a --tier leader canvas spawn got the WORKER preamble and measurably ran
    # the wrong doctrine until corrected. Until the bridge forwards env, the
    # brief is the only post-boot channel that reliably arrives.
    spawn_tier = context_env.get("LITEHARNESS_TIER", "").strip()
    if spawn_tier and spawn_tier != "worker":
        lines.append(f"YOUR TIER: {spawn_tier.upper()}.")
        lines.append(
            f"If your injected '## Tier Preamble' header says a DIFFERENT tier "
            f"(canvas-spawn delivery gap), your real doctrine is "
            f"resources/liteharness-plugin/prompts/preambles/{spawn_tier}-preamble.md "
            f"(orchestrator: orchestrator-role.md, same prompts dir) — READ IT "
            f"BEFORE acting; it overrides the injected one."
        )

    # Cognitive architecture — carried IN THE BRIEF for the same reason as tier:
    # the canvas fallback path does not forward context_env, so the hook-side
    # injection (LITEHARNESS_COGNITIVE_FILE) never fires there. A polymath that
    # boots without its architecture is just an Opus with a name (RULING, Ryan
    # 2026-08-07: polymaths spawned MUST read their respective prompts).
    cog_file = context_env.get("LITEHARNESS_COGNITIVE_FILE", "").strip()
    if cog_file:
        lines.append(
            f"YOUR COGNITIVE ARCHITECTURE: {cog_file} — if this file's content "
            f"was NOT already injected above under '## Cognitive Architecture', "
            f"Read it IN FULL now and adopt it as your reasoning constraints "
            f"BEFORE acting. METHOD ONLY: any tier scaffolding or tool-access "
            f"grant inside that file is VOID — your tier and tools come from "
            f"your Tier Preamble. Confirm adoption to your spawner quoting the "
            f"file's first operating principle."
        )

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
        lines.append("")
        lines.append(
            "Execute this task IMMEDIATELY after completing the startup steps, "
            "in the SAME turn — do NOT stop after registering or wait for "
            "further instructions. (Agents that idle after setup stall the "
            "whole orchestration until a nudge arrives.)"
        )

    return "\n".join(lines)


_SPAWN_NUDGE = (
    "You have a spawn brief in your context above (## Spawn Brief). "
    "Complete its Mandatory Startup steps and execute its Task NOW, in this same turn."
)


def _write_spawn_brief(bootstrap: str):
    """Persist the bootstrap as a one-shot brief file for SessionStart injection.

    The spawned session finds it via LITEHARNESS_SPAWN_BRIEF; hooks.register_presence
    prints it into context and deletes it — the deletion doubles as the spawner's
    delivery receipt (see _wait_brief_consumed). Also sweeps briefs older than 24h:
    a surviving brief means its spawn never booted."""
    import uuid as _uuid
    briefs_dir = config.get_root() / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in briefs_dir.glob("*.md"):
        try:
            if now - old.stat().st_mtime > 86400:
                old.unlink()
        except OSError:
            pass
    path = briefs_dir / f"{_uuid.uuid4().hex}.md"
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(bootstrap.encode("utf-8"))
    os.replace(tmp, path)
    return path


def _wait_brief_consumed(brief_path, timeout: float) -> bool:
    """True once the SessionStart hook has injected + deleted the brief."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not brief_path.exists():
            return True
        time.sleep(1.0)
    return not brief_path.exists()


def _deliver_prompt(write_fn, read_fn, brief_path, nudge: str, fallback: str, boot_timeout: float) -> str:
    """Wake a freshly spawned session and confirm its first turn actually started.

    The old design typed the full multi-line bootstrap into the TUI blind after
    sleep(8) — racing the TUI's raw-mode init and hook/notification-driven turn
    starts, and losing the ENTIRE prompt (two clean confirmations 2026-08-06).
    Now:
      1. Wait for the SessionStart hook to consume the brief file — guaranteed
         context injection; ANY subsequent wake-up carries the brief with it.
      2. Type a one-line nudge (retry-safe, unlike an 8KB multi-line brief).
      3. Confirm a turn started ("esc to interrupt" in the terminal tail);
         retype up to 3 times.
      4. Brief never consumed (env not forwarded / stale hooks) → fall back to
         typing the full bootstrap, same verify loop.

    Returns a human-readable status for the spawn report — measured, not assumed.
    """
    if _wait_brief_consumed(brief_path, boot_timeout):
        payload, label = nudge, "nudge (brief injected via SessionStart)"
    else:
        payload, label = fallback, "full bootstrap typed (brief NOT consumed — bridge env not forwarded?)"
    import re as _re

    def _turn_active(tail: str) -> bool:
        # The TUI's busy render varies by version/state: some builds show
        # "esc to interrupt", current ones show a spinner line like
        # "✻ Elucidating… (27s · ↓ 1.8k tokens)" (verb is randomized — match
        # the stable timer/token grammar, not the word).
        low = tail.lower()
        if "esc to interrupt" in low or "· ↓ " in tail:
            return True
        return bool(_re.search(r"\(\d+s ·", tail))

    for attempt in range(1, 4):
        try:
            write_fn(payload)
        except Exception as exc:
            return f"FAILED to write {label}: {exc}"
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                tail = read_fn()
            except Exception:
                tail = ""
            if _turn_active(tail):
                return f"{label}; turn confirmed on attempt {attempt}"
            time.sleep(1.0)
    return (
        f"UNVERIFIED — {label} typed 3x, no turn observed. "
        f'Wake it manually: liteharness send-input <agent-id> "go"'
    )


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
    split_mode: bool = False,
    split_pane: str | None = None,
    # "vertical" = vertical divider = panes side-by-side, terminals stay TALL.
    # "horizontal" stacks flat bands where leaf chrome eats the height
    # (RULING, Ryan 2026-08-07: fleet splits must form a grid, columns first).
    split_direction: str = "vertical",
    cognitive: str | None = None,
) -> None:
    """Spawn a new Claude Code session.

    --split: spawn ALONGSIDE — split a canvas pane (default: the caller's own)
             and boot the agent visibly in the new split
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
    # LITESUITE_PROJECT_ID — canonical name per the spatial plan; matches the
    # TS write side and hooks.py's presence reader (drift fixed 2026-08-06).
    resolved_project = project_id or os.environ.get("LITESUITE_PROJECT_ID", "")
    resolved_tier = tier or "worker"
    if resolved_thread:
        context_env["LITEHARNESS_THREAD_ID"] = resolved_thread
    if resolved_workspace:
        context_env["LITEHARNESS_WORKSPACE_ID"] = resolved_workspace
    if resolved_project:
        context_env["LITESUITE_PROJECT_ID"] = resolved_project
    context_env["LITEHARNESS_TIER"] = resolved_tier
    if team:
        context_env["LITEHARNESS_TEAM"] = team

    # Keep the child's transcript on disk. A spawning session almost always has
    # CLAUDE_CODE_CHILD_SESSION set in its own environment, the child inherits it,
    # and Claude Code then SUPPRESSES transcript persistence for that child while
    # the parent keeps its own. Measured 2026-08-16: a spawned worker's session id
    # resolved to a directory holding only tool-results/ — no .jsonl at all — while
    # the spawner's transcript was still being written.
    #
    # The predicate, read out of the shipped binary rather than from the warning
    # text (it names the suppression case "persistence-suppressed"):
    #     if (env.CLAUDE_CODE_FORCE_SESSION_PERSISTENCE) return false;   // not suppressed
    #     if (!(env.CLAUDE_CODE_CHILD_SESSION && ... )) return false;
    # so ANY truthy value short-circuits it; "1" is the documented spelling.
    #
    # Why this matters more than a missing log: the transcript is the recovery
    # store of last resort. A file a seat wrote and lost is reconstructable from
    # its own Write/Edit chain — but only if that chain was recorded. A
    # transcript-less agent's artifacts are the ONLY copy that will ever exist,
    # and nothing in its session reports that it is in that state.
    #
    # Set it only when the caller has not already chosen a value, so an operator
    # who deliberately wants the default suppression can still get it.
    if not os.environ.get("CLAUDE_CODE_FORCE_SESSION_PERSISTENCE", "").strip():
        context_env["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"

    # Who spawned this agent. Without it a worker has no way to find its leader —
    # it can only guess "the orchestrator", which is wrong in any fleet more than
    # one tier deep. Prefer config.get_agent_id(): LITEHARNESS_AGENT_ID is unset in
    # a hand-opened session (the same gap that silently defaults tier to "worker"),
    # so relying on the env alone would drop the link for every top-level spawn.
    resolved_parent = os.environ.get("LITEHARNESS_AGENT_ID", "").strip() or (config.get_agent_id() or "")
    if resolved_parent:
        context_env["LITEHARNESS_SPAWNED_BY"] = resolved_parent

    # 🔴 AND THE CHILD MUST NOT INHERIT THE PARENT'S OWN ID.
    #
    # term_env is `{**os.environ, **context_env}`, so without this line the child
    # silently receives the SPAWNER's LITEHARNESS_AGENT_ID and every consumer that
    # trusts the env — config.get_agent_id(), the pi inbox extension, watch-auto —
    # adopts an identity belonging to a different agent.
    #
    # Measured live 2026-08-17: a pi session started ~5 minutes after a sibling in
    # the same shell inherited the SIBLING's id. Its inbox extension then polled
    # that sibling's mailbox, correctly and healthily, at 1500ms, for its entire
    # life. The sibling was already dead, so nothing was ever claimed and nothing
    # was ever delivered, while every diagnostic reported a running watcher.
    # Proven by a two-arm test: a message addressed to the sibling's id was
    # claimed and steered into the victim's context; the identical message
    # addressed to its own id was never touched.
    #
    # Blanked rather than deleted: the key must be PRESENT and empty so it
    # overrides the inherited value in the merge above. Every reader treats "" as
    # absent (config.py:175 `if explicit:`, extension.ts `|| ""`), so the child's
    # SessionStart hook mints the id from its own session — which is the one
    # identity that cannot belong to somebody else.
    context_env["LITEHARNESS_AGENT_ID"] = ""

    # `--name` used to reach the agent only as an instruction inside the typed
    # bootstrap ("register with --name X") — which vanished whenever the
    # bootstrap did (3 confirmations, 2026-08-06). The SessionStart hook honors
    # this env at FIRST registration instead: naming overrides are keyed by the
    # session UUID, which only the hook ever learns.
    if name:
        context_env["LITEHARNESS_REQUESTED_NAME"] = name

    # Cognitive architecture — MECHANICAL delivery (RULING, Ryan 2026-08-07:
    # "the orch flow is broken without them getting their correct prompts").
    # Discipline-based delivery ("tell the agent to read its file") failed
    # silently the same day the brief-below-the-fold bug did; a polymath spawn
    # now resolves its architecture file HERE and the SessionStart hook injects
    # the content. --cognitive overrides; otherwise --name is tried against the
    # library, so `--name Linus` alone is enough. A plain fleet name resolving
    # to nothing is the normal case, not an error.
    try:
        from . import prompts as _prompts_mod
        _cog = _prompts_mod.resolve_cognitive_file(cognitive or name or "", resolved_tier)
        if _cog is not None:
            context_env["LITEHARNESS_COGNITIVE_FILE"] = str(_cog)
        elif cognitive:
            print(f"WARNING: --cognitive '{cognitive}' matched no file in the "
                  f"cognitive-architectures library — agent boots WITHOUT an "
                  f"architecture. Check the name against the library tree.")
    except Exception as _cog_exc:  # noqa: BLE001 — never let resolution kill a spawn
        if cognitive:
            print(f"WARNING: cognitive architecture resolution failed ({_cog_exc!r}).")

    # Inside LiteSuite (Electron): route through canvas panel IPC when available
    if split_mode:
        spawn_mode = "split"
    else:
        spawn_mode = "canvas" if is_inside_litesuite() and not pty_mode else ("pty" if pty_mode else "terminal")

    # Tell the spawned agent its TRUE transport. The hook used to infer
    # spawn_mode from an inherited LITESUITE_BRIDGE_TOKEN — which the pty
    # daemon leaks from whichever agent started it, so daemon-spawned agents
    # tagged themselves "canvas" with no canvas session to attach to (the
    # bug-7 half-state, orch E2E 2026-08-06). Explicit beats inherited.
    context_env["LITEHARNESS_SPAWN_MODE"] = spawn_mode

    if spawn_mode == "split":
        # Spawn ALONGSIDE: split an existing canvas pane and boot the agent
        # visibly in the new split — the multiplexer pattern (Ryan,
        # 2026-08-06: "the whole point of the multiplexer is for the agents to
        # spawn alongside each other, in split panes"). Headless PTY is for
        # background work; when a human is watching, the fleet works on screen.
        my_id = os.environ.get("LITEHARNESS_AGENT_ID", "").strip() or (config.get_agent_id() or "")
        t0 = time.time()
        agents_dir = config.get_root() / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        known = {p.stem for p in agents_dir.glob("*.json")}

        launch_parts = ["claude"]
        if resolved_model:
            launch_parts.extend(["--model", resolved_model])
        launch_parts.extend(["--permission-mode", permission_mode or "bypassPermissions"])
        if additional_args:
            launch_parts.append(additional_args)
        launch_cmd = " ".join(launch_parts)
        if cwd:
            safe_dir = target_dir.replace("'", "''")
            launch_cmd = f"Set-Location -LiteralPath '{safe_dir}'; {launch_cmd}"

        split_res = _bridge_request("POST", "/canvas/split", {
            "paneId": split_pane or "self",
            "agentId": my_id,
            "direction": split_direction,
        })
        new_session = split_res.get("newSessionId")
        mode_label = "split pane"
        brief_via_env = False
        spawned_leaf_id = None
        actual_spawn_mode = "split"
        if new_session:
            # ── Env rides the TYPED COMMAND — the only delivery that exists here.
            # A split leaf is a live PowerShell; /pty/write types into it, so
            # `$env:` assignments evaluated by that shell are inherited by the
            # claude process. Nothing else delivers env on this path (proven
            # 2026-08-07: a --tier leader spawn booted with the WORKER preamble
            # because the tier existed nowhere the SessionStart hook could see).
            # This closes tier/name/mode/brief delivery for splits with NO app
            # rebuild. NOTE: this is /pty/write into node-pty stdin — not the
            # pccontrol paste path that pre-expands $env: before it lands.
            env_delivery = dict(context_env)
            env_delivery["LITESUITE_CANVAS_SESSION"] = str(new_session)
            new_leaf = split_res.get("newLeafId")
            if new_leaf:
                env_delivery["LITESUITE_LEAF_ID"] = str(new_leaf)
            if split_pane and not str(split_pane).startswith("self"):
                # Only when the caller named the panel do we KNOW the pane id —
                # the split response does not echo the resolved alias back.
                env_delivery["LITESUITE_PANE_ID"] = str(split_pane)
            # Brief travels as a FILE + env pointer so the SessionStart hook
            # injects it into context (the guaranteed-delivery channel) instead
            # of racing the TUI via a second typed message.
            if prompt:
                bootstrap = _generate_bootstrap(resolved_model, None, prompt, target_dir, env_delivery)
                if name:
                    bootstrap += (
                        f"\nNOTE: your name is already registered as {name} — "
                        f"do NOT pass --name when registering."
                    )
                brief_path = _write_spawn_brief(bootstrap)
                env_delivery["LITEHARNESS_SPAWN_BRIEF"] = str(brief_path)
                brief_via_env = True
            spawned_leaf_id = new_leaf
            if name:
                # Optimistic name-on-tab: the tab shows the agent's name the
                # instant the split exists, seconds before the agent boots and
                # its registration re-asserts it (hooks.register_presence).
                try:
                    _bridge_request("POST", "/canvas/rename-terminal", {
                        "sessionId": str(new_session),
                        "title": name,
                    })
                except Exception:
                    pass  # cosmetic — never let a rename fail a spawn
            env_sets = "; ".join(
                f"$env:{k}='{str(v).replace(chr(39), chr(39) * 2)}'"
                for k, v in env_delivery.items()
                if v and (k.startswith("LITEHARNESS_") or k.startswith("LITESUITE_"))
            )
            # Env hygiene for the agent process: pane shells inherit the APP's
            # environment, and an app launched from an agent tool shell carries
            # NO_COLOR=1 (Claude Code sets it in its tool shells) — which turned
            # every fleet terminal monochrome on 2026-08-07. Clear it in the
            # typed launch so the agent renders color regardless of how the
            # host app was started; COLORTERM advertises what xterm.js truly is.
            env_hygiene = (
                "Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue; "
                "$env:COLORTERM='truecolor'"
            )
            env_sets = f"{env_hygiene}; {env_sets}" if env_sets else env_hygiene
            if env_sets:
                launch_cmd = f"{env_sets}; {launch_cmd}"
            # Let the fresh shell finish booting before typing — a command
            # written into a pwsh that has not printed its prompt yet is lost.
            time.sleep(1.5)
            w = _bridge_request("POST", "/pty/write", {
                "session_id": new_session,
                "data": launch_cmd + "\r",
            })
            if not w.get("ok"):
                print(f"Error: could not launch claude in the split — {w.get('error', 'write failed')}")
                sys.exit(1)
        else:
            # Fallback: a fresh visible claude pane (auto-focused) — still on
            # screen alongside, just not a split of the caller's pane.
            print(f"  Split unavailable ({split_res.get('error', 'no result')}) — falling back to /canvas/claude")
            claude_res = _bridge_request("POST", "/canvas/claude", {
                "cwd": target_dir,
                **({"model": resolved_model} if resolved_model else {}),
            })
            new_session = claude_res.get("session_id")
            mode_label = "canvas pane (split fallback)"
            actual_spawn_mode = "canvas"
            if not new_session:
                print(f"Error: split spawn failed — {claude_res.get('error', claude_res)}")
                sys.exit(1)

        print(f"Spawned Claude session ({mode_label}, visible):")
        print(f"  Canvas session: {new_session}")
        print(f"  Directory: {target_dir}")

        # The agent registers itself via hooks; only then do we know its UUID.
        new_uuid = None
        deadline = time.time() + 90
        while time.time() < deadline:
            for p in agents_dir.glob("*.json"):
                if p.stem in known:
                    continue
                if p.stat().st_mtime >= t0 - 1:
                    new_uuid = p.stem
                    break
            if new_uuid:
                break
            time.sleep(2)

        if not new_uuid:
            print(
                "  Registration: NOT DETECTED within 90s — deliver the brief "
                "manually via inbox once the agent appears in discover"
            )
            return

        print(f"  Agent ID: {new_uuid}")
        from . import naming
        if name and not naming.get_override(new_uuid) and not naming.is_name_taken(name, exclude_id=new_uuid):
            naming.set_override(new_uuid, name)
        ppath = agents_dir / f"{new_uuid}.json"
        try:
            pdata = json.loads(ppath.read_text(encoding="utf-8"))
            if name:
                pdata["name"] = naming.get_name(new_uuid)
            if my_id and not pdata.get("spawned_by"):
                pdata["spawned_by"] = my_id
            if resolved_tier and resolved_tier != "worker":
                pdata["tier"] = resolved_tier
            pdata["spawn_mode"] = actual_spawn_mode
            pdata["canvas_session_id"] = new_session
            # Spatial identity: presence.pane_id is what the bridge's self-alias
            # resolver reads — writing it here means agents spawned INTO the
            # panel can themselves split it via paneId "self" (the multiplexer
            # compounds instead of dead-ending at one generation).
            if split_pane and not str(split_pane).startswith("self") and not pdata.get("pane_id"):
                pdata["pane_id"] = str(split_pane)
            if spawned_leaf_id and not pdata.get("leaf_id"):
                pdata["leaf_id"] = str(spawned_leaf_id)
            config.atomic_write_json(ppath, pdata)
        except (json.JSONDecodeError, OSError):
            pass

        if brief_via_env:
            # Brief rode LITEHARNESS_SPAWN_BRIEF (file + env in the typed launch).
            # Print-then-delete in the hook doubles as a delivery RECEIPT: the
            # file being GONE proves the hook read it; still-present means the
            # injection never ran (3/3 idle boots shipped under an unverified
            # "delivered" claim on 2026-08-07 — never assert what you can check).
            if brief_path is not None and Path(brief_path).exists():
                print(
                    "  Brief: ⚠ NOT CONSUMED — the SessionStart hook never read "
                    f"{brief_path}; re-deliver via inbox: python -m liteharness.cli "
                    f'send {new_uuid} "<brief>" --from {my_id or "<your-id>"}'
                )
            else:
                print("  Brief: CONSUMED (SessionStart receipt — file unlinked by the hook)")
        else:
            # Brief via INBOX — the notification-carries-the-work channel (the
            # agent's watcher auto-starts; proven reliable 2026-08-06). Typed
            # delivery would race the TUI, and env delivery is impossible
            # post-boot on the /canvas/claude fallback. Generate WITHOUT --name:
            # the override is already set spawner-side, and a brief that says
            # "register --name X" invites the agent to overwrite it with an
            # improvised value (TrueSplit registered itself Worker-C, live).
            bootstrap = _generate_bootstrap(resolved_model, None, prompt, target_dir, context_env)
            if name:
                bootstrap += f"\nNOTE: your name is already registered as {name} — do NOT pass --name when registering."
            msg_id = inbox.send(
                from_agent=my_id or "spawner",
                to_agent=new_uuid,
                body=bootstrap,
            )
            print(f"  Brief delivered via inbox ({msg_id})")
        if name:
            print(f"  Name: {name}")
        return

    if spawn_mode == "canvas":
        # Canvas mode: create a terminal pane inside LiteSuite via Agent Bridge.
        # The bridge's /pty/create creates a node-pty session + canvas panel.
        # The prompt travels as a brief FILE + env pointer (see _deliver_prompt)
        # — typing it raced TUI startup and could lose the whole prompt, and
        # embedding it in argv invites cmd.exe quote mangling. The `env` field
        # needs the bridge to forward it (agent-bridge fix 2026-08-06); against
        # an older running app the brief poll times out and the fallback types
        # the full bootstrap exactly as before.
        bootstrap = _generate_bootstrap(resolved_model, name, prompt, target_dir, context_env)
        brief_path = _write_spawn_brief(bootstrap)
        context_env["LITEHARNESS_SPAWN_BRIEF"] = str(brief_path)

        canvas_parts = ["claude"]
        if resolved_model:
            canvas_parts.extend(["--model", resolved_model])
        canvas_parts.extend(["--permission-mode", permission_mode or "bypassPermissions"])
        if additional_args:
            canvas_parts.append(additional_args)
        result = _bridge_request("POST", "/pty/create", {
            "shell": " ".join(canvas_parts),
            "cwd": target_dir,
            "env": context_env,
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

        # Unify canvas identity: the agent's own register must carry the pane
        # mapping, or click-to-terminal can never resolve its live presence.
        # Rides the nudge/fallback because session_id only exists post-create,
        # after the brief file is already written.
        canvas_line = f" IMPORTANT: when you register (step 2), ALSO pass: --canvas-session {session_id}"

        def _canvas_write(text: str) -> None:
            r = _bridge_request("POST", "/pty/write", {
                "session_id": session_id,
                "data": text + "\r",
            })
            if not r.get("ok"):
                raise RuntimeError(r.get("error", "pty/write failed"))

        def _canvas_read() -> str:
            r = _bridge_request("POST", "/pty/read", {"session_id": session_id, "lines": 40})
            return str(r.get("output", "")) if r.get("ok") else ""

        status = _deliver_prompt(
            _canvas_write, _canvas_read, brief_path,
            nudge=_SPAWN_NUDGE + canvas_line,
            fallback=bootstrap + canvas_line,
            boot_timeout=15,
        )
        print(f"  Prompt delivery: {status}")
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

        # Brief file + env pointer BEFORE the daemon spawn — the session must
        # boot with LITEHARNESS_SPAWN_BRIEF already set for the SessionStart
        # hook to inject it. exec_cmd spawns run arbitrary processes with no
        # hooks, so they get no brief.
        brief_path = None
        bootstrap = ""
        if not exec_cmd:
            bootstrap = _generate_bootstrap(resolved_model, name, prompt, target_dir, context_env)
            brief_path = _write_spawn_brief(bootstrap)
            context_env["LITEHARNESS_SPAWN_BRIEF"] = str(brief_path)

        agent_id = f"pty-{int(time.time())}-{os.getpid()}"
        # The daemon session id, passed through so the agent's own presence
        # records it — that link is what lets `kill <UUID>` find the daemon
        # session (the provisional pty-* record and the real UUID record were
        # otherwise never joined, making agents unkillable by their UUID).
        context_env["LITEHARNESS_PROVISIONAL_ID"] = agent_id
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
            # Daemon commands are HYPHENATED. The old delivery here sent
            # "send_input" (underscore), got {"error": "unknown command"} back
            # on EVERY spawn, and swallowed it (success-only print) — the PTY
            # bootstrap was never delivered at all. The "spawn race" of
            # 2026-08-06 was this silent dispatch mismatch.
            def _pty_write(text: str) -> None:
                r = pty_daemon.send_command({
                    "cmd": "send-input",
                    "agent_id": agent_id,
                    "text": text,
                })
                if not r.get("ok"):
                    raise RuntimeError(r.get("error", "send-input failed"))

            def _pty_read() -> str:
                r = pty_daemon.send_command({
                    "cmd": "read-output",
                    "agent_id": agent_id,
                    "lines": 40,
                })
                return str(r.get("output", "")) if r.get("ok") else ""

            status = _deliver_prompt(
                _pty_write, _pty_read, brief_path,
                nudge=_SPAWN_NUDGE,
                fallback=bootstrap,
                boot_timeout=45,
            )
            print(f"  Prompt delivery: {status}")
    else:
        # Terminal mode: spawn via Windows Terminal (visible, detached).
        # context_env rides the process environment (wt → shell → claude) so
        # requested-name/tier/spawned_by reach the hooks; the prompt itself
        # stays on argv here — no PTY channel exists to type into.
        term_env = {**os.environ, **context_env}
        if sys.platform == "win32":
            spawn_cmd = f'start wt -d "{target_dir}" cmd /k {claude_str}'
            subprocess.Popen(
                spawn_cmd, shell=True, env=term_env,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                f"cd {target_dir} && {claude_str}",
                shell=True, start_new_session=True, env=term_env,
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
        attribution = w.get("shell_attribution", "")
        if attribution == "none":
            print("  Shells: (unattributed — shared WT process, no binding evidence)")
        elif attribution:
            print(f"  Shells ({attribution}):")
        for shell in w.get("shells", []):
            print(f"    Shell: {shell.get('name', '')} (pid {shell.get('pid', '?')})")


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
    verify_paste: bool = True,
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
            verify_paste=verify_paste,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not result or not result.get("ok"):
        result = result or {}
        error = result.get("error", "unknown")
        print(f"Error: DELIVERY FAILED ({error})", file=sys.stderr)
        holder = result.get("foreground_holder") or {}
        if holder:
            print(
                f"  foreground held by {holder.get('process', '?')} "
                f"pid={holder.get('pid', '?')} \"{holder.get('title', '')}\"",
                file=sys.stderr,
            )
        if result.get("readback_preview"):
            print(f"  composer readback: {result['readback_preview']!r}", file=sys.stderr)
        print(
            "  Nothing was submitted. The message is NOT delivered — treat it as "
            "DEFERRED and retry once the Codex Desktop window can take focus.",
            file=sys.stderr,
        )
        sys.exit(1)

    click = result.get("click", {})
    window = result.get("window", {})
    mode = "Dry run" if dry_run else ("Probe" if probe_only else "Sent")
    verified = ""
    if not dry_run and not probe_only:
        verified = (
            f"; foreground_verified={result.get('foreground_verified')}"
            f" paste_verified={result.get('paste_verified')}"
        )
    print(
        f"{mode} for Codex Desktop {window.get('handle')} "
        f"at ({click.get('x')}, {click.get('y')}); "
        f"restore_mouse={result.get('restore_mouse', result.get('restored_mouse'))}"
        f"{verified}"
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

    agent_id = current_codex_agent_id()
    # The inbox watcher reads the per-agent target file
    # (codex_inbox_<slug>_target.json), not the legacy codex_inbox_target.json.
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in agent_id)
    target_dir = Path.home() / ".codex" / "memories" / "liteharness"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"codex_inbox_{slug}_target.json"
    payload = {
        "mode": "codex-desktop",
        "window_handle": selected.get("handle"),
        "window_title": selected.get("title", ""),
        "rect": selected.get("rect"),
        "agent_id": agent_id,
        "captured_at": time.time(),
        "captured_via": "cli-codex-desktop-target",
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
    """Kill an agent session — universal routing, not just the pty daemon.

    Accepts a real agent UUID, a daemon session id (pty-*), or a canvas
    provisional id (canvas-*). Resolution order:
      1. presence.canvas_session_id      -> bridge DELETE /pty/<sid>
      2. daemon id (given or linked)     -> pty daemon kill
      3. presence.session_pid            -> OS kill (last resort)
    The old daemon-only version reported "session not found" for every
    canvas agent and every UUID (the provisional pty-* record and the real
    UUID presence were never linked) — reaping then required a manual
    taskkill by session_pid (orch E2E, 2026-08-06).
    """
    from . import pty_daemon

    presence: dict = {}
    presence_path = config.get_root() / "agents" / f"{agent_id}.json"
    if presence_path.exists():
        try:
            presence = json.loads(presence_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            presence = {}

    killed_via: list[str] = []

    # 1. Canvas session via the bridge
    canvas_sid = presence.get("canvas_session_id") or (
        agent_id[len("canvas-"):] if agent_id.startswith("canvas-") else None
    )
    if canvas_sid:
        result = _bridge_request("DELETE", f"/pty/{canvas_sid}", None)
        if result.get("success") or result.get("ok"):
            killed_via.append(f"canvas session {canvas_sid}")

    # 2. Daemon session (direct id or the linked provisional)
    daemon_id = agent_id if agent_id.startswith("pty-") else presence.get("provisional_id", "")
    if daemon_id and str(daemon_id).startswith("pty-") and pty_daemon.is_daemon_running():
        result = pty_daemon.send_command({"cmd": "kill", "agent_id": daemon_id})
        if result.get("ok"):
            killed_via.append(f"daemon session {daemon_id}")

    # 3. OS-level fallback by owning PID
    if not killed_via:
        session_pid = presence.get("session_pid")
        if session_pid:
            try:
                import subprocess as _sp
                if sys.platform == "win32":
                    _sp.run(["taskkill", "/PID", str(session_pid), "/T", "/F"],
                            capture_output=True, timeout=15)
                else:
                    os.kill(int(session_pid), 15)
                killed_via.append(f"session_pid {session_pid}")
            except Exception:
                pass

    if killed_via:
        print(f"Killed {agent_id} via {', '.join(killed_via)}")
    else:
        print(
            f"Error: no kill route found for {agent_id} — no canvas session, "
            f"no daemon session, no live session_pid in presence."
        )
        sys.exit(1)


def _dedupe_by_session_pid(agents: list) -> tuple:
    """Collapse rows that describe ONE process. Returns (kept, superseded).

    `/resume` boots with a throwaway session id, SessionStart registers it, then
    the resume adopts the real id and registers AGAIN -- two rows, one process,
    ~13 seconds apart (measured 2026-08-19: pid 61112 GrimShard 14:56:31 then
    Sentinel 14:56:45; pid 269264 LongRivet 14:56:08 then OpenBolt 14:56:20).

    The existing liveness check cannot catch this: BOTH rows carry the same
    LIVE pid, so `_pid_alive` is correctly True for each. A filter that asks
    "is this dead?" can never collapse two rows that are both alive.

    The LATER registration wins -- that is the id the session actually adopted;
    the earlier one is the throwaway it booted with.

    Rows with no session_pid are NEVER grouped. They would all collapse into a
    single bucket and the roster would lose every agent whose owner is unknown,
    which is the opposite failure and a worse one.
    """
    by_pid: dict = {}
    kept: list = []
    for a in agents:
        pid = a.get("session_pid")
        if not pid:
            kept.append(a)          # unknown owner: never grouped
            continue
        prev = by_pid.get(pid)
        if prev is None:
            by_pid[pid] = a
            continue
        # keep the later registration; the earlier is the resume throwaway
        if str(a.get("registered_at") or "") > str(prev.get("registered_at") or ""):
            by_pid[pid] = a
    superseded = [a for a in agents
                  if a.get("session_pid") and by_pid.get(a["session_pid"]) is not a]
    kept.extend(by_pid.values())
    return kept, superseded


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
    # Collapse /resume double-registrations. NOT silent: a dropped row that
    # nothing reports is how a roster gets quietly "fixed" into being wrong
    # in a new way.
    superseded: list = []
    if not include_all:
        agents, superseded = _dedupe_by_session_pid(agents)
        agents.sort(key=lambda a: a.get("_age_seconds", float("inf")))
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
    if superseded:
        from . import naming as _naming
        print(f"  ({len(superseded)} superseded by a later registration on the same PID:",
              ", ".join(f"{_naming.get_name(a.get('agent_id',''))}/{a.get('session_pid')}"
                        for a in superseded) + ")")
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
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
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
        print("  install-statusline             Install ONLY the status line (for callers that")
        print("                                 already wrote their own hooks, e.g. the wizard)")
        print("  update-scripts [--cli name]    Update watcher scripts for Codex/Copilot")
        print("  status                         Show status")
        print("  send <to> <message>            Send a message")
        print("  list                           List inbox messages")
        print("  inbox [N] [--all] [--agent ID] Read-only inbox view: full bodies, new+cur+done, newest N")
        print("  discover [count]               Discover active agents")
        print("  spawn [options]                Spawn a new Claude Code session")
        print("  sessions <cmd> [options]       Save/restore terminal agent sessions")
        # 🔴 TWO HELP TEXTS FOR ONE COMMAND. Keep them equal to register's own
        # usage line (search "Usage: liteharness register"), because they drift and
        # THE FRIENDLIER ONE IS THE ONE PEOPLE READ. This block was missing
        # --takeover, --session-pid and --canvas-session; an agent read it to learn
        # the register flags and was told --session-pid does not exist, hours after
        # it was added. Same family as a doc that describes the thing rather than
        # being generated from it: the authoritative description and the thing
        # itself, disagreeing, with the approachable one wrong.
        print("  register --agent-id ID [--cli CLI] [--model MODEL] [--name NAME] [--tier TIER] [--team TEAM]")
        print("           [--takeover] [--session-pid PID]")
        print("           [--pane-id PANE] [--leaf-id LEAF] [--session-id SID] [--thread-id TID]")
        print("           [--workspace-id WID] [--project-id PID] [--canvas-session CSID]")
        print("                                 Update agent presence info with spatial awareness data")
        print("  query-patterns [--top N] [--format text|json] [--query STR]")
        print("                                 Query task patterns (BM25)")
        print("  embed-query --query STR [--top N] [--format text|json]")
        print("                                 Hybrid RAG query (requires HARNESS_MCP_PORT)")
        print("  rag <action> [query] [--top-k N] [--scope S] [--tier T] [--source S]")
        print("                                 Multi-strategy code RAG (help|status|index|query|...)")
        print("  record-pattern --outcome <success|failure|stuck|unknown> [--agent-id ID] [--task DESC]")
        print("                 [--supersedes id[,id...]]  # retire patterns this one corrects (pattern_ids preferred)")
        print("                                 Records are BORN unverified — no level flag exists here")
        print("  verify-pattern --pattern-id ID --level human|judgement|gauntlet --actor WHO")
        print("                 --evidence-ref|--delegation-ref|--run-id EVIDENCE  # per level, required")
        print("  revoke-pattern --pattern-id ID --reason WHY --prior-attestation-id AID --actor WHO")
        print("  librarian-tick --job-id ID     Closed-app librarian runner (occurrence-ledger arbitrated)")
        print("  librarian-install --mode app|os|print [--remove]")
        print("                                 Offer the librarian as a mechanism; print does nothing")
        print("                                 Record a task pattern")
        print("                                 --task -  reads the description from stdin (opt-in;")
        print("                                 omitting --task never reads stdin and never blocks)")
        print("  install --list                 List supported CLIs + auto-detection results")
        print("  install --cli <name> [--path PATH] [--dry-run]")
        print("                                 Install skills+agents into a CLI's canonical dir (opt-in)")
        print("  workflow run <path> [--model NAME]")
        print("  workflow run --global <name> [--model NAME]")
        print("                                 Launch Claude with the Workflow tool to run a workflow file")
        print("  memory-nudge [--on|--off] [--cadence N]")
        print("                                 Toggle the every-other-turn durable-knowledge nudge (off by default)")
        print()
        print("Spawn options:")
        print("  --model <name>                 Model: opus (=Opus 5), opus-5, opus-1m, opus-200k, opus-4.8, fable, sonnet, sonnet-5, haiku, or full ID")
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
    elif cmd == "install-statusline":
        cmd_install_statusline()
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
            print(
                "Usage: liteharness send <to-agent-id> <message> [--from <your-id>] "
                "[--thread-id <id>] [--force]\n"
                "   or: liteharness send <to-agent-id> --body-file <path> [--from <your-id>]\n"
                "       Use --body-file for anything containing code, backticks or $ -- "
                "the shell edits an inline body silently and still reports success."
            )
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
        # --body-file exists because THE BODY IS PASSED THROUGH A SHELL and the shell
        # edits it silently. Two agents hit this within one hour on 2026-08-13, both
        # using backticks inside a double-quoted string: bash ran the contents as a
        # command substitution and DELETED them, the send reported success, and only
        # reading the delivered copy showed the gap. `$VAR` expands the same way. Any
        # body containing code, paths or shell metacharacters should be written to a
        # file and passed by path -- there is then no shell between the text and the
        # maildir, so there is nothing to mangle rather than a rule to remember.
        flags = {"--project": None, "--from": None, "--thread-id": None, "--body-file": None}
        bool_flags = {"--force": False}
        msg_tokens = []
        rest = sys.argv[3:]
        i = 0
        while i < len(rest):
            token = rest[i]
            if token in bool_flags:
                bool_flags[token] = True
                i += 1
                continue
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
        body_file = flags["--body-file"]
        if body_file:
            # Ambiguity is refused rather than resolved by precedence: a caller who
            # passed both meant one of them, and silently dropping the other is the
            # emptied-body failure again wearing a different hat.
            if msg_parts:
                print(
                    "Error: --body-file and an inline message were both given. "
                    "Pass one. Nothing was sent.",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                msg_parts = Path(body_file).read_text(encoding="utf-8").strip()
            except OSError as exc:
                print(f"Error: cannot read --body-file {body_file!r}: {exc}. Nothing was sent.",
                      file=sys.stderr)
                sys.exit(1)
        # Fail closed. A send that reports success on a body it emptied is worse than a
        # send that refuses: the caller believes the message landed and never re-sends.
        if not msg_parts:
            print(
                "Error: refusing to send an empty message body.\n"
                "  Usage: liteharness send <to-agent-id> <message> [--from <id>] "
                "[--thread-id <id>]"
            )
            sys.exit(1)
        cmd_send(sys.argv[2], msg_parts, project, from_id, send_thread_id, bool_flags["--force"])
    elif cmd == "list":
        cmd_list()
    elif cmd == "inbox":
        inbox_count = 10
        inbox_agent = None
        inbox_all = "--all" in sys.argv
        if "--agent" in sys.argv:
            idx = sys.argv.index("--agent")
            if idx + 1 < len(sys.argv):
                inbox_agent = sys.argv[idx + 1]
        for tok in sys.argv[2:]:
            if tok.isdigit():
                inbox_count = int(tok)
                break
        cmd_inbox(inbox_count, inbox_agent, inbox_all)
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
        reg_canvas_session = None
        reg_takeover = False
        reg_session_pid = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--takeover":
                reg_takeover = True
                i += 1
                continue
            if sys.argv[i] == "--session-pid" and i + 1 < len(sys.argv):
                try:
                    reg_session_pid = int(sys.argv[i + 1])
                except ValueError:
                    print(f"Error: --session-pid must be an integer, got {sys.argv[i + 1]!r}")
                    sys.exit(1)
                i += 2
                continue
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
            elif sys.argv[i] == "--canvas-session" and i + 1 < len(sys.argv):
                reg_canvas_session = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        if not reg_agent_id:
            print("Usage: liteharness register --agent-id ID [--cli CLI] [--model MODEL] [--name NAME] [--tier TIER] [--team TEAM] [--takeover] [--session-pid PID] [--pane-id PANE] [--leaf-id LEAF] [--session-id SID] [--thread-id TID] [--workspace-id WID] [--project-id PID] [--canvas-session CSID]")
            sys.exit(1)
        cmd_register(reg_agent_id, reg_cli, reg_model, reg_name, reg_tier, reg_team, reg_pane_id, reg_leaf_id, reg_session_id, reg_thread_id, reg_workspace_id, reg_project_id, canvas_session=reg_canvas_session, takeover=reg_takeover, session_pid=reg_session_pid)
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
        verify_paste = "--no-verify-paste" not in sys.argv
        window_handle = None
        click_x = None
        click_y = None
        text_parts = []
        i = 2
        while i < len(sys.argv):
            arg = sys.argv[i]
            if arg in ("--no-submit", "--dry-run", "--probe-only", "--no-restore-mouse", "--no-verify-paste"):
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
            print("Usage: liteharness codex-desktop-send [--dry-run] [--probe-only] [--no-submit] [--no-verify-paste] [--handle N] [--x N --y N] <text>")
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
            verify_paste=verify_paste,
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
    elif cmd == "memory-nudge":
        # Toggle / tune the every-other-turn durable-knowledge nudge emitted by
        # the UserPromptSubmit hook (liteharness.hooks memory-nudge). Distinct
        # from the nudge-bot (which drives idle agents via LM Studio).
        mn_cfg = config.load()
        mn = mn_cfg.get("memory_nudge")
        if not isinstance(mn, dict):
            mn = {}
        changed = False
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--on":
                mn["enabled"] = True
                changed = True
                i += 1
            elif sys.argv[i] == "--off":
                mn["enabled"] = False
                changed = True
                i += 1
            elif sys.argv[i] == "--cadence" and i + 1 < len(sys.argv):
                try:
                    cadence = int(sys.argv[i + 1])
                except ValueError:
                    print("Error: --cadence must be an integer")
                    sys.exit(1)
                if cadence < 1:
                    print("Error: --cadence must be >= 1")
                    sys.exit(1)
                mn["cadence"] = cadence
                changed = True
                i += 2
            else:
                i += 1
        if changed:
            mn_cfg["memory_nudge"] = mn
            config.save(mn_cfg)
        enabled = bool(mn.get("enabled", False))
        cadence = int(mn.get("cadence", 2))
        state = "ON" if enabled else "OFF"
        print(f"memory-nudge: {state} (cadence={cadence} — pointer every {cadence} UserPromptSubmit turn(s))")
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
        sp_split = False
        sp_split_pane = None
        sp_split_dir = "vertical"
        sp_cognitive = None
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
            elif sys.argv[i] == "--split":
                sp_split = True
                i += 1
            elif sys.argv[i] == "--pane" and i + 1 < len(sys.argv):
                sp_split_pane = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--direction" and i + 1 < len(sys.argv):
                sp_split_dir = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--cognitive" and i + 1 < len(sys.argv):
                sp_cognitive = sys.argv[i + 1]
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
            split_mode=sp_split, split_pane=sp_split_pane,
            split_direction=sp_split_dir, cognitive=sp_cognitive,
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
        rp_supersedes: list[str] = []
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
            elif sys.argv[i] == "--supersedes" and i + 1 < len(sys.argv):
                rp_supersedes.extend(
                    t.strip() for t in sys.argv[i + 1].split(",") if t.strip()
                )
                i += 2
            else:
                # STRICT: an unknown flag silently eaten here is how a caller
                # comes to believe it self-promoted a record. There is no
                # level flag on record BY DESIGN — patterns are born
                # unverified; promotion is an attestation via verify-pattern.
                print(
                    f"[record-pattern] unknown argument: {sys.argv[i]} "
                    "(record accepts no verification level — use verify-pattern)",
                    file=sys.stderr,
                )
                sys.exit(2)
        cmd_record_pattern(
            outcome=rp_outcome,
            agent_id=rp_agent_id,
            task_desc=rp_task,
            project=rp_project,
            supersedes=rp_supersedes,
        )
    elif cmd == "verify-pattern":
        vp: dict[str, str | None] = {
            "--pattern-id": None, "--level": None, "--actor": None,
            "--evidence-ref": None, "--delegation-ref": None, "--run-id": None,
            "--project": None,
        }
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] in vp and i + 1 < len(sys.argv):
                vp[sys.argv[i]] = sys.argv[i + 1]
                i += 2
            else:
                print(f"[verify-pattern] unknown argument: {sys.argv[i]}", file=sys.stderr)
                sys.exit(2)
        if not vp["--pattern-id"] or not vp["--level"]:
            print(
                "Usage: liteharness verify-pattern --pattern-id ID --level "
                "human|judgement|gauntlet --actor WHO --evidence-ref/"
                "--delegation-ref/--run-id EVIDENCE [--project ROOT]",
                file=sys.stderr,
            )
            sys.exit(2)
        cmd_verify_pattern(
            pattern_id=vp["--pattern-id"],
            level=vp["--level"],
            actor=vp["--actor"],
            evidence_ref=vp["--evidence-ref"],
            delegation_ref=vp["--delegation-ref"],
            run_id=vp["--run-id"],
            project=vp["--project"],
        )
    elif cmd == "revoke-pattern":
        rv: dict[str, str | None] = {
            "--pattern-id": None, "--reason": None,
            "--prior-attestation-id": None, "--actor": None, "--project": None,
        }
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] in rv and i + 1 < len(sys.argv):
                rv[sys.argv[i]] = sys.argv[i + 1]
                i += 2
            else:
                print(f"[revoke-pattern] unknown argument: {sys.argv[i]}", file=sys.stderr)
                sys.exit(2)
        if not rv["--pattern-id"]:
            print(
                "Usage: liteharness revoke-pattern --pattern-id ID --reason WHY "
                "--prior-attestation-id AID --actor WHO [--project ROOT]",
                file=sys.stderr,
            )
            sys.exit(2)
        cmd_revoke_pattern(
            pattern_id=rv["--pattern-id"],
            reason=rv["--reason"],
            prior_attestation_id=rv["--prior-attestation-id"],
            actor=rv["--actor"],
            project=rv["--project"],
        )
    elif cmd == "librarian-tick":
        lt_job_id = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--job-id" and i + 1 < len(sys.argv):
                lt_job_id = sys.argv[i + 1]
                i += 2
            else:
                print(f"[librarian-tick] unknown argument: {sys.argv[i]}", file=sys.stderr)
                sys.exit(2)
        if not lt_job_id:
            print("Usage: liteharness librarian-tick --job-id <id>", file=sys.stderr)
            sys.exit(2)
        from .librarian_tick import run_tick
        sys.exit(run_tick(lt_job_id))
    elif cmd == "librarian-install":
        li_mode = None
        li_remove = False
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--mode" and i + 1 < len(sys.argv):
                li_mode = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--remove":
                li_remove = True
                i += 1
            else:
                print(f"[librarian-install] unknown argument: {sys.argv[i]}", file=sys.stderr)
                sys.exit(2)
        if not li_mode:
            print(
                "Usage: liteharness librarian-install --mode app|os|print [--remove]",
                file=sys.stderr,
            )
            sys.exit(2)
        from .librarian_install import install as _librarian_install
        sys.exit(_librarian_install(li_mode, remove=li_remove))
    elif cmd == "rag":
        cmd_rag()
    elif cmd == "install":
        cmd_install()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()

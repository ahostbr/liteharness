"""T384: init's hook setup works on first run and preserves custom bindings."""
import copy
import json
from pathlib import Path

from liteharness import cli


CHECK = "python -m liteharness.hooks check"
REGISTER = "python -m liteharness.hooks register"


def matcher(command, **fields):
    return {"hooks": [{"type": "command", "command": command, "timeout": 1234}], **fields}


def check_events(settings):
    return {
        event for event, groups in settings.get("hooks", {}).items()
        if any(h.get("command") == CHECK for group in groups for h in group.get("hooks", []))
    }


def install_in(home, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert cli._install_claude_hooks()
    return home / ".claude" / "settings.json"


def test_fresh_hook_setup_has_two_default_check_events_and_is_idempotent(tmp_path, monkeypatch):
    path = install_in(tmp_path, monkeypatch)
    assert check_events(json.loads(path.read_text())) == {"SessionStart", "PostToolUse"}
    first = path.read_bytes()
    install_in(tmp_path, monkeypatch)
    assert path.read_bytes() == first


def test_existing_hook_setup_preserves_user_hooks_and_is_idempotent(tmp_path, monkeypatch):
    # Independent user contract: customized matcher metadata, timeout, mixed tool
    # entries, third check event, and compaction registration must all survive.
    custom = {
        "UserPromptSubmit": [matcher(CHECK, matcher="", user_note="early inbox"),
                             matcher("python user_memory_reminder.py")],
        "PreCompact": [matcher(REGISTER, matcher="manual")],
        "PostCompact": [matcher(REGISTER)],
    }
    initial = {
        "hooks": copy.deepcopy(custom),
        "statusLine": {"type": "command", "command": "python user_status.py"},
        "userPreference": "retain-me",
    }
    source = tmp_path / "original-settings.json"
    source.write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
    original_bytes = source.read_bytes()
    home = tmp_path / "installed-home"
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(original_bytes)

    install_in(home, monkeypatch)
    installed = json.loads(path.read_text())
    assert check_events(installed) == {"SessionStart", "PostToolUse", "UserPromptSubmit"}
    for event, value in custom.items():
        assert installed["hooks"][event] == value
    assert installed["statusLine"] == initial["statusLine"]
    assert installed["userPreference"] == initial["userPreference"]
    first = path.read_bytes()
    install_in(home, monkeypatch)
    assert path.read_bytes() == first
    assert source.read_bytes() == original_bytes

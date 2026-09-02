"""A re-registration must never lower an agent's tier, model or name.

🔴 THE DEFECT, MEASURED 2026-09-02 ON THE LIVE FLEET. Sentinel's own registry row
went `orchestrator` -> `worker` and `claude-fable-5-1` -> `unknown` between
14:05:16Z and 14:06:07Z, and the heartbeat then carried the demoted values
onward. `liteharness discover` printed him as a worker; the JSON on disk agreed
with itself; nothing errored and nothing warned. (OpenBolt's catch, inbox
message 0c171ad2 — found only because two `discover` runs two minutes apart
disagreed about the orchestrator's tier.)

The cause was not the preservation logic, which was already there and correct:

    tier = os.environ.get("LITEHARNESS_TIER") or existing.get("tier") or "worker"

It was where `existing` COMES FROM. A read that failed produced `{}`, which is
indistinguishable from "this agent has no row", and every default below then
fires. Two writers made that likely: `liteharness register` wrote the presence
file with a non-atomic `write_text` while hooks wrote atomically, so a hook
re-registering during a CLI register could read a complete document followed by
the tail of a longer one.

So there are two fixes and both are tested here: the reader SALVAGES a torn file
instead of treating it as absent, and the CLI writer no longer produces one.
"""

from __future__ import annotations

import json

from liteharness.hooks import _read_presence, _write_json_atomic

ORCHESTRATOR = {
    "agent_id": "bfc5e812-ec7b-4588-b74f-769e7bbe2eb1",
    "name": "Sentinel",
    "tier": "orchestrator",
    "model": "claude-fable-5-1",
    "cli": "claude-code",
    "started_at": "2026-09-02T09:00:00+00:00",
}

# The exact corruption shape `_write_json_atomic` documents: a whole document
# followed by the tail of a longer one.
TORN = json.dumps(ORCHESTRATOR, indent=2) + '\nsession_pid": 342828\n}\n'


class TestReadPresence:
    def test_reads_an_intact_row(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text(json.dumps(ORCHESTRATOR), encoding="utf-8")
        assert _read_presence(path) == ORCHESTRATOR

    def test_missing_row_is_empty(self, tmp_path):
        # The one case that legitimately licenses defaults.
        assert _read_presence(tmp_path / "nope.json") == {}

    def test_salvages_a_torn_row_rather_than_reporting_absence(self, tmp_path):
        """🔴 The arm the whole fix exists for.

        Before: json.loads raises, the caller sees {}, and tier/model default.
        The salvaged prefix is not a guess — it is a real presence file written
        by a real registration, with someone else's tail stuck to it.
        """
        path = tmp_path / "a.json"
        path.write_text(TORN, encoding="utf-8")
        got = _read_presence(path)
        assert got.get("tier") == "orchestrator"
        assert got.get("model") == "claude-fable-5-1"
        assert got.get("name") == "Sentinel"

    def test_garbage_with_no_agent_id_is_not_salvaged(self, tmp_path):
        # A salvage that accepts anything would invent a row. The prefix must
        # look like a presence file, not merely like valid JSON.
        path = tmp_path / "a.json"
        path.write_text('{"unrelated": 1}\ntrailing', encoding="utf-8")
        assert _read_presence(path) == {}

    def test_empty_file_is_empty(self, tmp_path):
        path = tmp_path / "a.json"
        path.write_text("", encoding="utf-8")
        assert _read_presence(path) == {}


class TestHookReRegisterPreservesIdentity:
    def _row_after_hook_style_merge(self, existing: dict, env_tier: str | None) -> dict:
        """The exact expressions both hook write sites use, over a read result."""
        model = "unknown"  # what config.get_model() yields when detection fails

        def prefer_known(new_value: str, old_value: str) -> str:
            if new_value and new_value != "unknown":
                return new_value
            if old_value and old_value != "unknown":
                return old_value
            return new_value or old_value or "unknown"

        return {
            "tier": env_tier or existing.get("tier") or "worker",
            "model": prefer_known(model, existing.get("model", "")),
            "name": existing.get("name") or "",
        }

    def test_orchestrator_survives_a_re_register_that_names_nothing(self, tmp_path):
        """The reported failure, end to end: hook fires with no tier and no model."""
        path = tmp_path / "a.json"
        _write_json_atomic(path, ORCHESTRATOR)

        merged = self._row_after_hook_style_merge(_read_presence(path), env_tier=None)
        assert merged["tier"] == "orchestrator"
        assert merged["model"] == "claude-fable-5-1"
        assert merged["name"] == "Sentinel"

    def test_orchestrator_survives_even_when_the_row_is_torn(self, tmp_path):
        """The same, through the corruption that actually caused it."""
        path = tmp_path / "a.json"
        path.write_text(TORN, encoding="utf-8")

        merged = self._row_after_hook_style_merge(_read_presence(path), env_tier=None)
        assert merged["tier"] == "orchestrator", "a torn read demoted a live orchestrator"
        assert merged["model"] == "claude-fable-5-1"

    def test_an_explicit_tier_still_wins(self, tmp_path):
        # Preserving must not become "ignoring": a payload that NAMES a tier is
        # the one thing allowed to change it.
        path = tmp_path / "a.json"
        _write_json_atomic(path, ORCHESTRATOR)
        merged = self._row_after_hook_style_merge(_read_presence(path), env_tier="leader")
        assert merged["tier"] == "leader"


class TestAtomicWrite:
    def test_write_leaves_no_temp_file_behind(self, tmp_path):
        path = tmp_path / "a.json"
        _write_json_atomic(path, ORCHESTRATOR)
        assert json.loads(path.read_text(encoding="utf-8")) == ORCHESTRATOR
        assert [p.name for p in tmp_path.iterdir()] == ["a.json"]

    def test_rewrite_never_leaves_a_longer_tail(self, tmp_path):
        """A shorter document replacing a longer one is where write_text tears."""
        path = tmp_path / "a.json"
        _write_json_atomic(path, {**ORCHESTRATOR, "padding": "x" * 500})
        _write_json_atomic(path, ORCHESTRATOR)
        # Parses whole, with nothing after the closing brace.
        assert json.loads(path.read_text(encoding="utf-8")) == ORCHESTRATOR

"""Tests for MODEL_ALIASES, _normalize_model_id, and _validate_model_id."""

import pytest

from liteharness.cli import (
    MODEL_ALIASES,
    _normalize_model_id,
    _validate_model_id,
)


class TestNormalizeModelId:
    def test_hyphen_1m_to_bracket(self):
        assert _normalize_model_id("claude-opus-4-6-1m") == "claude-opus-4-6[1m]"

    def test_already_bracketed_unchanged(self):
        assert _normalize_model_id("claude-opus-4-6[1m]") == "claude-opus-4-6[1m]"

    def test_no_suffix_unchanged(self):
        assert _normalize_model_id("claude-sonnet-5") == "claude-sonnet-5"

    def test_non_claude_prefix_unchanged(self):
        assert _normalize_model_id("gpt-4-1m") == "gpt-4-1m"


class TestValidateModelId:
    @pytest.mark.parametrize("alias,expected", [
        ("opus", "claude-opus-5[1m]"),
        ("opus-4.6", "claude-opus-4-6[1m]"),
        ("opus-4.6-1m", "claude-opus-4-6[1m]"),
        ("opus-4.6-200k", "claude-opus-4-6"),
        ("opus-4.8", "claude-opus-4-8[1m]"),
        ("sonnet", "claude-sonnet-5"),
        ("haiku", "claude-haiku-4-5-20251001"),
        ("fable", "claude-fable-5"),
        ("fable-5.1", "claude-fable-5-1"),
    ])
    def test_alias_resolves(self, alias, expected):
        assert _validate_model_id(alias) == expected

    def test_full_id_passthrough(self):
        assert _validate_model_id("claude-opus-4-6[1m]") == "claude-opus-4-6[1m]"

    def test_hyphen_1m_normalised(self):
        assert _validate_model_id("claude-opus-4-6-1m") == "claude-opus-4-6[1m]"

    def test_unknown_id_refused(self):
        with pytest.raises(SystemExit, match="Unknown model"):
            _validate_model_id("not-a-real-model")

    def test_error_message_names_accepted_forms(self):
        with pytest.raises(SystemExit, match=r"\[1m\] not -1m"):
            _validate_model_id("banana")


class TestAliasCompleteness:
    def test_every_alias_resolves_to_valid_id(self):
        for alias, target in MODEL_ALIASES.items():
            assert _validate_model_id(alias) == target, f"alias {alias!r} → {target!r} fails validation"

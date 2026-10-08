"""Owned launch profile defaults preserve explicit resolver and local choices."""
import pytest

from liteharness.owned_launch import request

AID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("backend", ["codex", "claude", "lmstudio", "llamacpp", "ninfer", "strata", "custom"])
@pytest.mark.parametrize("explicit", [[], ["--tool-profile", "interactive"], ["--tool-profile=strict"]])
def test_profile_selection(tmp_path, fresh, backend, explicit):
    resolution = {"agentId": AID, "liteTuiDataRoot": str(tmp_path), "request": {
        "args": list(explicit), "env": {}, "harnessAgentId": AID}}
    result = request(resolution, root=tmp_path, name="QuietHelm", agent_id=AID,
                     backend=backend, model="fixture", thinking_level="high", fresh=fresh)
    args = result["args"]
    profiles = [arg for arg in args if arg == "--tool-profile" or arg.startswith("--tool-profile=")]
    if explicit:
        assert profiles == [explicit[0]]
        assert args[:len(explicit)] == explicit
        assert "autonomous" not in args
    elif backend in {"codex", "claude"}:
        assert profiles == ["--tool-profile"]
        assert args[args.index("--tool-profile") + 1] == "autonomous"
    else:
        assert profiles == []
    assert resolution["request"]["args"] == explicit

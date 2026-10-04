import pytest


@pytest.fixture(autouse=True)
def _no_live_fleet_state(monkeypatch, tmp_path):
    """T1025: `spawn` now consults the fleet policy and the codex model list.
    No test may read or write the live ~/.litesuite or ~/.codex, and none may run
    the real `codex debug models` (it rewrites the live models_cache.json)."""
    from liteharness import fleet_policy

    monkeypatch.setenv("LITESUITE_FLEET_POLICY", str(tmp_path / "fleet-policy.absent.json"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    def _no_real_codex():
        raise RuntimeError("tests never run the real codex")

    monkeypatch.setattr(fleet_policy, "refetch_codex_models", _no_real_codex)


@pytest.fixture(autouse=True)
def _no_live_name_index(monkeypatch, tmp_path):
    """T0236: the persistent name index is the real ~/.liteharness/names.json.

    A test that has not redirected `config.get_root()` must fail loudly instead of reading
    or rewriting it. A fresh LiteTUI spawn also waits for its born conversation; tests
    that do not exercise that wait must not sleep, nor scan the live LiteTUI data root."""
    from liteharness import agent_names, cli, config

    live = (config.HARNESS_ROOT / "names.json").resolve()

    def guarded():
        path = config.get_root() / "names.json"
        if path.resolve() == live:
            raise RuntimeError("tests never touch the live ~/.liteharness/names.json")
        return path

    monkeypatch.setattr(agent_names, "index_path", guarded)
    monkeypatch.setattr(cli, "FRESH_NAME_WAIT_SECONDS", 0.0)
    monkeypatch.setenv("LITETUI_DATA_ROOT", str(tmp_path / "litetui-data"))

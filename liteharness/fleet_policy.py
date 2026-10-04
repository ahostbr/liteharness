"""Fleet model/thinking floor (T1025).

2026-09-26: a Codex seat ran gpt-5.6-sol at medium thinking because the spawner
read a stale model list and substituted a weaker model. the user: "this MUST NEVER
happen again". So the floor lives in the spawn path, not in prose:

  check()            pre-launch: refuse a request below the floor
  check_available()  pre-launch: a model missing from the live list is refetched,
                     then refused by name -- never substituted
  verify_seat()      post-launch: read what the SEAT says it resolved (its presence
                     row) and fail on anything below the floor

Which seats a floor governs: every seat on a backend named like the floor
(codex governs every model it runs), plus every seat whose model starts with
one of the floor's match_prefixes ON ANY BACKEND -- the floor is about the
model's capability, and LiteTUI's custom backend can be OpenAI itself -- unless
the model starts with one of its exempt_prefixes (gpt-oss-: open weights run
locally, not the codex family).

"Below the floor" is index arithmetic on two ordered lists (low -> high). A model
or level that is NOT in its list is refused, never treated as above the floor.

The policy file is ~/.litesuite/fleet-policy.json, or $LITESUITE_FLEET_POLICY.
Missing -> DEFAULT_POLICY (a missing file must not remove the floor).
Unreadable or malformed -> PolicyError, and every spawn is refused.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import subprocess
import time
from pathlib import Path
from typing import Callable

POLICY_ENV = "LITESUITE_FLEET_POLICY"

DEFAULT_POLICY: dict = {
    "version": 1,
    "floors": {
        "codex": {
            "match_prefixes": ["gpt-"],
            "exempt_prefixes": ["gpt-oss-"],
            "models": [
                "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
                "gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra",
            ],
            "min_model": "gpt-6.1-sol",
            "thinking_levels": [
                "off", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
            ],
            "min_thinking_level": "high",
        }
    },
}


class PolicyError(Exception):
    """The policy file exists but cannot be used. Every spawn is refused."""


def policy_path() -> Path:
    override = os.environ.get(POLICY_ENV, "").strip()
    return Path(override) if override else Path.home() / ".litesuite" / "fleet-policy.json"


def _validate(policy: object, where: str) -> dict:
    def bad(why: str) -> PolicyError:
        return PolicyError(f"fleet policy {where} is malformed: {why}. Every spawn is refused until it is fixed.")

    if not isinstance(policy, dict) or not isinstance(policy.get("floors"), dict) or not policy["floors"]:
        raise bad("expected {\"floors\": {<backend>: {...}}}")
    for name, floor in policy["floors"].items():
        if not isinstance(floor, dict):
            raise bad(f"floor {name!r} is not an object")
        for list_key, min_key in (("models", "min_model"), ("thinking_levels", "min_thinking_level")):
            items = floor.get(list_key)
            if not isinstance(items, list) or not items or not all(isinstance(x, str) for x in items):
                raise bad(f"floor {name!r}.{list_key} must be a non-empty list of strings")
            if floor.get(min_key) not in items:
                raise bad(f"floor {name!r}.{min_key} {floor.get(min_key)!r} is not in {list_key}")
        for key in ("match_prefixes", "exempt_prefixes"):
            prefixes = floor.get(key, [])
            if not isinstance(prefixes, list) or not all(isinstance(x, str) for x in prefixes):
                raise bad(f"floor {name!r}.{key} must be a list of strings")
    return policy


def load(path: Path | None = None) -> tuple[dict, str]:
    """(policy, where-it-came-from). Raises PolicyError for an unusable file."""
    path = path or policy_path()
    if not path.exists():
        return DEFAULT_POLICY, f"built-in default ({path} absent)"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError(f"fleet policy {path} is unreadable ({type(exc).__name__}: {exc}). "
                          "Every spawn is refused until it is fixed.") from exc
    return _validate(policy, str(path)), str(path)


def floor_for(policy: dict, backend: str | None, model: str | None) -> tuple[str, dict] | None:
    """The floor that governs this seat, or None when no floor applies. A backend
    named like the floor governs every model; otherwise a match_prefixes model
    is governed on ANY backend (custom can be OpenAI itself), unless it is one of
    the floor's exempt_prefixes (gpt-oss-120b on local is a local seat)."""
    backend = (backend or "").strip().lower()
    model = (model or "").strip().lower()

    def starts(key: str, floor: dict) -> bool:
        return any(model.startswith(p.lower()) for p in floor.get(key, []))

    for name, floor in policy["floors"].items():
        if backend == name.lower():
            return name, floor
        if model and starts("match_prefixes", floor) and not starts("exempt_prefixes", floor):
            return name, floor
    return None


def _clean(value: str | None) -> str | None:
    """Trimmed, and blank -> None: the same normalisation resolveSpawn applies."""
    return value.strip() or None if isinstance(value, str) else value


def _add_model_hint(name: str, floor: dict, path: Path | None) -> str:
    """the orchestrator af053094: a new model is ONE EDIT to the policy file, never a code
    change -- a refusal that reads like a wall gets routed around."""
    path = path or policy_path()
    target = str(path) if path.exists() else f"the built-in default; create {path} to override"
    return (f"To allow it, add it to floors.{name}.models in {target}. That list is ordered "
            f"low->high, so insert it by rank (the floor is min_model {floor['min_model']})")


def below_floor(name: str, floor: dict, model: str | None, thinking_level: str | None,
                path: Path | None = None) -> str | None:
    """Why (model, thinking_level) is below `floor`, or None when it meets it."""
    need = (f"{name} seats need model >= {floor['min_model']} and thinking_level >= "
            f"{floor['min_thinking_level']} (models low->high: {', '.join(floor['models'])}; "
            f"levels low->high: {', '.join(floor['thinking_levels'])})")
    models, levels = floor["models"], floor["thinking_levels"]
    problems = []
    if model not in models:
        problems.append(f"model {model!r} is not in the {name} list, and an unlisted model is refused"
                        + (f". Nothing was substituted. {_add_model_hint(name, floor, path)}" if model else ""))
    elif models.index(model) < models.index(floor["min_model"]):
        problems.append(f"model {model!r} is below {floor['min_model']}")
    if thinking_level not in levels:
        problems.append(f"thinking_level {thinking_level!r} is not a listed level (unset/default is refused)")
    elif levels.index(thinking_level) < levels.index(floor["min_thinking_level"]):
        problems.append(f"thinking_level {thinking_level!r} is below {floor['min_thinking_level']}")
    if not problems:
        return None
    return f"FLEET FLOOR: {'; '.join(problems)}. {need}."


def check(backend: str | None, model: str | None, thinking_level: str | None,
          path: Path | None = None) -> str | None:
    """Pre-launch gate. None = allowed; otherwise the refusal text."""
    model, thinking_level = _clean(model), _clean(thinking_level)
    try:
        policy, where = load(path)
    except PolicyError as exc:
        return f"SPAWN REFUSED: {exc}"
    governed = floor_for(policy, backend, model)
    if governed is None:
        return None
    why = below_floor(*governed, model, thinking_level, path)
    return f"SPAWN REFUSED: {why} Policy: {where}. Nothing was spawned." if why else None


def gate(backend: str | None, model: str | None, thinking_level: str | None,
         path: Path | None = None, cache: Path | None = None,
         refetch: Callable[[], set[str]] | None = None) -> str | None:
    """The whole pre-launch gate: the floor, then (for a governed seat) the model list."""
    model = _clean(model)
    why = check(backend, model, thinking_level, path)
    if why is not None:
        return why
    if floor_for(load(path)[0], backend, model) is None:
        return None
    return check_available(model, cache, refetch or refetch_codex_models)


# ── item 4: the model list ──────────────────────────────────────────────────────

def codex_models_cache() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".codex") / "models_cache.json"


def _slugs(catalog: object) -> set[str]:
    models = catalog.get("models", []) if isinstance(catalog, dict) else []
    return {m["slug"] for m in models if isinstance(m, dict) and isinstance(m.get("slug"), str)}


def refetch_codex_models() -> set[str]:
    """`codex debug models` refreshes the catalog (and rewrites models_cache.json)."""
    out = subprocess.run(["codex", "debug", "models"], capture_output=True, text=True,
                         encoding="utf-8", timeout=90)
    if out.returncode != 0:
        raise RuntimeError(f"codex debug models exited {out.returncode}: {out.stderr.strip()[:200]}")
    return _slugs(json.loads(out.stdout))


def check_available(model: str, cache: Path | None = None,
                    refetch: Callable[[], set[str]] = refetch_codex_models) -> str | None:
    """None if `model` is in the codex list; else refetch once, then refuse by name."""
    try:
        cached = _slugs(json.loads((cache or codex_models_cache()).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        cached = set()
    if model in cached:
        return None
    try:
        fresh = refetch()
    except Exception as exc:  # noqa: BLE001 -- any failure to refetch is a refusal
        return (f"SPAWN REFUSED: model {model!r} is not in the cached codex list and the refetch "
                f"failed ({exc}). Nothing was substituted and nothing was spawned.")
    if model in fresh:
        return None
    return (f"SPAWN REFUSED: model {model!r} is not in the codex model list even after a refetch "
            f"(available: {', '.join(sorted(fresh)) or 'none'}). Nothing was substituted and "
            "nothing was spawned.")


# ── T0088: the codex CLI's own record of what it ran ───────────────────────────

def codex_sessions_dir() -> Path:
    home = os.environ.get("CODEX_HOME", "").strip()
    return (Path(home) if home else Path.home() / ".codex") / "sessions"


SPAWN_MARKER_KEY = "LITEHARNESS_SPAWN_ID"

#: codex CLI options, read off `codex --help` (0.158). Anything not listed is unknown to
#: with_spawn_marker, so a newer codex fails closed instead of being guessed at.
_CODEX_VALUE_FLAGS = frozenset({
    "-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
    "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox", "-C", "--cd",
    "--add-dir", "-a", "--ask-for-approval",
})
_CODEX_BOOL_FLAGS = frozenset({
    "--strict-config", "--oss", "--approve-for-me", "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust", "--worktree", "--search", "--no-alt-screen", "--no-daemon",
})


def new_spawn_marker() -> str:
    """A fresh marker for ONE launch: 128 random bits, minted by the spawner. It is the whole
    first user message of the codex seat, which codex writes into its own rollout."""
    return f"{SPAWN_MARKER_KEY}={secrets.token_hex(16)}"


def with_spawn_marker(exec_cmd: str, marker: str) -> str | None:
    """`exec_cmd` with `marker` appended as codex's positional PROMPT, or None when that
    is not unambiguous. The contract is deliberately strict: the program token must be
    codex itself (no cmd /c, pwsh -c or python -m wrapper, whose quoting we would be
    guessing at), followed only by options from the two known lists above. A subcommand,
    a positional prompt already there, an unknown option, a `--`, the variadic `-i/--image`
    (it would swallow the marker as another file) or a value flag with no value: all None.
    The caller must refuse the spawn on None; it never launches an unmarked codex."""
    try:
        parts = shlex.split(exec_cmd, posix=False)
    except ValueError:
        return None
    if not parts or re.split(r"[\\/]", parts[0].strip("\"'"))[-1].split(".")[0].lower() != "codex":
        return None
    i = 1
    while i < len(parts):
        flag, has_eq, _ = parts[i].partition("=")
        if parts[i] in _CODEX_BOOL_FLAGS:
            i += 1
        elif flag in _CODEX_VALUE_FLAGS:
            if has_eq:
                i += 1
            elif i + 1 < len(parts) and not parts[i + 1].startswith("-"):
                i += 2
            else:
                return None
        else:
            return None
    return f"{exec_cmd} {marker}"


def _marker_hits(rollout: Path, marker: str) -> list[dict]:
    """Every USER message in this rollout whose whole text IS `marker` (exact, after
    trimming; a message that merely contains it does not count), as {"sid", "turn_id",
    "turn"}. `turn` is the turn_context with that SAME turn_id, None while it is not written."""
    sid, turns, found = rollout.name, {}, []
    session_cwds = []
    try:
        with open(rollout, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or not isinstance(rec.get("payload"), dict):
                    continue
                payload = rec["payload"]
                if rec.get("type") == "session_meta":
                    session_cwds.append(payload.get("cwd"))
                    if isinstance(payload.get("id"), str) and payload["id"]:
                        sid = payload["id"]
                elif rec.get("type") == "turn_context":
                    turn_id = payload.get("turn_id")
                    if isinstance(turn_id, str) and turn_id:
                        # Even identical duplicates are ambiguous evidence, never last-wins.
                        if turn_id in turns:
                            turns[turn_id] = {"ambiguous": True}
                        else:
                            turns[turn_id] = payload
                elif (rec.get("type") == "response_item" and payload.get("type") == "message"
                      and payload.get("role") == "user"):
                    content = payload.get("content")
                    if (not isinstance(content, list) or not content
                            or not all(isinstance(c, dict) and isinstance(c.get("text"), str)
                                       for c in content)):
                        continue
                    text = "".join(c["text"] for c in content)
                    if text.strip() == marker:
                        meta = payload.get("internal_chat_message_metadata_passthrough")
                        turn_id = meta.get("turn_id") if isinstance(meta, dict) else None
                        found.append({"sid": sid, "turn_id": turn_id
                                      if isinstance(turn_id, str) and turn_id else None})
    except OSError:
        return []
    for hit in found:
        hit["turn"] = turns.get(hit["turn_id"]) if hit["turn_id"] else None
        hit["cwd"] = session_cwds[0] if len(session_cwds) == 1 else None
    return found


def verify_codex_rollout(marker: str, expect_model: str | None, expect_effort: str | None,
                         since: float | None = None, path: Path | None = None,
                         sessions: Path | None = None, wait: float = 90,
                         result: dict | None = None,
                         sleep: Callable[[float], None] = time.sleep,
                         clock: Callable[[], float] = time.monotonic,
                         expect_cwd: str | None = None) -> str | None:
    """ACTIVATED ON THE T0088-C BRANCH ONLY (cmd_spawn calls it for --split --cli codex); main
    keeps refusing the codex CLI until this is proven and merged. The spawner launches codex
    with `new_spawn_marker()` as its whole prompt, via with_spawn_marker. On success, when
    `result` is given it is filled with sid, model, effort and turn_id of the matched turn.

    Post-launch check of a codex CLI seat, attributed by that marker: the rollout holding a
    user message that IS the marker is taken as this seat's, and the model and effort come
    from the turn_context with THAT message's turn_id. Fails on a mismatch with the ask or
    anything below the codex floor. Refused: no such message within `wait`; one whose turn
    is not recorded in time; one with no turn_id to join on; and more than one such message
    anywhere (two rollouts, or two turns of one). A neighbour's rollout can neither pass nor
    fail this seat. `since` (epoch seconds) only narrows which files are opened.

    THREAT MODEL, stated as an assumption and not a proof: the token is 128 random bits minted
    at launch, so an unrelated codex session cannot produce it by accident, which is exactly
    what cwd + time could not promise. It is visible in the seat's own command line, so
    another process of the same user could read and repeat it; that is caught only if the copy
    also reaches a rollout (then two hits). This defends against mis-attribution, not against
    a deliberate spoof, and nothing here has been run against a live codex."""
    try:
        policy, where = load(path)
    except PolicyError as exc:
        return f"SEAT FAILED FLOOR: {exc}"
    root = sessions or codex_sessions_dir()
    deadline = clock() + wait
    while True:
        hits = []
        for rollout in root.rglob("rollout-*.jsonl") if root.exists() else ():
            try:
                if since is not None and rollout.stat().st_mtime < since - 5:
                    continue
            except OSError:
                continue
            hits.extend(_marker_hits(rollout, marker))
        if len(hits) > 1:
            return (f"SEAT FAILED FLOOR: {marker} is the whole text of {len(hits)} user messages "
                    f"(sessions {', '.join(str(h['sid']) for h in hits)}); which one is this seat's cannot be told. Refused.")
        if hits and hits[0]["turn_id"] is None:
            return (f"SEAT FAILED FLOOR: codex session {hits[0]['sid']} holds {marker} in a message with no "
                    "turn_id, so it cannot be tied to a turn and what it ran cannot be read. Unverified is refused.")
        if hits and hits[0]["turn"] is not None:
            sid, turn = hits[0]["sid"], hits[0]["turn"]
            if expect_cwd is not None:
                # Exact string written by codex session_meta, no substring/URI/alias equivalence.
                # The launcher already passes its resolved absolute cwd in native OS spelling.
                if hits[0]["cwd"] != expect_cwd:
                    return (f"SEAT FAILED MISMATCH: codex session {sid} session_meta.cwd is not the exact "
                            "requested cwd (missing, duplicate or different metadata). Refused.")
            if turn.get("ambiguous"):
                return (f"SEAT FAILED FLOOR: codex session {sid} has duplicate turn_context records for "
                        f"turn {hits[0]['turn_id']}; attribution is ambiguous. Refused.")
            model, effort = turn.get("model"), turn.get("effort")
            if not isinstance(model, str) or not isinstance(effort, str):
                return f"SEAT FAILED FLOOR: codex session {sid} has malformed model/effort evidence. Refused."
            if ((expect_model and not _same(model, expect_model))
                    or (expect_effort and not _same(effort, expect_effort))):
                return (f"SEAT FAILED MISMATCH: codex session {sid} was asked for model={expect_model or '<any>'} "
                        f"thinking_level={expect_effort or '<any>'} and ran model={model} effort={effort}. "
                        f"Policy: {where}.")
            governed = floor_for(policy, "codex", model)
            why = below_floor(*governed, model, effort, path) if governed else None
            if not why and result is not None:
                result.update(sid=sid, model=model, effort=effort, turn_id=hits[0]["turn_id"])
            return (f"SEAT FAILED FLOOR: codex session {sid} ran model={model} effort={effort}. "
                    f"{why} Policy: {where}.") if why else None
        if clock() >= deadline:
            if hits:
                return (f"SEAT FAILED FLOOR: codex session {hits[0]['sid']} holds {marker} but did not record turn "
                        f"{hits[0]['turn_id']} within {wait:g}s, so the model and effort it ran cannot be read. "
                        "Unverified is refused.")
            return (f"SEAT FAILED FLOOR: no codex rollout holds {marker} as a user message within {wait:g}s, so this "
                    "seat's model and effort cannot be read. Unverified is refused.")
        sleep(2)


# ── item 2: what the seat actually resolved ────────────────────────────────────

def _same(a: object, b: object) -> bool:
    return str(a).strip().casefold() == str(b).strip().casefold()


def verify_seat(agent_id: str, root: Path, wait: float = 90, path: Path | None = None,
                backend: str | None = None, allow_silent: bool = False,
                expect_model: str | None = None, expect_thinking: str | None = None,
                sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> str | None:
    """Poll the seat's presence row until it reports its effective model (and,
    for a governed model, its thinking_level); None = at or above the floor.
    `backend` is the one the spawner launched: under a governed backend a model
    the floor does not list (o4-mini, codex-mini-latest) is refused, not waved
    through as ungoverned.

    `allow_silent` is for a request the pre-gate called ungoverned (a local seat
    with no model loaded reports "unknown" forever): a seat that never reports a
    model passes. It never excuses a REPORTED governed model: the pin can turn a
    local request into codex gpt-5.6-sol/medium, and that seat is refused.

    `expect_model` / `expect_thinking` are what the spawn ASKED for. Card T1025 item
    (2): the spawn fails on MISMATCH, not only below the floor -- the T1004 pin can
    override --model, and a gpt-6-astra/xhigh request that became gpt-6-sol/high,
    or a gpt-6-sol request pinned to o4-mini, is not the seat that was asked for.
    Compared trimmed and case-folded."""
    try:
        policy, where = load(path)
    except PolicyError as exc:
        return f"SEAT FAILED FLOOR: {exc}"
    row_path = root / "agents" / f"{agent_id}.json"
    deadline = clock() + wait
    row: dict = {}
    while True:
        try:
            row = json.loads(row_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            row = {}
        model = row.get("model")
        if model and model != "unknown":
            level = row.get("thinking_level")
            if ((expect_model and not _same(model, expect_model))
                    or (expect_thinking and "thinking_level" in row and not _same(level, expect_thinking))):
                return (f"SEAT FAILED MISMATCH: {agent_id} was asked for model={expect_model or '<any>'} "
                        f"thinking_level={expect_thinking or '<any>'} and resolved model={model} "
                        f"thinking_level={row.get('thinking_level', '<absent>')}. A pinned default can "
                        "override --model (T1004), so the spawn fails on any mismatch, not only below "
                        f"the floor. Policy: {where}.")
            # T0095: governed by the spawner's backend OR the backend the seat reports
            # (T1027), and it must clear every floor that applies: a reported backend
            # can add a floor (local request pinned to codex/o4-mini) but never remove one.
            governed = [g for g in (floor_for(policy, backend, model),
                                    floor_for(policy, row.get("backend"), model)) if g]
            if not governed and not expect_thinking:
                return None
            if "thinking_level" in row:
                whys = [w for g in governed if (w := below_floor(*g, model, row["thinking_level"], path))]
                return (f"SEAT FAILED FLOOR: {agent_id} resolved model={model} "
                        f"thinking_level={row['thinking_level']}. {whys[0]} Policy: {where}.") if whys else None
        if clock() >= deadline:
            if allow_silent and not (model and model != "unknown"):
                return None
            return (f"SEAT FAILED FLOOR: {agent_id} did not report its effective model and "
                    f"thinking_level within {wait:g}s (row: model={row.get('model')!r}, "
                    f"thinking_level={row.get('thinking_level', '<absent>')!r}). Unverified is refused.")
        sleep(2)

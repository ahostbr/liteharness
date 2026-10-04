"""`liteharness handoffs --audit` — read-only inventory of legacy handoff files (T0238, WS3a).

One Harness puts every agent's handoff in its LiteTUI conversation store
(`.convos/<uuid>/handoff.md`). This module finds the handoff-like files that still
live elsewhere and says, per file, what it is and who claims it. It NEVER moves,
deletes, writes or marks anything; migration/freeze is WS3b and needs a fresh GO.

Contract
  audit(roots, ...) -> dict  {roots, rows, summary, [writable_legacy]}
  * A root that cannot be read is status "UNKNOWN" with files_scanned/rows = None,
    never 0. A root with an error in a subdirectory is "partial". summary.complete
    is True only when every root is "ok".
  * Reparse points (symlinks, junctions) are never followed, only counted.
  * kind separates handoff prose from things that must not be migrated as prose:
    lock, binary, transcript, index, memory, checkpoint_json, other.
  * Owner resolution is ONE swappable function, `resolve_name(claim, index)`. WS1's
    resolver replaces it by passing `resolver=` to audit(); the row shape is fixed.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(rf"^{_UUID}$")
_UUID_FIND = re.compile(_UUID)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_HEADER_BYTES = 4096

# Name that makes a file a handoff/memory CANDIDATE in "candidates" mode. Name only:
# this is an inventory, not content classification.
CANDIDATE_RE = re.compile(
    r"handoff|hand-off|memory|soul|checkpoint|convo|resume|continuity|current-status|"
    r"leader-current", re.IGNORECASE)

_LOCK_EXT = {".lock", ".lease"}
_BINARY_EXT = {
    ".uasset", ".umap", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".exe",
    ".dll", ".pyc", ".so", ".zip", ".7z", ".gz", ".tar", ".db", ".sqlite", ".bin", ".pdf",
    ".mp4", ".mov", ".wav", ".mp3", ".ogg", ".fbx", ".psd", ".pak", ".onnx", ".safetensors",
    ".gguf",
}
_PROSE_EXT = {".md", ".txt", ".markdown"}

# Tokens that are words about the file, not the name of its owner.
_STOP_TOKENS = {
    "handoff", "handoffs", "hand", "off", "final", "restart", "current", "latest", "status",
    "memory", "memories", "soul", "checkpoint", "checkpoints", "convo", "log", "leader",
    "resume", "continuity", "notes", "note", "the", "and", "for", "of", "to", "in", "on",
    "active", "delta", "gate", "plan", "review", "pending", "next", "attempt", "proof",
    "slot", "go", "hold", "law", "rescued", "candidate", "readme", "index", "summary",
    "md", "txt", "lock", "json", "jsonl",
}
_SPLIT_RE = re.compile(r"[_\-\s.]+")
_HEADER_CLAIM_RE = re.compile(
    r"(?im)^[ \t>#*_-]*(?:agent[- _]?name|agent|owner|seat|author|written[ _]by|from|name)"
    r"[ \t*_]*[:=][ \t*_]*([^\r\n]+)")
_HEADER_ID_RE = re.compile(rf"(?i)agent[-_ ]?id[ \t*_]*[:=][ \t*_\"']*({_UUID})")


@dataclass(frozen=True)
class Root:
    """A scan root. mode: "all" lists every file; "candidates" lists name matches only;
    "strays" is the convo root: files OUTSIDE a `<uuid>/` store, name matches only."""
    label: str
    path: Path
    mode: str = "candidates"
    legacy: bool = False  # legacy roots are the ones 3b will freeze
    skip_dirs: tuple = ()  # directory NAMES not descended into ("candidates" mode only)

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        if self.mode not in ("all", "candidates", "strays"):
            raise ValueError(f"unknown root mode {self.mode!r}")


ENV_ROOTS = "LITEHARNESS_HANDOFF_AUDIT_ROOTS"   # os.pathsep-separated --root specs
ENV_CONVOS = "LITETUI_CONVOS_ROOT"
EXIT_INCOMPLETE = 3   # output was produced, but a root was UNKNOWN/partial: do not trust counts


def parse_root_spec(spec: str) -> Root:
    """`LABEL=PATH[@mode][!skipdir,skipdir][+legacy]` -> Root. mode: all | candidates | strays.
    `+legacy` marks a root 3b will freeze (what --writable-legacy reports on)."""
    label, sep, rest = spec.partition("=")
    if not sep or not label or not rest:
        raise ValueError(f"want LABEL=PATH[@mode][!skipdir,...], got {spec!r}")
    rest, plus, flag = rest.rpartition("+") if rest.endswith("+legacy") else (rest, "", "")
    rest, _, skips = rest.partition("!")
    path, _, mode = rest.rpartition("@") if "@" in rest else (rest, "", "candidates")
    skip = _NOISE_DIRS | {x for x in skips.split(",") if x}
    return Root(label, Path(path), mode or "candidates", legacy=bool(plus),
                skip_dirs=tuple(sorted(skip)))


def default_roots(convos_root: Path | None = None) -> list[Root]:
    """Portable defaults only: this file ships in a public package, so machine-specific
    trees (game repos, scratch dirs, the LiteTUI checkout) come from --root or the
    LITEHARNESS_HANDOFF_AUDIT_ROOTS env var, never from source."""
    roots = [Root("liteharness_handoffs", Path.home() / ".liteharness" / "handoffs", "all",
                  legacy=True)]
    for spec in filter(None, os.environ.get(ENV_ROOTS, "").split(os.pathsep)):
        roots.append(parse_root_spec(spec))
    if convos_root is not None:
        roots.append(Root("convos_strays", convos_root, "strays"))
    return roots


_NOISE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
# Source/log extensions matched by a handoff-ish NAME (convo_search.py, test_convos.py)
# are code, not handoffs: "candidates" mode leaves them out and COUNTS them per root
# (`excluded_source`) so nothing vanishes silently.
_SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".snap", ".diff", ".patch", ".sql",
               ".log", ".ps1", ".index", ".cs", ".cpp", ".h", ".toml", ".yaml", ".yml", ".html",
               ".css"}


# --------------------------------------------------------------------- scanning

def _is_reparse(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False


def _walk(root: Root, info: dict):
    """Yield (DirEntry, rel_parts) for every regular file; record errors, never raise."""
    stack: list[tuple[Path, tuple[str, ...]]] = [(root.path, ())]
    while stack:
        directory, rel = stack.pop()
        try:
            it = os.scandir(directory)
        except OSError as exc:
            info["errors"].append(f"{directory}: {exc.strerror or exc}")
            continue
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError as exc:
                    info["errors"].append(f"{directory}: {exc.strerror or exc}")
                    break
                if _is_reparse(entry):
                    info["reparse_skipped"] += 1
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError as exc:
                    info["errors"].append(f"{entry.path}: {exc.strerror or exc}")
                    continue
                if is_dir:
                    if root.mode == "strays" and not rel and UUID_RE.match(entry.name):
                        info["stores_skipped"] += 1
                        continue
                    if root.mode == "candidates" and entry.name in root.skip_dirs:
                        continue
                    stack.append((Path(entry.path), rel + (entry.name,)))
                elif is_file:
                    yield entry, rel


def classify(name: str, rel_parts: tuple[str, ...] = ()) -> str:
    lower = name.lower()
    ext = Path(lower).suffix
    if ext in _LOCK_EXT:
        return "lock"
    if ext in _BINARY_EXT:
        return "binary"
    if lower in ("index.json", "manifest.json") or lower.endswith("-index.json"):
        return "index"
    if ext == ".jsonl":
        return "transcript"
    if lower in ("memory.md", "soul.md") or any(p.lower() in ("memory", "memories") for p in rel_parts):
        return "memory"
    if ext == ".json":
        if lower.startswith("checkpoint") or any("checkpoint" in p.lower() for p in rel_parts):
            return "checkpoint_json"
        return "other"
    if ext in _PROSE_EXT:
        return "handoff_prose"
    return "other"


# ---------------------------------------------------------------------- owners

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _name_tokens(text: str) -> list[str]:
    """Candidate owner tokens in a file/dir name, in order; words about the file,
    dates, versions and card ids are dropped."""
    out: list[str] = []
    for tok in _SPLIT_RE.split(text):
        if not tok or not tok[0].isalpha():
            continue
        if tok.lower() in _STOP_TOKENS or re.fullmatch(r"v\d+", tok, re.IGNORECASE) \
                or re.fullmatch(r"[tT]\d{2,}[A-Za-z]*", tok):
            continue
        if re.fullmatch(r"[0-9a-fA-F]{1,12}", tok) and any(c.isdigit() for c in tok):
            continue  # a UUID fragment ("b118"), not a name
        out.append(tok)
    return out


def _read_head(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return fh.read(_HEADER_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def owner_claims(abs_path: str, rel_parts: tuple[str, ...], name: str, kind: str) -> list[tuple[str, str]]:
    """Ordered (claim, source) candidates. source: header | name | parent.
    Only prose is opened (first 4 KiB); locks/binaries/transcripts are never read."""
    claims: list[tuple[str, str]] = []
    if kind == "checkpoint_json":
        # sots_convo checkpoints are keyed by MODEL directory/timestamp, not by agent:
        # the path tokens name a model, so only an explicit id/name inside counts.
        head = _read_head(abs_path)
        m = re.search(r'"agent[_-]?(?:id|name)"\s*:\s*"([^"]+)"', head)
        return [(m.group(1), "header")] if m else []
    if kind == "handoff_prose":
        head = _read_head(abs_path)
        for m in _HEADER_ID_RE.finditer(head):
            claims.append((m.group(1).lower(), "header"))
        for m in _HEADER_CLAIM_RE.finditer(head):
            value = m.group(1).strip()
            uid = _UUID_FIND.search(value)
            if uid:
                claims.append((uid.group(0).lower(), "header"))
            word = re.match(r"[A-Za-z][\w.\-]*", value)
            if word:
                claims.append((word.group(0).rstrip(".-"), "header"))
    stem = name
    for tok in _name_tokens(stem):
        claims.append((tok, "name"))
    # joined form, e.g. "DaVinci-Q8" -> also try "DaVinci-Q8"
    joined = [t for t in _SPLIT_RE.split(stem) if t and t[0].isalpha()
              and t.lower() not in _STOP_TOKENS]
    if len(joined) >= 2:
        claims.append(("-".join(joined[:2]), "name"))
    if rel_parts:
        parent = rel_parts[-1]
        if UUID_RE.match(parent):
            claims.append((parent.lower(), "parent"))  # store-shaped dir: the dir IS the convo id
        else:
            for tok in _name_tokens(parent):
                claims.append((tok, "parent"))
    return claims


@dataclass
class Index:
    """Who exists: registry presence, registry names, convo seats."""
    live_by_name: dict
    named_by_name: dict
    live_ids: set
    convo_by_name: dict
    convo_by_seat_id: dict
    convo_ids: set
    errors: list


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_index(registry_root: Path, convos_root: Path | None) -> Index:
    live_by_name: dict = {}
    named_by_name: dict = {}
    live_ids: set = set()
    convo_by_name: dict = {}
    convo_by_seat_id: dict = {}
    convo_ids: set = set()
    errors: list = []

    def _add(table: dict, key: str, value: str):
        table.setdefault(key.lower(), [])
        if value not in table[key.lower()]:
            table[key.lower()].append(value)

    agents = Path(registry_root) / "agents"
    if agents.is_dir():
        for f in sorted(agents.glob("*.json")):
            data = _load_json(f)
            if not isinstance(data, dict):
                continue
            aid = str(data.get("agent_id") or f.stem)
            live_ids.add(aid)
            if data.get("name"):
                _add(live_by_name, str(data["name"]), aid)
    else:
        errors.append(f"{agents}: registry agents dir not readable")
    for sub in ("names",):
        d = Path(registry_root) / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if UUID_RE.match(f.name) and f.is_file():
                    try:
                        name = f.read_text(encoding="utf-8").strip()
                    except OSError:
                        continue
                    if name:
                        _add(named_by_name, name, f.name)
    retired = Path(registry_root) / "retired-agents"
    if retired.is_dir():
        for f in sorted(retired.glob("*.json")):
            data = _load_json(f)
            if isinstance(data, dict) and data.get("name"):
                _add(named_by_name, str(data["name"]), str(data.get("agent_id") or f.stem))

    if convos_root is None:
        errors.append("no convos root given (--convos-root / LITETUI_CONVOS_ROOT): "
                      "convo-store owner resolution is UNAVAILABLE, not empty")
    elif Path(convos_root).is_dir():
        try:
            entries = list(os.scandir(convos_root))
        except OSError as exc:
            errors.append(f"{convos_root}: {exc.strerror or exc}")
            entries = []
        for e in entries:
            if not UUID_RE.match(e.name) or not e.is_dir(follow_symlinks=False):
                continue
            convo_ids.add(e.name.lower())
            data = _load_json(Path(e.path) / "settings.json")
            if isinstance(data, dict):
                if data.get("seat_name"):
                    _add(convo_by_name, str(data["seat_name"]), e.name)
                if data.get("seat_id"):
                    _add(convo_by_seat_id, str(data["seat_id"]).lower(), e.name)
    else:
        errors.append(f"{convos_root}: convos root not readable")
    return Index(live_by_name, named_by_name, live_ids, convo_by_name, convo_by_seat_id,
                 convo_ids, errors)


def _ws1_lookup(claim: str, index: Index) -> tuple:
    """WS1 name index (`liteharness.agent_names`, contract: onehar/WS1-names-contract.md),
    consulted only when importable. Returns (via, agent_ids, convo_ids); via is "" when the
    module is absent or has no record. An unreadable index (IndexCorrupt) is reported in
    index.errors ONCE and the local lookup still answers: never silently treated as
    "no such name"."""
    try:
        import importlib
        names = importlib.import_module("liteharness.agent_names")
    except ImportError:
        return "", [], []
    try:
        name = claim
        if UUID_RE.match(claim):
            name = names.name_for_agent(claim)
            if not name:
                return "", [], []
        rec = names.resolve_name(name)
    except Exception as exc:  # IndexCorrupt and anything else the index raises
        msg = f"WS1 names index unavailable: {type(exc).__name__}: {exc}"
        if msg not in index.errors:
            index.errors.append(msg)
        return "", [], []
    if not rec:
        return "", [], []
    return ("names-index", [rec["agent_id"]] if rec.get("agent_id") else [],
            [rec["convo_id"]] if rec.get("convo_id") else [])


def resolve_name(claim: str, index: Index) -> dict:
    """DEFAULT resolver: exact, case-insensitive match of `claim` (a name or a UUID)
    against the registry presence files, `names/` overrides, retired-agents and each
    convo store's settings.json seat_name/seat_id, PLUS the WS1 names index when
    `liteharness.agent_names` is importable (via "names-index").

    SWAP POINT: a different resolver replaces this whole function via audit(resolver=).
    Returns {"status": "resolved"|"unresolved", "via": [...], "agent_ids": [...],
    "convo_ids": [...]}.
    """
    key = claim.lower()
    via: list = []
    agent_ids: list = []
    convo_ids: list = []

    def _take(ids, into):
        for i in ids:
            if i not in into:
                into.append(i)

    if UUID_RE.match(claim):
        if claim in index.live_ids or key in {i.lower() for i in index.live_ids}:
            via.append("registry")
            _take([claim], agent_ids)
        if key in index.convo_by_seat_id:
            via.append("convo")
            _take([key], agent_ids)
            _take(index.convo_by_seat_id[key], convo_ids)
        if key in index.convo_ids:
            if "convo" not in via:
                via.append("convo")
            _take([key], convo_ids)
    else:
        if key in index.live_by_name:
            via.append("registry")
            _take(index.live_by_name[key], agent_ids)
        if key in index.named_by_name:
            via.append("registry-name")
            _take(index.named_by_name[key], agent_ids)
        if key in index.convo_by_name:
            via.append("convo")
            _take(index.convo_by_name[key], convo_ids)
    ws1_via, ws1_agents, ws1_convos = _ws1_lookup(claim, index)
    if ws1_via:
        via.append(ws1_via)
        _take(ws1_agents, agent_ids)
        _take(ws1_convos, convo_ids)
    return {"status": "resolved" if via else "unresolved", "via": via,
            "agent_ids": agent_ids, "convo_ids": convo_ids}


def _owner(abs_path, rel_parts, name, kind, index, resolver) -> dict:
    if kind in ("lock", "binary", "transcript", "index"):
        return {"claim": None, "source": None, "status": "not_applicable", "via": [],
                "agent_ids": [], "convo_ids": []}
    claims = owner_claims(abs_path, rel_parts, name, kind)
    if not claims:
        return {"claim": None, "source": None, "status": "unclaimed", "via": [],
                "agent_ids": [], "convo_ids": []}
    first_unresolved = None
    for claim, source in claims:
        res = resolver(claim, index)
        if res.get("status") == "resolved":
            return {"claim": claim, "source": source, "status": "resolved",
                    "via": list(res.get("via", [])), "agent_ids": list(res.get("agent_ids", [])),
                    "convo_ids": list(res.get("convo_ids", []))}
        if first_unresolved is None:
            first_unresolved = (claim, source)
    claim, source = first_unresolved
    return {"claim": claim, "source": source, "status": "unresolved", "via": [],
            "agent_ids": [], "convo_ids": []}


# ----------------------------------------------------------------------- audit

def is_frozen(root: Root) -> bool:
    """3b installs the freeze hook; until then nothing is frozen."""
    return False


def audit(roots: list[Root], *, registry_root: Path | None = None,
          convos_root: Path | None = None,
          resolver: Callable[[str, Index], dict] = resolve_name,
          writable_legacy: bool = False) -> dict:
    if registry_root is None:
        from . import config
        registry_root = config.get_root()
    if convos_root is None and os.environ.get(ENV_CONVOS):
        convos_root = Path(os.environ[ENV_CONVOS])
    index = build_index(Path(registry_root), convos_root)

    root_reports: list[dict] = []
    rows: list[dict] = []
    for root in roots:
        info = {"label": root.label, "path": str(root.path), "mode": root.mode,
                "legacy": root.legacy, "status": "ok", "files_scanned": 0, "rows": 0,
                "reparse_skipped": 0, "excluded_source": 0, "errors": []}
        if root.mode == "strays":
            info["stores_skipped"] = 0
        if not root.path.is_dir():
            info.update(status="UNKNOWN", files_scanned=None, rows=None)
            info["errors"].append(f"{root.path}: root missing or not a directory")
            root_reports.append(info)
            continue
        scanned = 0
        emitted = 0
        for entry, rel in _walk(root, info):
            scanned += 1
            name = entry.name
            kind = classify(name, rel)
            if root.mode != "all" and not CANDIDATE_RE.search(name):
                continue
            if root.mode != "all" and Path(name.lower()).suffix in _SOURCE_EXT:
                info["excluded_source"] += 1
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                info["errors"].append(f"{entry.path}: {exc.strerror or exc}")
                continue
            rows.append({
                "root": root.label,
                "path": entry.path,
                "size": st.st_size,
                "mtime": _iso(st.st_mtime),
                "kind": kind,
                "owner": _owner(entry.path, rel, name, kind, index, resolver),
            })
            emitted += 1
        info["files_scanned"] = scanned
        info["rows"] = emitted
        if info["errors"]:
            # Errors with nothing readable at all is UNKNOWN, not an empty root.
            info["status"] = "partial" if scanned else "UNKNOWN"
            if not scanned:
                info["files_scanned"] = None
                info["rows"] = None
        root_reports.append(info)

    by_kind: dict = {}
    by_root: dict = {}
    owners = {"resolved": 0, "unresolved": 0, "unclaimed": 0, "not_applicable": 0}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        by_root[r["root"]] = by_root.get(r["root"], 0) + 1
        owners[r["owner"]["status"]] += 1
    unknown = [r["label"] for r in root_reports if r["status"] == "UNKNOWN"]
    partial = [r["label"] for r in root_reports if r["status"] == "partial"]
    result = {
        "schema": "liteharness.handoff-audit/1",
        "generated_at": _iso(datetime.now(timezone.utc).timestamp()),
        "read_only": True,
        "roots": root_reports,
        "rows": rows,
        "summary": {
            "rows": len(rows), "by_kind": by_kind, "by_root": by_root, "owners": owners,
            "complete": not unknown and not partial,
            "unknown_roots": unknown, "partial_roots": partial,
            "index_errors": index.errors,
        },
    }
    if writable_legacy:
        legacy = {r.label: r for r in roots if r.legacy}
        frozen_all = bool(legacy) and all(is_frozen(r) for r in legacy.values())
        # 3b will make this 0 rows; before the freeze every legacy row is writable.
        legacy_rows = [r for r in rows if r["root"] in legacy]
        result["writable_legacy"] = {
            "frozen": frozen_all, "note": "frozen" if frozen_all else "not frozen yet",
            "rows": 0 if frozen_all else len(legacy_rows),
            "roots": sorted(legacy),
        }
    return result


# ---------------------------------------------------------------------- output

def render_table(result: dict) -> str:
    lines = []
    lines.append(f"{'ROOT':<20} {'KIND':<15} {'SIZE':>9} {'MTIME (UTC)':<24} {'OWNER':<26} {'STATUS':<14} PATH")
    for r in result["rows"]:
        o = r["owner"]
        owner = (o["claim"] or "-") + (f" ({o['source']})" if o["source"] else "")
        lines.append(f"{r['root']:<20} {r['kind']:<15} {r['size']:>9} {r['mtime']:<24} "
                     f"{owner[:26]:<26} {o['status']:<14} {r['path']}")
    s = result["summary"]
    lines.append("")
    lines.append(f"rows={s['rows']} complete={s['complete']} by_kind={s['by_kind']}")
    lines.append(f"owners={s['owners']}")
    for r in result["roots"]:
        lines.append(f"root {r['label']}: {r['status']} scanned={r['files_scanned']} rows={r['rows']} "
                     f"reparse_skipped={r['reparse_skipped']} errors={len(r['errors'])}")
    if "writable_legacy" in result:
        w = result["writable_legacy"]
        lines.append(f"writable-legacy: {w['rows']} rows ({w['note']})")
    return "\n".join(lines)


def cmd_handoffs(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="liteharness handoffs")
    p.add_argument("--audit", action="store_true", help="read-only inventory of legacy handoff files")
    p.add_argument("--format", choices=("table", "json", "both"), default="both")
    p.add_argument("--out", help="also write the JSON manifest to this file")
    p.add_argument("--root", action="append", metavar="LABEL=PATH[@all|@strays][!skip,...][+legacy]",
                   help="scan root (repeatable). Given any --root, BOTH the built-in default and $LITEHARNESS_HANDOFF_AUDIT_ROOTS are ignored. Default mode: candidates; !a,b skips dirs named a,b")
    p.add_argument("--convos-root", help="LiteTUI .convos dir for store owner resolution (default: $LITETUI_CONVOS_ROOT)")
    p.add_argument("--registry-root", help="LiteHarness registry dir (default ~/.liteharness)")
    p.add_argument("--writable-legacy", action="store_true",
                   help="report legacy roots still writable (3b freeze not installed: prints not frozen yet)")
    args = p.parse_args(argv)
    if not args.audit:
        print("Usage: liteharness handoffs --audit [--format table|json|both] [--out FILE] "
              "[--root LABEL=PATH[@mode][!skip,...][+legacy]]... [--convos-root DIR] [--registry-root DIR] "
              "[--writable-legacy]", file=__import__("sys").stderr)
        return 2
    convos = Path(args.convos_root) if args.convos_root else (
        Path(os.environ[ENV_CONVOS]) if os.environ.get(ENV_CONVOS) else None)
    if args.root:
        try:
            roots = [parse_root_spec(spec) for spec in args.root]
        except ValueError as exc:
            print(f"bad --root: {exc}", file=__import__("sys").stderr)
            return 2
    else:
        roots = default_roots(convos)
    if args.out:
        out_resolved = Path(args.out).resolve()
        for r in roots:
            try:
                out_resolved.relative_to(r.path.resolve())
            except (ValueError, OSError):
                continue
            print(f"refusing --out {args.out}: it is inside scanned root {r.label} ({r.path}); "
                  "the audit must not write into what it inventories", file=__import__("sys").stderr)
            return 2
    result = audit(roots, registry_root=Path(args.registry_root) if args.registry_root else None,
                   convos_root=convos, writable_legacy=args.writable_legacy)
    text_json = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text_json, encoding="utf-8")
    if args.format in ("table", "both"):
        print(render_table(result))
    if args.format == "json":
        print(text_json)
    elif args.format == "both":
        print()
        print(text_json)
    if not result["summary"]["complete"]:
        print("handoffs --audit INCOMPLETE: unknown roots=%s partial roots=%s (exit %d)" % (
            result["summary"]["unknown_roots"], result["summary"]["partial_roots"], EXIT_INCOMPLETE),
            file=__import__("sys").stderr)
        return EXIT_INCOMPLETE
    return 0

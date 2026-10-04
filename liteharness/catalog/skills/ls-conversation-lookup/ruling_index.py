"""Opt-in human decision candidates. Source provenance is evidence, not authentication.

Own tables only: never mutates the general conversation, memory or vector index.
No model calls, inferred intent, time-proximity links, or automatic merge verdicts.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time

from conversation_sources import identity, serialized_index

FILTER_VERSION = 2
NON_HUMAN_TEXT_MARKERS = (
    "Base directory for this skill", "This session is being continued",
    "<command-name>", "<local-command", "<system-reminder>", "<task-notification>",
    "<artifact-content-authored-by-others", "[inbox from", "[Summary of earlier conversation",
    "# AGENTS.md instructions", "<environment_context>", "<permissions instructions>",
)
CARD_ID = re.compile(r"T[0-9]+(?:-[A-Za-z0-9]+)*\Z")
CITE_BYTES = 8 * 1024 * 1024
CITE_SECONDS = 1.0


def _raw_records(path, bounded=False):
    """Retain exact physical record hashes; incomplete bounded scans cannot cite."""
    used, deadline = 0, time.monotonic() + CITE_SECONDS
    with Path(path).open("rb") as stream:
        line = 0
        while True:
            if bounded and time.monotonic() > deadline:
                raise TimeoutError("source verification budget exceeded")
            raw = stream.readline(CITE_BYTES - used + 1) if bounded else stream.readline()
            if not raw:
                break
            line += 1
            used += len(raw)
            if bounded and (used > CITE_BYTES or time.monotonic() > deadline):
                raise TimeoutError("source verification budget exceeded")
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict):
                yield line, hashlib.sha256(raw).hexdigest(), record


def _human_class(record):
    if record.get("isMeta") or record.get("isSidechain"):
        return None
    origin = record.get("origin")
    if origin is not None and (not isinstance(origin, dict) or origin.get("kind") != "human"):
        return None
    source, turn = record.get("promptSource"), record.get("turnOrigin")
    if source is not None and source not in ("typed", "human"):
        return None
    if turn is not None and turn != "human":
        return None
    return "human-typed" if origin or source or turn else "legacy-human-candidate"


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    # Mixed text/tool content is not a human turn, even if the prose says the user.
    if any(not isinstance(b, dict) or b.get("type") not in ("text", "input_text") for b in content):
        return ""
    return "\n".join(b.get("text", "") for b in content if isinstance(b.get("text"), str))


def _form(record, questions):
    """Only structured AskUserQuestion answers resolved to a unique earlier call."""
    result = record.get("toolUseResult")
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        return None
    ref = record.get("sourceToolAssistantUUID")
    candidates = questions.get(ref, [])
    blocks = record.get("message", {}).get("content", [])
    if not isinstance(blocks, list):
        return None
    call_ids = {b["tool_use_id"] for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"
                and isinstance(b.get("tool_use_id"), str)}
    candidates = [q for q in candidates if q["call_id"] in call_ids]
    if len(candidates) != 1 or _human_class(record) is None:
        return None
    question = candidates[0]
    # The question echoed by the tool result must match the original tool input.
    if result.get("questions") != question["questions"]:
        return None
    answers = result["answers"]
    if not answers or not all(isinstance(k, str) and isinstance(v, str) for k, v in answers.items()):
        return None
    if not all(isinstance(q, dict) and isinstance(q.get("question"), str) for q in question["questions"]):
        return None
    asked = {q["question"] for q in question["questions"]}
    if not set(answers).issubset(asked):
        return None
    return "\n\n".join(f"Question: {q}\nAnswer: {a}" for q, a in answers.items()), question


def _extract(path):
    provider, convo, project = identity(path)
    questions, assistant_counts = {}, {}
    rows = []
    for line, digest, record in _raw_records(path):
        kind = record.get("type")
        envelope = record.get("message", {})
        uuid = record.get("uuid", "")
        if not isinstance(envelope, dict) or not isinstance(uuid, str):
            continue
        if kind == "assistant":
            assistant_counts[uuid] = assistant_counts.get(uuid, 0) + 1
            content = envelope.get("content", [])
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    inp = b.get("input", {})
                    qs = inp.get("questions") if isinstance(inp, dict) else None
                    if isinstance(qs, list) and isinstance(b.get("id"), str) and b["id"]:
                        questions.setdefault(uuid, []).append({"questions": qs, "call_id": b["id"],
                                                              "hash": digest, "line": line})
            continue
        if kind == "response_item":
            item = record.get("payload", {})
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "user":
                continue
            content, uuid = item.get("content"), item.get("id", "")
        elif kind == "user":
            if envelope.get("role", "user") != "user":
                continue
            content = envelope.get("content")
        else:
            continue
        if not isinstance(uuid, str):
            continue
        ref = record.get("sourceToolAssistantUUID")
        form = (_form(record, questions) if kind == "user" and isinstance(ref, str)
                and assistant_counts.get(ref) == 1 else None)
        question_hash = None
        if form:
            text, question = form
            origin_class = "form-answer"
            question_hash = question["hash"]
        else:
            # An unresolved or unrelated tool result can never become human prose.
            if "toolUseResult" in record:
                continue
            origin_class = _human_class(record)
            text = _text(content)
            if not origin_class or not text.strip() or any(marker in text for marker in NON_HUMAN_TEXT_MARKERS):
                continue
        rows.append({"id": hashlib.sha256((str(path) + "\0" + digest).encode()).hexdigest(),
                     "provider": provider, "conversation_id": convo, "project": project,
                     "source_path": str(path), "record_uuid": str(uuid), "source_hash": digest,
                     "timestamp": record.get("timestamp", ""), "origin_class": origin_class,
                     "text": text, "question_hash": question_hash})
    return rows


def _absolute(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must name an absolute path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{label} must name an absolute path")
    return path.resolve()


def _validate_config(config):
    if not isinstance(config, dict) or set(config) - {"sources", "repositories", "cards"}:
        raise ValueError("ruling config accepts only sources, repositories, cards")
    if not isinstance(config.get("sources"), list) or not config["sources"]:
        raise ValueError("sources must explicitly allowlist at least one transcript")
    if not isinstance(config.get("repositories", []), list) or not isinstance(config.get("cards", []), list):
        raise ValueError("repositories and cards must be lists")
    sources = list(dict.fromkeys(_absolute(p, "source") for p in config["sources"]))
    if any(not p.is_file() or p.suffix != ".jsonl" for p in sources):
        raise ValueError("each source must be an existing JSONL file")
    repos = list(dict.fromkeys(_absolute(p, "repository") for p in config.get("repositories", [])))
    return sources, repos


def _commit_rows(repos):
    for repo in repos:
        # Do not silently accept a path inside an unallowlisted parent repository.
        root = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                                       text=True, timeout=30).strip()
        if Path(root).resolve() != repo:
            raise ValueError(f"repository must be its Git top-level: {repo}")
        output = subprocess.check_output(
            ["git", "-C", str(repo), "log", "--all", "--format=%H%x00%(trailers:key=Task-id,valueonly)%x00"],
            timeout=30).decode("utf-8", errors="replace")
        parts = output.split("\0")
        for n in range(0, len(parts) - 1, 2):
            sha, trailers = parts[n].strip(), parts[n + 1]
            if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
                continue
            for card in trailers.splitlines():
                card = card.strip()
                if CARD_ID.fullmatch(card):
                    yield str(repo), sha, card


SCHEMA = """
CREATE TABLE IF NOT EXISTS rulings (
 id TEXT PRIMARY KEY, provider TEXT NOT NULL, conversation_id TEXT NOT NULL,
 project TEXT NOT NULL, source_path TEXT NOT NULL, record_uuid TEXT NOT NULL,
 source_hash TEXT NOT NULL, timestamp TEXT, origin_class TEXT NOT NULL,
 text TEXT NOT NULL, question_hash TEXT, filter_version INTEGER NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS rulings_fts USING fts5(text, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS ruling_cards (
 ruling_id TEXT NOT NULL, card_id TEXT NOT NULL, basis TEXT NOT NULL,
 PRIMARY KEY(ruling_id,card_id));
CREATE TABLE IF NOT EXISTS ruling_commits (
 ruling_id TEXT NOT NULL, card_id TEXT NOT NULL, repo TEXT NOT NULL, sha TEXT NOT NULL,
 basis TEXT NOT NULL, PRIMARY KEY(ruling_id,card_id,repo,sha));
"""


@serialized_index
def index_rulings(db_path, config):
    """Explicit authoritative allowlist replaces only the ruling layer, atomically."""
    sources, repos = _validate_config(config)
    rows = [r for p in sources for r in _extract(p)]
    cards = []
    for mapping in config.get("cards", []):
        if not isinstance(mapping, dict) or set(mapping) - {"source_path", "record_uuid", "card_id", "source_hash"}:
            raise ValueError("invalid explicit card mapping")
        path = str(_absolute(mapping.get("source_path"), "mapping source_path"))
        card = mapping.get("card_id", "")
        if not isinstance(card, str) or not CARD_ID.fullmatch(card):
            raise ValueError("card_id must be an exact Task-id such as T0262")
        if not isinstance(mapping.get("record_uuid"), str) or not mapping["record_uuid"]:
            raise ValueError("mapping requires a nonempty exact record_uuid")
        if "source_hash" in mapping and (not isinstance(mapping["source_hash"], str)
                                         or not re.fullmatch(r"[0-9a-f]{64}", mapping["source_hash"])):
            raise ValueError("source_hash must be a SHA256 hex string when supplied")
        matches = [r for r in rows if r["source_path"] == path and r["record_uuid"] == mapping["record_uuid"]
                   and ("source_hash" not in mapping or r["source_hash"] == mapping["source_hash"])]
        if len(matches) != 1:
            raise ValueError("card mapping must resolve to exactly one allowed human/form record")
        cards.append((matches[0]["id"], card, "explicit-card-map"))
    commits = list(_commit_rows(repos))
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
        for table in ("ruling_commits", "ruling_cards", "rulings_fts", "rulings"):
            conn.execute(f"DELETE FROM {table}")
        for r in {r["id"]: r for r in rows}.values():
            conn.execute("INSERT INTO rulings VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         tuple(r[k] for k in ("id", "provider", "conversation_id", "project", "source_path",
                               "record_uuid", "source_hash", "timestamp", "origin_class", "text", "question_hash"))
                         + (FILTER_VERSION,))
            rowid = conn.execute("SELECT rowid FROM rulings WHERE id=?", (r["id"],)).fetchone()[0]
            conn.execute("INSERT INTO rulings_fts(rowid,text) VALUES (?,?)", (rowid, r["text"]))
        conn.executemany("INSERT OR IGNORE INTO ruling_cards VALUES (?,?,?)", cards)
        edges = [(rid, card, repo, sha, "explicit-card-map+Task-id") for rid, card, _ in cards
                 for repo, sha, task in commits if task == card]
        conn.executemany("INSERT OR IGNORE INTO ruling_commits VALUES (?,?,?,?,?)", edges)
        conn.commit()
        return {"candidates": len({r["id"] for r in rows}), "sources": len(sources), "commit_links": len(edges)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _citations(path):
    matches = {}
    try:
        for line, digest, _ in _raw_records(path, bounded=True):
            matches.setdefault(digest, []).append(line)
    except (OSError, TimeoutError):
        return {}  # Cannot prove uniqueness or line position after an incomplete scan.
    return {digest: lines[0] for digest, lines in matches.items() if len(lines) == 1}


def search_rulings(db_path, query, limit=10):
    """Read-only query. Empty/missing layer is not evidence of absent decisions."""
    path = Path(db_path).resolve()
    if not path.exists():
        return []
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='rulings_fts'").fetchone():
            return []
        # Treat query as literal tokens, never as executable FTS syntax.
        terms = re.findall(r"\w+", query, re.UNICODE)
        if not terms:
            return []
        match = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        hits = conn.execute("SELECT r.* FROM rulings r JOIN rulings_fts f ON f.rowid=r.rowid "
                            "WHERE rulings_fts MATCH ? ORDER BY bm25(rulings_fts), r.id LIMIT ?",
                            (match, limit)).fetchall()
        result, verified = [], {}
        for row in hits:
            hit = dict(row)
            source = hit["source_path"]
            if source not in verified:
                verified[source] = _citations(source)
            hit["citation"] = {"path": source, "line": verified[source].get(hit["source_hash"])}
            hit["question_citation"] = ({"path": source, "line": verified[source].get(hit["question_hash"])}
                                         if hit["question_hash"] else None)
            hit["cards"] = [dict(r) for r in conn.execute(
                "SELECT card_id,basis FROM ruling_cards WHERE ruling_id=? ORDER BY card_id", (hit["id"],))]
            hit["commits"] = [dict(r) for r in conn.execute(
                "SELECT card_id,repo,sha,basis FROM ruling_commits WHERE ruling_id=? ORDER BY repo,sha", (hit["id"],))]
            hit["resolution_verified"] = bool(hit["citation"]["line"] and (
                not hit["question_hash"] or hit["question_citation"]["line"]))
            hit["status"] = "decision-candidate, not an intent-gate verdict"
            result.append(hit)
        return result
    finally:
        conn.close()


def run_cli(args, db_path):
    """Early dispatch: ruling commands never refresh the general conversation index."""
    try:
        modes = [a for a in args if a in ("--index-rulings", "--search-rulings")]
        if len(modes) != 1 or any(a in args for a in ("--index", "--index-memory", "--index-embeddings", "--search", "--backfill-provenance")):
            raise ValueError("choose exactly one ruling command, without general index/search commands")
        for option in ("--ruling-config", "--search-rulings", "-n"):
            if option in args:
                n = args.index(option) + 1
                if n >= len(args) or args[n].startswith("-"):
                    raise ValueError(f"{option} requires a value")
        if "--index-rulings" in args:
            if "--ruling-config" not in args:
                raise ValueError("--index-rulings requires --ruling-config <JSON allowlist>")
            config = Path(args[args.index("--ruling-config") + 1])
            print(json.dumps(index_rulings(db_path, json.loads(config.read_text(encoding="utf-8"))), indent=2))
        else:
            query = args[args.index("--search-rulings") + 1]
            limit = int(args[args.index("-n") + 1]) if "-n" in args else 10
            hits = search_rulings(db_path, query, limit)
            print(json.dumps({"results": hits, "unknowns": [
                "No hits means no indexed match (or ruling layer not indexed), not absence of human decisions.",
                "Human provenance is not authentication; legacy-human-candidate is explicitly unverified.",
                "Task-id links prove traceability, not that a commit implements the decision.",
                "Source line is unknown when missing, duplicated or outside the 8 MiB / 1 second verification budget.",
            ]}, indent=2, ensure_ascii=False))
        return 0
    except (ValueError, OSError, IndexError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}")
        return 1

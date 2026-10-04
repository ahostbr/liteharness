"""T0222: canonical append-only lifecycle evidence; requests are not observed exits.

PTY owners produce spawn/exit. Registration and registry cleanup only produce
identity/audit events. Never dump argv, environment, or arbitrary presence fields.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def log_path() -> Path:
    return Path.home() / ".litesuite" / "harness" / "seat-lifecycle.jsonl"


def scrub(text: str, secrets: tuple[str, ...] = ()) -> str:
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]", "", text)
    for secret in secrets:
        if len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\bBearer\s+[^\s\"',;]+", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"((?:[\w-]*(?:token|secret|password|api[_-]?key|authorization)[\w-]*)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}]+", r"\1[REDACTED]", text, flags=re.I)
    text = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})\b", "[REDACTED]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def append(event: dict, *, path: Path | None = None) -> str | None:
    path = path or log_path()
    record_id = str(uuid.uuid4())
    row = {"schema_version": 1, "record_id": record_id,
           "timestamp": datetime.now(timezone.utc).isoformat(), "writer_pid": os.getpid(), **event}
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = (json.dumps(row, ensure_ascii=True) + "\n").encode("utf-8")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, payload) != len(payload):
                raise OSError("short lifecycle append")
        finally:
            os.close(fd)
        return record_id
    except OSError as exc:
        print(f"[seat-lifecycle] append failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def capture_registration_identity(presence: dict) -> None:
    """Capture creation only at registration; never sample a recycled PID at cleanup."""
    import psutil
    presence.pop("session_process_started_at", None)
    pid = presence.get("session_pid")
    presence.pop("session_run_id", None)
    run = os.environ.get("LITEHARNESS_SEAT_RUN_ID")
    try:
        if run:
            uuid.UUID(run)
            presence["session_run_id"] = run
        if isinstance(pid, int) and pid > 0:
            started = psutil.Process(pid).create_time()
            registered = datetime.fromisoformat(presence["registered_at"].replace("Z", "+00:00")).timestamp()
            if started <= registered + 5:
                presence["session_process_started_at"] = started * 1000
    except (psutil.Error, KeyError, TypeError, ValueError):
        pass


def identity(presence: dict) -> dict:
    return {"seat_id": presence.get("agent_id"),
            "terminal_id": presence.get("canvas_session_id") or presence.get("provisional_id"),
            "pid": presence.get("session_pid"),
            "process_started_at": presence.get("session_process_started_at"),
            "registered_at": presence.get("registered_at"),
            "run_id": presence.get("session_run_id")}


def registration(presence: dict, source: str) -> None:
    append({"event": "register", **identity(presence), "owner": source,
            "seat_name": presence.get("name"), "spawned_by": presence.get("spawned_by")})


def preserve_before_delete(path: Path, expected: dict, reason: str) -> bool:
    """Archive resumable identity BEFORE removing transient presence; fail closed.

    No live restoration. A resumed process must register normally. Changed/unreadable
    records are not deleted. Existing roster semantics remain intact.
    """
    from . import config
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
        if current != expected:
            return False
        archive = config.get_root() / "retired-agents" / path.name
        # Resume/identity allowlist: never archive arbitrary RPC/auth payloads or recaps.
        fields = ("agent_id", "name", "tier", "model", "backend", "thinking_level", "cli",
                  "spawned_by", "session_pid", "session_process_started_at", "session_run_id",
                  "started_at", "registered_at", "canvas_session_id", "provisional_id", "spawn_mode")
        saved = {key: current[key] for key in fields if key in current}
        saved.update(retired_at=datetime.now(timezone.utc).isoformat(), retirement_reason=reason)
        config.atomic_write_json(archive, saved)
        record = append({"event": "registry_delete_requested", **identity(current),
                         "owner": "liteharness-registry", "origin": {
                             "source": reason, "actor_id": config.get_agent_id()},
                         "archive_path": str(archive)})
        if not record:
            return False
        saved["lifecycle_registry_record_id"] = record
        config.atomic_write_json(archive, saved)
        # Recheck after archival/logging so a registration that arrived meanwhile wins.
        if json.loads(path.read_text(encoding="utf-8")) != current:
            return False
        from . import agent_names
        if agent_names.owns_conversation(current.get("agent_id") or path.stem, current):
            # T0236: an agent that owns a conversation is never DELETED -- the presence
            # file is what `resume` and the roster resolve it through. Mark it offline
            # (`exited_at` is the existing "not live" flag every liveness check honours).
            offline = dict(current, exited_at=saved["retired_at"], status="offline",
                           retired_at=saved["retired_at"], retirement_reason=reason)
            config.atomic_write_json(path, offline)
            append({"event": "registry_marked_offline", **identity(current),
                    "owner": "liteharness-registry", "request_record_id": record,
                    "archive_path": str(archive)})
            return True
        path.unlink()
        append({"event": "registry_deleted", **identity(current),
                "owner": "liteharness-registry", "request_record_id": record,
                "archive_path": str(archive)})
        return True
    except (OSError, ValueError):
        return False


class ProcessLifecycle:
    """Exactly one recorder retained by an actual Python PTY process owner."""
    def __init__(self, seat_id: str, terminal_id: str, pid: int, started_at: float,
                 env: dict[str, str], *, sink=append):
        self.identity = {"owner": "python-pty-daemon", "seat_id": seat_id,
                         "terminal_id": terminal_id, "pid": pid,
                         "process_started_at": None, "observed_started_at": started_at * 1000,
                         "run_id": env.get("LITEHARNESS_SEAT_RUN_ID") or str(uuid.uuid4())}
        self._sink = sink
        self._start = started_at
        self._output = ""
        self._context = None
        self._kill = None
        self._origin = None
        self._exited = False
        self._secrets = tuple(v for k, v in env.items()
                              if re.search(r"token|secret|password|api[_-]?key|authorization", k, re.I))
        self.record("spawn")

    def record(self, event: str, **details):
        return self._sink({"event": event, **self.identity, **details})

    def data(self, data: str) -> None:
        self._output = (self._output + data)[-65536:]
        matches = re.findall(r"(?:context(?:\s+(?:used|usage))?\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*(?:context|ctx))", scrub(self._output), re.I)
        if matches:
            value = float(matches[-1][0] or matches[-1][1])
            if 0 <= value <= 100:
                self._context = value

    def request_kill(self, origin: dict | None = None) -> None:
        if self._exited:
            return
        origin = origin or {"source": "unknown-close-caller"}
        self._origin = {key: scrub(str(origin[key]))[:128]
                        for key in ("source", "actor_id", "request_id") if key in origin}
        self._kill = self.record("kill_requested", origin=self._origin)

    def exit(self, code: int | None, signal: int | None = None, error: str | None = None) -> None:
        if self._exited:
            return
        self._exited = True
        lines = [line[-512:] for line in scrub(self._output, self._secrets).splitlines()[-50:]]
        text = "\n".join(lines)
        dump = re.search(r"(?:crash\s*dump|dump\s*(?:path|file))\s*[:=]\s*([^\r\n]+)", text, re.I)
        self.record("exit", exit_code=code, signal=signal, origin=self._origin,
                    kill_record_id=self._kill, unexplained=self._origin is None,
                    actual_cause="unknown", uptime_ms=max(0, (time.time() - self._start) * 1000),
                    last_context_percent=self._context, output_tail=lines,
                    traceback=text if re.search(r"Traceback \(most recent call last\)|(?:Error|Exception):|\bat .+:\d+:\d+", text) else None,
                    owner_error=scrub(error, self._secrets)[-2048:] if error else None,
                    crash_dump_path=dump[1] if dump else None)


def records(*, since: str | None = None, unexplained: bool = False, path: Path | None = None) -> list[dict]:
    """Read linewise; report corrupt records, never silently declare a clean log."""
    cutoff = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp() if since else 0
    rows = []
    try:
        fh = (path or log_path()).open(encoding="utf-8")
    except FileNotFoundError:
        return []
    with fh:
        for number, line in enumerate(fh, 1):
            try:
                row = json.loads(line)
                stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid lifecycle line {number}: {type(exc).__name__}") from exc
            if stamp >= cutoff and (not unexplained or row.get("event") == "exit" and row.get("unexplained")):
                rows.append(row)
    return rows


def same_process(left: dict, right: dict) -> bool:
    """Join known process incarnations only. Seat/terminal/PID alone never suffices."""
    if left.get("run_id") and right.get("run_id"):
        return left["run_id"] == right["run_id"]
    started = left.get("process_started_at")
    other_started = right.get("process_started_at")
    return bool(left.get("seat_id") and left.get("seat_id") == right.get("seat_id") and
                isinstance(left.get("pid"), int) and left["pid"] > 0 and
                left["pid"] == right.get("pid") and
                isinstance(started, (int, float)) and isinstance(other_started, (int, float)) and
                started > 0 and abs(started - other_started) < 1)


def unexplained_since(rows: list[dict], since: str | None) -> list[dict]:
    """Include vanished registered processes, without claiming an observed exit/status.

    the orchestrator invokes this read-only check; failures probing a process are UNKNOWN,
    not death. External kill requests join only the same identity and later time.
    """
    import psutil
    cutoff = datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp() if since else 0
    kills = [r for r in rows if r.get("event") == "kill_requested"]
    result = []
    seen = set()
    observed = [r for r in rows if r.get("event") == "exit"]
    for row in reversed(rows):
        if row.get("event") not in ("exit", "register"):
            continue
        key = (row.get("seat_id"), row.get("pid"), row.get("process_started_at") or row.get("registered_at"))
        if row.get("event") == "register" and key in seen:
            continue
        seen.add(key)
        stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).timestamp()
        if row.get("event") == "exit":
            if not row.get("unexplained") or stamp < cutoff:
                continue
            # An OS kill request can precede the owner callback but share no run ID.
            requested = any(same_process(k, row) and
                            k.get("timestamp", "") <= row["timestamp"] and
                            k.get("timestamp", "") >= str(row.get("registered_at") or
                                datetime.fromtimestamp((row.get("process_started_at") or row.get("observed_started_at") or 0) / 1000,
                                                       timezone.utc).isoformat()) for k in kills)
            if not requested:
                result.append(row)
            continue
        pid = row.get("pid")
        if any(same_process(e, row) and
               e.get("timestamp", "") >= row["timestamp"] for e in observed):
            continue
        if not isinstance(pid, int) or pid <= 0:
            continue
        try:
            process = psutil.Process(pid)
            started = row.get("process_started_at")
            # An existing PID with unknown creation is neither this seat nor proven gone.
            if not isinstance(started, (int, float)) or started <= 0:
                continue
            if abs(process.create_time() * 1000 - started) < 1:
                continue
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            continue
        if any(same_process(k, row) and
               k.get("timestamp", "") >= row["timestamp"] for k in kills):
            continue
        # Detection time, not an invented death timestamp; keep the original record link.
        result.append({**row, "event": "process_gone", "unexplained": True,
                       "detected_at": datetime.now(timezone.utc).isoformat(),
                       "actual_cause": "unknown", "exit_code": None, "signal": None})
    return list(reversed(result))


def latest_for(presence: dict, rows: list[dict]) -> dict | None:
    """No stale incarnation links: known creation/run identity or exact deletion receipt."""
    target = identity(presence)
    if presence.get("run_id"):
        target["run_id"] = presence["run_id"]
    matches = [r for r in rows if r.get("event") in ("exit", "registry_deleted", "registry_marked_offline") and
               (same_process(target, r) or
                r.get("event") in ("registry_deleted", "registry_marked_offline") and
                presence.get("lifecycle_registry_record_id") and
                r.get("request_record_id") == presence["lifecycle_registry_record_id"])]
    return matches[-1] if matches else None

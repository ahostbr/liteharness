"""Bounded, fail-isolated pasted-image history for the existing Claude hooks.

No transcript text, paths, tokens or remote images are rendered. Theater owns its
page/session metadata; this module writes only its own hidden state and assets.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

SCAN_BYTES = 16 * 1024 * 1024
IMAGE_BYTES = 5 * 1024 * 1024
STORAGE_BYTES = 256 * 1024 * 1024
HISTORY_LIMIT = 256
LOCAL_BUDGET = 2.0
BRIDGE_TIMEOUT = 0.75
MIME_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
EVENTS = {("check", "SessionStart"), ("memory-nudge", "UserPromptSubmit"),
          ("check", "PostToolUse"), ("obs", "Stop")}


class CaptureError(ValueError):
    """Only static, credential-free diagnostic messages may use this type."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CaptureError("bridge redirect refused")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size > 256 * 1024:
        raise CaptureError("history state exceeds limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CaptureError("invalid history state")
    return value


def _atomic(path: Path, data: bytes) -> None:
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as stream:
            stream.write(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _save(path: Path, value: dict) -> None:
    _atomic(path, json.dumps(value, separators=(",", ":")).encode("utf-8"))


def _identity(stream) -> list:
    stat = os.fstat(stream.fileno())
    stream.seek(0)
    return [stat.st_dev, stat.st_ino]


def _images(record: dict):
    message = record.get("message")
    if (record.get("type") != "user" or record.get("isMeta")
            or not isinstance(message, dict) or message.get("role") != "user"):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image":
            continue  # deliberately never recurse into tool_result content
        source = block.get("source")
        if not isinstance(source, dict) or source.get("type") != "base64":
            continue
        mime, encoded = source.get("media_type"), source.get("data")
        if mime not in MIME_EXT or not isinstance(encoded, str):
            raise CaptureError("unsupported pasted image MIME/encoding")
        if len(encoded) > ((IMAGE_BYTES + 2) // 3) * 4:
            raise CaptureError("pasted image exceeds 5 MiB limit")
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > IMAGE_BYTES:
            raise CaptureError("empty/oversized pasted image")
        valid = {"image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
                 "image/jpeg": data.startswith(b"\xff\xd8\xff"),
                 "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
                 "image/webp": data.startswith(b"RIFF") and data[8:12] == b"WEBP"}
        if not valid[mime]:
            raise CaptureError("pasted image signature does not match MIME")
        yield hashlib.sha256(data).hexdigest() + "." + MIME_EXT[mime], data


def _page(entries: list[dict]) -> bytes:
    # Only module-generated asset names and ordinal labels reach TSX, never
    # transcript text/captions. JSON is data inside JSX expressions, not markup.
    names = [entry["file"] for entry in entries]
    for name in names:
        stem, ext = name.split(".")
        if len(stem) != 64 or any(c not in "0123456789abcdef" for c in stem) or ext not in MIME_EXT.values():
            raise CaptureError("invalid history asset name")
    source = '''export default function Page() {
  const images = NAMES;
  return <main style={{background:'#090d16',color:'#e5e7eb',minHeight:'100vh',padding:20,fontFamily:'system-ui'}}>
    <h1 style={{fontSize:20}}>Pasted images</h1>
    <p style={{color:'#94a3b8'}}>Newest first · kept locally across turns</p>
    <section style={{display:'grid',gap:16}}>{images.map((name, index) =>
      <figure key={name} style={{margin:0,padding:12,background:'#151c2b',borderRadius:12}}>
        <a href={'assets/' + name} target="_blank" rel="noreferrer">
          <img src={'assets/' + name} alt={'Pasted image ' + (images.length-index)}
            style={{display:'block',maxWidth:'100%',maxHeight:480,objectFit:'contain'}} />
        </a>
        <figcaption style={{paddingTop:8,color:'#94a3b8'}}>Paste {images.length-index}</figcaption>
      </figure>)}</section>
  </main>;
}
'''.replace("NAMES", json.dumps(names))
    return source.encode("utf-8")


def _open(project: Path, agent_id: str, agents_dir: Path, leaf_id: str | None) -> bool:
    if not agent_id or Path(agent_id).name != agent_id or "\\" in agent_id:
        raise CaptureError("no registered page owner")
    presence = _read_json(agents_dir / f"{agent_id}.json")
    if not presence:
        raise CaptureError("no registered page owner")
    url = os.environ.get("LITESUITE_BRIDGE_URL", "http://127.0.0.1:7423")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or parsed.username or parsed.password:
        raise CaptureError("bridge must be local HTTP")
    try:
        token = (Path.home() / ".litesuite" / "bridge-token").read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    token = token or os.environ.get("LITESUITE_BRIDGE_TOKEN", "")
    if not token:
        raise CaptureError("bridge token unavailable; images retained locally")
    body = {"projectDir": str(project), "pageId": "pastes", "title": "Pasted images",
            "owner": {"id": agent_id, "name": str(presence.get("name") or agent_id[:8]),
                      "tier": str(presence.get("tier") or "unknown")},
            "leafId": leaf_id}
    request = urllib.request.Request(url.rstrip("/") + "/theater/open",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    # Never echo HTTP response bodies/exceptions: these can contain credentials.
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(request, timeout=BRIDGE_TIMEOUT) as response:
        raw = response.read(64 * 1024 + 1)
    if len(raw) > 64 * 1024:
        raise CaptureError("oversized bridge response")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("error"):
        raise CaptureError("Theater open failed; images retained locally")
    return True


def _project_root(cwd: Path) -> Path:
    """Stable root: the hook cwd follows the agent's cd (into e.g. an Unreal Content/ tree)."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env and Path(env).is_absolute() and Path(env).is_dir():
        return Path(env)
    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        top = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=3, creationflags=flags).stdout.strip()
        if top and Path(top).is_dir():
            return Path(top)
    except Exception:
        pass
    return cwd


def capture(hook_input: dict, action: str, agent_id: str, agents_dir: Path) -> None:
    """Dispatch once per selected existing event, with no hook stdout changes."""
    event = hook_input.get("hook_event_name")
    if (action, event) not in EVENTS:
        return
    transcript, cwd = hook_input.get("transcript_path"), hook_input.get("cwd")
    if not isinstance(transcript, str) or not isinstance(cwd, str):
        return
    project, transcript = Path(cwd), Path(transcript)
    if not project.is_absolute() or not transcript.is_absolute():
        return
    project = _project_root(project)
    deadline = time.monotonic() + LOCAL_BUDGET
    directory = project / ".litesuite" / "theater" / "pastes"
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".capture.lock"
    # Do not wait behind another hook. A later tool/Stop event retries the cursor.
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise CaptureError("history capture busy; later event required (stale lock needs manual removal)")
    os.close(fd)
    try:
        key = hashlib.sha256((str(transcript) + "\0" + str(hook_input.get("session_id", ""))).encode()).hexdigest()
        state_path = directory / f".cursor-{key}.json"
        state = _read_json(state_path)
        if not transcript.exists():
            if event == "SessionStart":
                _save(state_path, {"offset": 0, "identity": None, "pending": False})
            return
        with transcript.open("rb") as stream:
            identity = _identity(stream)
            size = os.fstat(stream.fileno()).st_size
            offset = state.get("offset", size)
            prefix_len = state.get("prefix_len", 0)
            stream.seek(0)
            prefix = hashlib.sha256(stream.read(prefix_len)).hexdigest()
            replaced = (state.get("identity") not in (None, identity)
                        or size < offset or (prefix_len and prefix != state.get("prefix")))
            if event == "SessionStart" or not state or replaced:
                length = min(size, 256)
                stream.seek(0)
                _save(state_path, {"offset": size, "identity": identity, "pending": False,
                                  "prefix_len": length, "prefix": hashlib.sha256(stream.read(length)).hexdigest()})
                if event != "SessionStart":
                    raise CaptureError("transcript baseline reset; historical images not replayed")
                return
            state["identity"] = identity
            if not prefix_len and size:
                length = min(size, 256)
                stream.seek(0)
                state.update(prefix_len=length, prefix=hashlib.sha256(stream.read(length)).hexdigest())
            if event == "UserPromptSubmit":
                state["pending"] = True
            _save(state_path, state)
            history_path = directory / ".history.json"
            history = _read_json(history_path)
            entries = history.get("entries", [])
            _page(entries)  # validate local manifest before using its paths
            total = sum(entry["bytes"] for entry in entries)
            used = 0
            changed = False
            stream.seek(offset)
            while state.get("pending") and stream.tell() < size:
                if time.monotonic() >= deadline or used >= SCAN_BYTES:
                    raise CaptureError("transcript scan budget reached; cursor retained for retry")
                start = stream.tell()
                line = stream.readline(min(SCAN_BYTES - used + 1, size - start))
                if len(line) > SCAN_BYTES - used:
                    raise CaptureError("transcript record exceeds scan budget; cursor retained")
                if not line.endswith(b"\n"):
                    break  # partial JSONL append: retry this same line next event
                used += len(line)
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise CaptureError("invalid transcript record")
                for name, data in _images(record):
                    if time.monotonic() >= deadline:
                        raise CaptureError("image capture budget reached; cursor retained")
                    if any(entry["file"] == name for entry in entries):
                        continue
                    if len(entries) >= HISTORY_LIMIT or total + len(data) > STORAGE_BYTES:
                        raise CaptureError("history storage limit reached; no images deleted")
                    assets = directory / "assets"
                    assets.mkdir(exist_ok=True)
                    # Count all files, including leftovers from interrupted publication.
                    storage = 0
                    for asset in assets.iterdir():
                        if time.monotonic() >= deadline:
                            raise CaptureError("storage scan budget reached")
                        if asset.is_file():
                            storage += asset.stat().st_size
                    if storage + len(data) > STORAGE_BYTES:
                        raise CaptureError("history storage limit reached; no images deleted")
                    _atomic(assets / name, data)
                    if not entries:
                        # Bind the first capture, not the later hook that happens
                        # to retry a cwd-shared history after bridge failure.
                        history["opener"] = {"cursor": key, "agentId": agent_id,
                                             "leafId": os.environ.get("LITESUITE_LEAF_ID") or None}
                    entries.insert(0, {"file": name, "bytes": len(data)})
                    total += len(data)
                    changed = True
                if changed:
                    history["entries"] = entries
                    _save(history_path, history)
                    changed = False
                state["offset"] = stream.tell()
                _save(state_path, state)
            # Repair interrupted page publication even on a repeated event.
            if entries:
                source = _page(entries)
                page = directory / "page.tsx"
                if not page.exists() or page.read_bytes() != source:
                    _atomic(page, source)
            if event == "Stop" and state.get("offset") == size:
                state["pending"] = False
            _save(state_path, state)
            opener = history.get("opener")
            # Legacy histories without capture provenance stay inert: no guessing
            # or migration of existing Theater ownership from a later hook.
            if (entries and not history.get("opened") and isinstance(opener, dict)
                    and opener.get("cursor") == key and opener.get("agentId") == agent_id):
                history["opened"] = _open(project, agent_id, agents_dir, opener.get("leafId"))
                _save(history_path, history)
    finally:
        lock.unlink(missing_ok=True)


def handle(hook_input: dict, action: str, agent_id: str, agents_dir: Path) -> None:
    """Display errors must not change inbox, guard, or prompt-hook semantics."""
    try:
        capture(hook_input, action, agent_id, agents_dir)
    except Exception as exc:
        # Type + a static reason for our own errors, never external details.
        reason = str(exc) if type(exc) is CaptureError else type(exc).__name__
        print(f"[paste-history] {reason}", file=sys.stderr)

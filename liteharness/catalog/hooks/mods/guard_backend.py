"""Mods policy bridge. Target commands are DATA: this file never executes them.

Run by path; only stdlib is loaded before the authoritative floor is resolved.
A broken/missing installed floor is a refusal, never an approval. Python settings
hooks remain enabled; this bridge does not install, remove, or change them.
"""
import sys

if __name__ == "__main__" and sys.path:
    del sys.path[0]

import importlib.metadata
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

PROTOCOL = 1
FILE_WRITES = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
# Ported from no_full_suite.py, SHA256 recorded in docs/claude-guards.md.
TEST_HEAD = re.compile(r"^(?:\w+=\S+\s+)*(?:bunx\s+|npx\s+|python\s+-m\s+)?(vitest|playwright|pytest|cargo\s+test|bun\s+run\s+test|bun\s+test|npm\s+(run\s+)?test|pnpm\s+(run\s+)?test|yarn\s+(run\s+)?test)\b")
FULL_SUITE = [
    r"\bbun\s+run\s+test(\b|:)",
    r"\bbun\s+test\b(?![^\n|;&]*\.(ts|tsx|js|mjs))",
    r"\b(bunx\s+|npx\s+)?vitest\s+(run\b)?(?![^\n|;&]*\.(ts|tsx|js|mjs))",
    r"\b(bunx\s+|npx\s+)?playwright\s+test\b(?![^\n|;&]*\.(ts|tsx|js|mjs))",
    r"\bcargo\s+test\b(?![^\n|;&]*\s\w)",
    r"\b(pnpm|npm|yarn)\s+(run\s+)?test\b",
    r"\bpytest\b(?![^\n|;&]*\.py)",
]
WORKTREE_REMOVE = re.compile(r"\bgit\b[^\n;&|]*\bworktree\s+remove\b", re.I)


def load_gate():
    # Resolve the distribution's actual file, not a shell-supplied module/path.
    distribution = importlib.metadata.distribution("liteharness")
    path = Path(distribution.locate_file("liteharness/deny_gate.py"))
    if not path.is_file():
        # Editable installs describe their source in direct_url.json.
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
        from urllib.parse import unquote, urlparse
        url = urlparse(direct.get("url", ""))
        if url.scheme != "file" or url.netloc not in ("", "localhost"):
            raise ValueError("Installed deny floor cannot be located")
        source = unquote(url.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", source):
            source = source[1:]
        path = Path(source) / "liteharness" / "deny_gate.py"
    import hashlib
    expected = {"deny_gate.py": "15e98aea4ac8154d028aadc2e375e18892d202a95794b9fbe82ef59706d3cd79",
                "deny_floor.py": "7703738d2d6b1919a440e45543b4016e099eb8b136ef939a7014cfe07315d60e"}
    # Policy source: OSS 9940abe59abb643c6d3181171c9c898e2eef51d7; package version 0.4.4.
    if distribution.version != "0.4.4":
        raise ValueError("Unverified installed policy version")
    from types import ModuleType
    modules = {}
    for name, digest in expected.items():
        source = path.with_name(name).read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(source).hexdigest() != digest:
            raise ValueError("Unverified installed policy source")
        module = ModuleType("mods_authoritative_" + name[:-3])
        module.__file__ = str(path.with_name(name))
        # Execute exactly the verified bytes, never a reopened source or .pyc.
        exec(compile(source, module.__file__, "exec"), module.__dict__)
        modules[name] = module
    gate = modules["deny_gate.py"]
    gate._load_floor = lambda: modules["deny_floor.py"]
    return gate


def reply(deny=None, allow=False, command=None):
    return {"protocol": PROTOCOL, "deny": deny, "allowOwnWrite": allow, "command": command}


def validate(payload):
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL:
        raise ValueError("Unsupported guard protocol")
    if payload.get("kind") not in {"call", "check"}:
        raise ValueError("Unsupported event kind")
    if not isinstance(payload.get("tool_input"), dict):
        raise ValueError("Unsupported tool input")
    for key in ("tool_name", "cwd", "session_id"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError("Missing tool/session context")
    if not IDENTITY.fullmatch(payload["session_id"]):
        raise ValueError("Invalid session identity")
    inputs = payload["tool_input"]
    if payload["tool_name"] in FILE_WRITES:
        key = "notebook_path" if payload["tool_name"] == "NotebookEdit" else "file_path"
        if not isinstance(inputs.get(key), str) or not inputs[key]:
            raise ValueError("Unsupported file write payload")
    if payload["tool_name"] == "Bash" and not isinstance(inputs.get("command"), str):
        raise ValueError("Unsupported Bash payload")
    for key in ("command", "text", "input", "data", "cwd"):
        if key in inputs and not isinstance(inputs[key], str):
            raise ValueError("Unsupported executable/context field")


def trusted_helper(name, forbidden=None):
    """Resolve absolute PATH entries, never the target worktree/current directory.

    Installed interpreter and host-owned PATH remain deployment trust boundaries;
    a repository-local helper must not manufacture proof or run in this bridge.
    """
    forbidden = Path(forbidden or os.getcwd()).resolve()
    suffixes = [".exe"] if os.name == "nt" else [""]
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        base = Path(directory)
        if not base.is_absolute():
            continue
        for suffix in suffixes:
            candidate = base / (name + suffix)
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved.is_relative_to(forbidden) or not safe_components(candidate):
                continue
            return str(resolved)
    raise ValueError("Trusted helper unavailable")


def git_query(arguments, cwd):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    return subprocess.run([trusted_helper("git", cwd), *arguments], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=3, check=True).stdout


def runner_pids():
    if os.name == "nt":
        command = [trusted_helper("powershell"), "-NoProfile", "-Command",
                   'Get-CimInstance Win32_Process -Filter "Name=\'bun.exe\' OR Name=\'node.exe\'" | '
                   "Where-Object { $_.CommandLine -match 'vitest|playwright' } | "
                   "ForEach-Object { $_.ProcessId }"]
        output = subprocess.run(command, capture_output=True, text=True, timeout=8, check=True)
        return output.stdout.split()
    # Do not claim serialization if the process census is unavailable.
    output = subprocess.run([trusted_helper("ps"), "-eo", "pid=,comm=,args="], capture_output=True,
                            text=True, timeout=8, check=True)
    return [line.split()[0] for line in output.stdout.splitlines()
            if re.search(r"\b(?:node|bun)\b.*\b(?:vitest|playwright)\b", line)]


def suite_refusal(command, census=runner_pids):
    if os.environ.get("LITEHARNESS_ALLOW_FULL_SUITE") == "1":
        return None
    runs = [s.strip() for s in re.split(r"\n|&&|\|\||;|\|", command)
            if TEST_HEAD.match(s.strip())]
    if not runs:
        return None
    if any(re.search(pattern, segment) for pattern in FULL_SUITE for segment in runs):
        return "RULE 13: no full test suites on a seat; run explicit touched test files only."
    pids = census()
    if pids:
        return "RULE 13: another test runner is alive; wait before running touched files."
    return None


def literal_tokens(command):
    """Small bounded literal-command grammar; never evaluates shell expansions.

    Whole quoted tokens preserve backticks/$ literally. Complex syntax is refused
    for rewrite/removal analysis, not normalized into something that might differ.
    """
    tokens = []
    i = 0
    while i < len(command):
        if command[i].isspace():
            i += 1
            continue
        start = i
        prefix = ""
        assignment = re.match(r"[A-Za-z_][A-Za-z0-9_-]*=", command[i:])
        if assignment:
            prefix = assignment.group()
            i += len(prefix)
        if i == len(command):
            tokens.append((prefix, start, i, None))
            continue
        quote = command[i] if command[i] in "\"'" else None
        if quote:
            i += 1
            begin = i
            while i < len(command) and command[i] != quote:
                if command[i] == "\\" or command[i] in "\r\n":
                    raise ValueError("Unsupported quoted escape or multiline shell command")
                i += 1
            if i == len(command):
                raise ValueError("Unterminated quoted shell argument")
            value = command[begin:i]
            i += 1
            if i < len(command) and not command[i].isspace():
                raise ValueError("Unsupported concatenated shell argument")
        else:
            while i < len(command) and not command[i].isspace():
                if command[i] in ";&|<>`$(){}\"'\\":
                    raise ValueError("Unsupported shell operator or expansion")
                i += 1
            value = command[start + len(prefix):i]
        tokens.append((prefix + value, start, i, quote))
    return tokens


def is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def safe_components(path):
    """Check existing components without following links; absent leaf is okay."""
    path = Path(os.path.abspath(path))
    for component in [*reversed(path.parents), path]:
        try:
            if is_reparse(component):
                return False
        except FileNotFoundError:
            continue
    return True


def junction_refusal(command, cwd):
    # Classify removal BEFORE inspecting force options: Git accepts unique long
    # prefixes such as --for/--forc. Unknown options must never skip this guard.
    hint = WORKTREE_REMOVE.search(command.replace("'", "").replace('"', ""))
    if not hint:
        return None
    tokens = [item[0] for item in literal_tokens(command)]
    if tokens[:3] != ["git", "worktree", "remove"]:
        raise ValueError("Unsupported worktree removal syntax")
    forced = False
    paths = []
    after_separator = False
    for word in tokens[3:]:
        if not after_separator and word == "--":
            after_separator = True
        elif not after_separator and word in {"--force", "-f"}:
            forced = True
        elif not after_separator and word.startswith("-"):
            raise ValueError("Unsupported worktree removal option; use literal --force or -f")
        else:
            paths.append(word)
    if len(paths) != 1:
        raise ValueError("Worktree removal requires one literal target")
    if not forced:
        return None
    target = Path(cwd) / paths[0]
    if not safe_components(target) or not target.is_dir():
        return "Forced worktree removal blocked: target is missing or has a link/reparse component."
    pending = [target]
    count = 0
    while pending:
        directory = pending.pop()
        for entry in directory.iterdir():
            count += 1
            if count > 10000:
                return "Forced worktree removal blocked: junction scan exceeded bounded budget."
            if is_reparse(entry):
                return "Forced worktree removal blocked: target contains a junction/symlink/reparse point."
            if entry.is_dir():
                pending.append(entry)
    return None


def assignment_record(session_id, home):
    """Bind one authoritative registration to this live Claude parent process.

    Missing psutil/identity evidence earns no allow. Unlike legacy takeover
    heuristics, uncertainty is NOT affirmative evidence in a permission path.
    """
    from datetime import datetime, timezone
    import psutil

    parent = psutil.Process(os.getppid())
    if "claude" not in parent.name().lower():
        return None
    candidates = []
    paths = list((home / "agents").glob("*.json"))
    if len(paths) > 1000:
        return None
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("session_pid") != parent.pid or record.get("status") == "offline":
            continue
        if record.get("cli") != "claude-code" or record.get("registration_source") != "takeover":
            continue
        registered = datetime.fromisoformat(record.get("registered_at", ""))
        if registered.tzinfo is None:
            return None
        if not parent.create_time() <= registered.timestamp() <= datetime.now(timezone.utc).timestamp():
            continue
        candidates.append(record)
    if len(candidates) != 1:
        return None
    record = candidates[0]
    identities = {record.get("agent_id"), record.get("session_id"),
                  (record.get("spatial") or {}).get("session_id")}
    return record if session_id in identities else None


def own_write(payload, home=None):
    if payload["tool_name"] not in FILE_WRITES:
        return False
    home = Path(home) if home else Path.home() / ".liteharness"
    # Registry is a host-owned deployment boundary, not input supplied by the tool.
    record = assignment_record(payload["session_id"], home)
    if record is None or not safe_components(Path(payload["cwd"])):
        return False
    root = Path(payload["cwd"]).resolve(strict=True)
    if Path(record.get("cwd", "")).resolve(strict=True) != root:
        return False
    # A main checkout's .git directory is NOT a worker worktree.
    gitfile = root / ".git"
    if not gitfile.is_file() or not safe_components(root) or is_reparse(gitfile):
        return False
    branch = git_query(["branch", "--show-current"], root).strip()
    if not branch:
        return False
    listing = git_query(["worktree", "list", "--porcelain"], root)
    if not any("worktree " + str(root).replace("\\", "/") in block.splitlines()
               and "branch refs/heads/" + branch in block.splitlines()
               for block in listing.split("\n\n")):
        return False
    key = "notebook_path" if payload["tool_name"] == "NotebookEdit" else "file_path"
    raw = payload["tool_input"][key]
    # Windows aliases/ADS and metadata are not reversible ordinary file writes.
    parts = raw.replace("\\", "/").split("/")
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        parts = parts[1:]
    if any(part.rstrip(" .") != part or ":" in part for part in parts):
        return False
    target = root / raw
    if not safe_components(target):
        return False
    if target.exists() and target.stat().st_nlink > 1:
        return False  # A hardlink inside can alias an outside file.
    resolved = target.resolve()
    relative = resolved.relative_to(root)
    if not relative.parts or any(part.lower() in {".git", ".liteharness", ".claude"} for part in relative.parts):
        return False
    return True


def inbox_plan(command):
    # Only CLI send has a documented --body-file contract. `lst run inbox` is
    # translated only for its closed set of text fields; JSON payloads refuse.
    if "`" not in command or not re.search(r"\b(?:liteharness\s+send|lst\s+run\s+inbox)\b", command):
        return None
    tokens = literal_tokens(command)
    words = [t[0] for t in tokens]
    if words[:2] == ["liteharness", "send"] and len(words) >= 4:
        if not IDENTITY.fullmatch(words[2]):
            raise ValueError("Invalid inbox recipient")
        flags = {"--from", "--thread-id", "--type", "--project"}
        body = None
        i = 3
        while i < len(words):
            if words[i] in flags:
                i += 2
                if i > len(words):
                    raise ValueError("Missing send flag value")
                if tokens[i - 1][3] == '"' and any(c in words[i - 1] for c in '`$'):
                    raise ValueError("Inbox flag contains shell expansion")
            elif words[i] == "--force":
                i += 1
            elif body is None and tokens[i][3]:
                body = tokens[i]
                i += 1
            else:
                raise ValueError("Unsupported inline inbox command")
        if not body or "`" not in body[0]:
            return None
        return body[0], command[:body[1]], command[body[2]:]
    if words[:3] == ["lst", "run", "inbox"]:
        fields = {}
        for value, _, _, quote in tokens[3:]:
            key, separator, text = value.partition("=")
            if not separator or key in fields or key not in {"action", "to", "from", "from_id", "message", "type"}:
                raise ValueError("Unsupported inbox field")
            if key == "message" and not quote:
                raise ValueError("Inline inbox body must be one quoted literal")
            fields[key] = text
        if fields.get("action") != "send" or not IDENTITY.fullmatch(fields.get("to", "")):
            raise ValueError("Unsupported inbox action or recipient")
        if "from" in fields and "from_id" in fields:
            raise ValueError("Ambiguous inbox sender")
        body = fields.get("message", "")
        if "`" not in body:
            return None
        prefix = "liteharness send '" + fields["to"] + "' "
        suffix = ""
        for field, flag in (("from", "--from"), ("from_id", "--from"), ("type", "--type")):
            if field in fields:
                if not IDENTITY.fullmatch(fields[field]):
                    raise ValueError("Unsupported inbox identity or type")
                suffix += " " + flag + " '" + fields[field] + "'"
        return body, prefix, suffix
    raise ValueError("Unsupported inline inbox syntax; use liteharness send with --body-file")


def save_body(plan):
    body, before, after = plan
    directory = Path.home() / ".liteharness" / "mods" / "inbox-bodies"
    if not safe_components(directory):
        raise ValueError("Inbox body directory contains reparse points")
    directory.mkdir(parents=True, exist_ok=True)
    if not safe_components(directory):
        raise ValueError("Inbox body directory changed during creation")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", suffix=".txt",
                                     dir=directory, delete=False) as stream:
        stream.write(body)
        path = Path(stream.name)
    # Bash's single-quote grammar: filesystem text is not shell interpolation.
    quoted = "'" + str(path).replace("\\", "/").replace("'", "'\"'\"'") + "'"
    return before + "--body-file " + quoted + after


def decide(payload, gate=None, census=runner_pids, ownership=own_write, writer=save_body):
    validate(payload)
    # FIRST policy operation. No process census, git, filesystem scan or write
    # may precede the real floor. Exceptions reach main's failclosed response.
    decision = (gate or load_gate()).decide(payload)
    if decision is not None:
        if not isinstance(decision, dict):
            raise ValueError("Unsupported floor response")
        output = decision.get("hookSpecificOutput")
        if not isinstance(output, dict) or output.get("permissionDecision") != "deny":
            raise ValueError("Unsupported floor decision")
        reason = output.get("permissionDecisionReason")
        if not isinstance(reason, str) or not reason:
            raise ValueError("Missing floor refusal reason")
        return reply(reason)
    commands = [payload["tool_input"][key] for key in ("command", "text", "input", "data")
                if key in payload["tool_input"]]
    for command in commands:
        for base in {payload["cwd"], str(Path(payload["cwd"]) / payload["tool_input"].get("cwd", ""))}:
            reason = junction_refusal(command, base)
            if reason:
                return reply(reason)
        reason = suite_refusal(command, census)
        if reason:
            return reply(reason)
    plan = inbox_plan(payload["tool_input"].get("command", ""))
    if plan and payload["kind"] == "check":
        return reply("Inline backtick inbox body must be rewritten before permission checking.")
    allow = False
    if payload["tool_name"] in FILE_WRITES:
        try:
            allow = ownership(payload)
        except Exception:
            allow = False  # No ownership proof means ordinary permission path.
    return reply(allow=allow, command=writer(plan) if plan else None)


def main():
    try:
        raw = sys.stdin.buffer.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Guard input exceeds bounded budget")
        result = decide(json.loads(raw.decode("utf-8")))
    except Exception:
        # Never include transcript/payload/secrets in diagnostics.
        result = reply("Mods guard unavailable or unsupported payload; refusing without executing the tool.")
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

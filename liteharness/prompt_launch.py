"""Will a LiteTUI home accept the default role a resume derives? Read-only; writes nothing.

A resume with no `--system-prompt-file` carries the resolver's default role (`--role-prompt-file`, a
composed tier preamble). LiteTUI hashes its body and compares it with the role the home recorded in
`.prompt-launch.json`; a home whose `role_source` is `default` is refused ("Launch prompt source
changed for an initialized home") once the tier preamble has changed since the home was initialised.
The refusal only shows on the seat's own terminal, after a PTY exists (T0558-F-E, DaVinci 2026-10-25).

DRIFT RISK: `role_sha` and `role_body` below replicate LiteTUI `src/litetui/seat_prompts.py`
(`role_body`, `ROLE_HEADER`, `MODE_HEADER`, `METHOD_HEADER`, `authored_text`) and
`prompt_overrides.sha`/`read_text`; the comparison replicates `seat_prompts.prepare` lines 157-182.
LiteTUI cannot be imported here (it may be absent). If those change, this check mispredicts; the
failure mode of a wrong "ok" is the old refusal, never a wrong launch. tests/test_prompt_launch.py pins
the real manifest shape.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

_ALIASES = {'owner': 'leader', 'builder': 'worker', 'critic': 'reviewer', 'scout': 'thinker'}
_ROLES = frozenset({'orchestrator', 'leader', 'worker', 'thinker', 'reviewer'})
_ROLE_HEADER = re.compile(r'^﻿*#\s+([A-Z]+) ROLE[^\n]*$', re.MULTILINE)
_MODE_HEADER = re.compile(r'^﻿*#\s+POLYMATHIC[^\n]*\s[—-]\s[^\n]*Mode[^\n]*$', re.MULTILINE | re.IGNORECASE)
_METHOD_HEADER = re.compile(r'^﻿*#\s+POLYMATHIC[^\n]*$', re.MULTILINE)
_MAX_BYTES = 64 * 1024
_SOURCES = ('custom', 'default', 'seed', 'none')


class Undecidable(ValueError):
    """The check cannot decide; the caller names this reason and does not guess."""


@dataclass(frozen=True)
class Verdict:
    action: str  # 'ok' | 'override' | 'stop'
    role_file: str = ''  # override: the home's own role file to pass as --system-prompt-file
    reason: str = ''  # stop: why, with the exact manual command


def _read(path: Path) -> str:
    """prompt_overrides.read_text: bounded, UTF-8, no NUL, universal newlines."""
    if path.stat().st_size > _MAX_BYTES:
        raise Undecidable(f'{path.name} exceeds 64 KiB')
    raw = path.read_bytes()
    if len(raw) > _MAX_BYTES or b'\0' in raw:
        raise Undecidable(f'{path.name} is oversized or contains NUL bytes')
    try:
        return raw.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeError as exc:
        raise Undecidable(f'{path.name} is not UTF-8') from exc


def role_body(text: str, role: str) -> str:
    """seat_prompts.role_body: the role text LiteTUI hashes, for the seat's actual role."""
    if text.startswith('﻿'):
        text = text[1:]
        if text.startswith('﻿'):
            raise Undecidable('multiple leading encoding BOMs')
    headers = list(_ROLE_HEADER.finditer(text))
    if len(headers) > 1 or _MODE_HEADER.search(text):
        raise Undecidable('duplicate or embedded role-mode scaffold')
    if headers:
        declared = _ALIASES.get(headers[0][1].lower(), headers[0][1].lower())
        if declared not in _ROLES or declared != role:
            raise Undecidable(f'role scaffold {declared} disagrees with actual role {role}')
        prefix = text[:headers[0].start()].strip()
        if prefix and not re.fullmatch(r'<!--.*?-->', prefix, re.DOTALL):
            raise Undecidable('unexpected text before the role scaffold')
        text = text[headers[0].end():]
    if _METHOD_HEADER.search(text):
        raise Undecidable('role body contains a polymath method')
    return text.strip()


def role_sha(text: str, role: str) -> str:
    return hashlib.sha256(role_body(text, role).encode('utf-8')).hexdigest()


def read_manifest(home: Path) -> dict | None:
    """The home's `.prompt-launch.json` (version 2) or None when the home has none (never launched)."""
    path = home / '.prompt-launch.json'
    if not path.is_file():
        return None
    try:
        manifest = json.loads(_read(path))
    except ValueError as exc:
        raise Undecidable('the home\'s .prompt-launch.json is unreadable') from exc
    sources = manifest.get('sources') if isinstance(manifest, dict) else None
    if (not isinstance(manifest, dict) or set(manifest) != {'version', 'sources', 'role_source'}
            or type(manifest['version']) is not int or manifest['version'] != 2
            or manifest['role_source'] not in _SOURCES or not isinstance(sources, dict)
            or any(not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}', v) for v in sources.values())):
        raise Undecidable('the home\'s .prompt-launch.json is not a version 2 manifest')
    return manifest


def role_file_arg(args: list) -> str | None:
    """The `--role-prompt-file` the resolver put in a LiteTUI launch, or None."""
    for i, arg in enumerate(args):
        if isinstance(arg, str) and arg == '--role-prompt-file':
            return args[i + 1] if i + 1 < len(args) and isinstance(args[i + 1], str) else None
        if isinstance(arg, str) and arg.startswith('--role-prompt-file='):
            return arg.split('=', 1)[1]
    return None


def manual_command(agent_id: str, cwd: str, tier: str, role_file: str | None) -> str:
    source = role_file or '<the current ' + tier + ' preamble file>'
    return (f'python -m liteharness.cli spawn --resume {agent_id} --split --cwd "{cwd}" --tier {tier} '
            f'--system-prompt-file "{source}"')


def verdict(home: Path, resolved_role_file: str | None, role: str, *, agent_id: str, cwd: str, tier: str) -> Verdict:
    """What a resume of this home needs, given the role file the resolver would hand LiteTUI.

    custom/seed/none homes are never touched. A default home whose recorded role matches the
    resolver's role body resumes as-is. A default home that does not match can be resumed with its OWN
    role (`prompts/role.md`, the role the seat was running); when that file cannot be used the answer is
    a stop that names the seat and the exact manual command.
    """
    try:
        manifest = read_manifest(home)
        if manifest is None or manifest['role_source'] != 'default' or not resolved_role_file:
            return Verdict('ok')
        recorded = manifest['sources'].get('role.md')
        current = role_sha(_read(Path(resolved_role_file)), role)
        if recorded == current:
            return Verdict('ok')
        mismatch = (f'{home.name}: home role_source is default and its recorded role {str(recorded)[:12]} differs '
                    f'from the resolver\'s current {role} role {current[:12]}; LiteTUI would refuse the resume '
                    f'("Launch prompt source changed for an initialized home")')
        live = home / 'prompts' / 'role.md'
        try:
            role_sha(_read(live), role)  # LiteTUI will run the same body check on a --system-prompt-file
        except (OSError, Undecidable) as exc:
            return Verdict('stop', reason=f'{mismatch}, and the home\'s own prompts/role.md cannot be passed instead '
                                          f'({exc}). Resume by hand: ' + manual_command(agent_id, cwd, tier, resolved_role_file))
        return Verdict('override', role_file=str(live), reason=mismatch)
    except (OSError, Undecidable) as exc:
        return Verdict('stop', reason=f'{home.name}: cannot check the home\'s launch prompt source ({exc}). '
                                      'Resume by hand: ' + manual_command(agent_id, cwd, tier, resolved_role_file))

"""Claude native transcript identity, shared by registration and resume.

A presence/index pointer is a locator, not proof. Never create a transcript or
replace a saved-name binding in order to make a seat resumable.
"""
from pathlib import Path
import json
import os

from .agent_store import StoreError, _unlinked, valid_id


def transcript_identity(row: dict, *, native_id: str | None = None,
                        cwd: str | None = None) -> tuple[str, str, Path]:
    root = Path(os.environ.get('CLAUDE_CONFIG_DIR') or (Path.home() / '.claude')) / 'projects'
    _unlinked(root)
    reference = row.get('transcript_path')
    registered_native = row.get('native_session_id')
    if native_id and registered_native and native_id != registered_native:
        raise ValueError('Claude registry native conversation contradicts its saved name')
    native_id = native_id or registered_native
    if reference:
        path = Path(reference)
        if not path.is_absolute() or '..' in path.parts or not path.is_relative_to(root):
            raise ValueError('Claude transcript is outside the native projects root')
        _unlinked(path)
        if native_id and path.stem != native_id:
            raise ValueError('Claude transcript/native conversation identity mismatch')
        native_id = path.stem
    try:
        native_id = valid_id(native_id)
    except StoreError as exc:
        raise ValueError('Claude native conversation identity is missing or invalid') from exc
    candidates = list(root.glob(f'*/{native_id}.jsonl'))
    if len(candidates) != 1:
        raise ValueError('Claude native transcript is missing or ambiguous')
    path = _unlinked(candidates[0])
    if reference and path != Path(reference):
        raise ValueError('Claude transcript reference does not match native conversation')
    expected_cwd = cwd or row.get('cwd')
    if cwd and row.get('cwd') and Path(cwd).resolve() != Path(row['cwd']).resolve():
        raise ValueError('Claude registry cwd contradicts its saved name')
    found_cwd = None
    observed = False
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError('Malformed Claude transcript record')
                sid = event.get('sessionId')
                if sid is not None:
                    if sid != native_id:
                        raise ValueError('Contradictory Claude native session identity')
                    observed = True
                directory = event.get('cwd')
                if directory is not None:
                    if not isinstance(directory, str) or not Path(directory).is_absolute():
                        raise ValueError('Invalid Claude transcript cwd')
                    if found_cwd is not None and Path(directory).resolve() != Path(found_cwd).resolve():
                        raise ValueError('Contradictory Claude transcript cwd')
                    found_cwd = directory
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Claude transcript is unreadable or malformed') from exc
    if not observed or not found_cwd:
        raise ValueError('Claude transcript lacks native session/cwd evidence')
    if expected_cwd and Path(expected_cwd).resolve() != Path(found_cwd).resolve():
        raise ValueError('Claude original cwd disagrees with native transcript')
    if not Path(found_cwd).is_dir():
        raise ValueError('Claude original cwd no longer exists')
    return native_id, expected_cwd or found_cwd, path


def record_registered_name(presence: dict) -> None:
    """After actual registration, use the existing atomic collision-protected writer."""
    if presence.get('cli') not in ('claude-code', 'claude'):
        return
    from . import agent_names
    if str(presence.get('backend') or 'claude').split('/', 1)[0].lower() != 'claude':
        raise ValueError('Claude registration backend contradicts its CLI identity')
    if not presence.get('name') or not presence.get('agent_id'):
        raise ValueError('Claude registration lacks its actual resolved name/agent identity')
    # This also runs in a SPAWNER: its ambient native session is not the child.
    native_id, cwd, _ = transcript_identity(presence)
    agent_names.record_name(presence['name'], presence['agent_id'], native_id, cwd,
                            backend='claude', model=presence.get('model'))

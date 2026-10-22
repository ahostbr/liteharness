"""Claude native transcript identity, shared by registration and resume.

A presence/index pointer is a locator, not proof. Never create a transcript or
replace a saved-name binding in order to make a seat resumable.
"""
from pathlib import Path
import json
import os

from .agent_store import StoreError, _unlinked, valid_id


class ClaudeTranscriptPending(ValueError):
    """Exact native transcript evidence has not materialized yet; not a contradiction."""


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
    if not candidates:
        raise ClaudeTranscriptPending('Claude native transcript is missing')
    if len(candidates) != 1:
        raise ValueError('Claude native transcript is ambiguous')
    path = _unlinked(candidates[0])
    if reference and path != Path(reference):
        raise ValueError('Claude transcript reference does not match native conversation')
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
                    if found_cwd is None:
                        found_cwd = directory
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Claude transcript is unreadable or malformed') from exc
    if found_cwd:
        if cwd and Path(cwd).resolve() != Path(found_cwd).resolve():
            raise ValueError('Claude original cwd disagrees with native transcript')
        if not Path(found_cwd).is_dir():
            raise ValueError('Claude original cwd no longer exists')
    if not observed or not found_cwd:
        raise ClaudeTranscriptPending('Claude transcript lacks native session/cwd evidence')
    return native_id, found_cwd, path


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


def real_model(value) -> str | None:
    """A registry/index model that names a model: None for missing, empty or the literal 'unknown'."""
    if not isinstance(value, str) or not value.strip() or value.strip().lower() == 'unknown':
        return None
    return value.strip()


def transcript_model(path: Path) -> str | None:
    """The newest assistant record's message.model, skipping '<synthetic>'.

    Base model only: the transcript carries no context-window suffix and nothing here guesses one.
    """
    found = None
    try:
        with Path(path).open(encoding='utf-8') as stream:
            for line in stream:
                if '"assistant"' not in line or '"model"' not in line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get('type') != 'assistant':
                    continue
                model = (event.get('message') or {}).get('model')
                if isinstance(model, str) and model and model != '<synthetic>':
                    found = model
    except (OSError, UnicodeError):
        return None
    return found

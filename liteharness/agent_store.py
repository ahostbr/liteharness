"""Agent-owned storage contract, schema 1 (T0308), stdlib only.

CANONICAL: liteharness-oss/liteharness/agent_store.py. LiteTUI ships a
byte-identical src/litetui/agent_store.py mirror; both repositories test parity.
No runtime cross-checkout import, registry lookup, writes, or directory creation.
The caller supplies the durable data root, never a cwd or registry guess.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
import unicodedata
from uuid import UUID

SCHEMA_VERSION = 1
AGENTS_DIR = ".agents"
LEGACY_DIR = ".convos"
CONVERSATIONS_DIR = "conversations"
SETTINGS_NAME = "settings.json"
INITIALIZING_NAME = ".agent.initializing"
TRANSCRIPT_NAME = "convo.jsonl"
MEMORY_FILES = frozenset({"memory.md", "soul.md", "handoff.md"})
MEMORIES_DIR = "memories"
_RESERVED = re.compile(r"(?i)^(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\.|$)")


class StoreError(ValueError):
    """Invalid, ambiguous or incompatible persistent identity; never guess."""


def valid_name(value: object) -> str:
    """One portable Windows-safe folder component, preserving its exact spelling."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or value.endswith((".", " ")) or value in (".", "..")
            or any(c in '<>:"/\\|?*' or unicodedata.category(c).startswith("C") for c in value)
            or len(value.encode("utf-16-le")) > 510
            or unicodedata.normalize("NFC", value) != value
            or _RESERVED.match(value)):
        raise StoreError("Invalid agent folder name")
    return value


def name_key(value: str) -> str:
    return unicodedata.normalize("NFC", valid_name(value)).casefold()


def valid_id(value: object) -> str:
    """Canonical UUID component; no prefix matching or path interpretation."""
    if not isinstance(value, str):
        raise StoreError("Invalid storage identity")
    try:
        canonical = str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise StoreError("Invalid storage identity") from exc
    if canonical != value:
        raise StoreError("Storage identity must be a canonical UUID")
    return value


def _unlinked(path: Path) -> Path:
    """Reject existing links/reparse points including ancestors; absent is okay.

    Location validation, not a TOCTOU sandbox. Writers must hold ownership and
    revalidate paths; reads never create lock files or repair missing metadata.
    """
    try:
        for part in (path, *path.parents):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise StoreError("Linked storage paths are not supported")
    except OSError as exc:
        raise StoreError("Storage path cannot be inspected") from exc
    return path


@dataclass(frozen=True)
class Agent:
    name: str
    agent_id: str
    directory: Path
    settings: dict

    @property
    def memory_root(self) -> Path:
        return self.directory

    @property
    def conversations_root(self) -> Path:
        return self.directory / CONVERSATIONS_DIR


def read_agent(directory: Path) -> Agent:
    return _read_agent_metadata(directory)


def _read_agent_metadata(directory: Path, *, inactive_catalog: bool = False) -> Agent:
    directory = _unlinked(Path(directory))
    name = valid_name(directory.name)
    marker = _unlinked(directory / INITIALIZING_NAME)
    try:
        marker.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise StoreError("Agent initialization cannot be inspected") from exc
    else:
        if not inactive_catalog:
            raise StoreError("Agent initialization is incomplete")
    settings_path = _unlinked(directory / SETTINGS_NAME)
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StoreError("Agent settings are absent or unreadable") from exc
    if not isinstance(settings, dict) or type(settings.get("schema_version")) is not int:
        raise StoreError("Agent settings require a schema version")
    if settings["schema_version"] != SCHEMA_VERSION:
        raise StoreError("Unsupported agent settings schema")
    if settings.get("name") != name:
        raise StoreError("Agent settings name disagrees with its folder")
    identity = valid_id(settings.get("agent_id"))
    execution = settings.get("execution")
    if not isinstance(execution, dict) or any(
            not isinstance(execution.get(key), str) or not execution[key].strip()
            for key in ("backend", "model", "thinking_level")):
        raise StoreError("Agent execution authority is incomplete")
    return Agent(name, identity, directory, settings)


class AgentStore:
    """Read-only path resolver; activation/migration policy belongs to callers."""

    def __init__(self, data_root: Path | str):
        root = Path(data_root)
        if not root.is_absolute() or ".." in root.parts:
            raise StoreError("Data root must be an absolute path without traversal")
        self.data_root = _unlinked(root)
        self.root = self.data_root / AGENTS_DIR
        self.legacy_root = self.data_root / LEGACY_DIR

    def agent_directory(self, name: str) -> Path:
        return _unlinked(self.root / valid_name(name))

    def list_agents(self) -> list[Agent]:
        root = _unlinked(self.root)
        if not root.exists():
            return []
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name.casefold())
        except OSError as exc:
            raise StoreError("Agent catalog cannot be inspected") from exc
        agents: list[Agent] = []
        names: set[str] = set()
        identities: set[str] = set()
        for directory in entries:
            _unlinked(directory)
            if not directory.is_dir():
                continue
            # Inactive copies reserve their identity/name even though they
            # cannot be launched. Parse the SAME strict metadata contract and
            # check collisions before excluding them from the ready catalog;
            # malformed inactive metadata still blocks, never silently skipped.
            inactive = _unlinked(directory / INITIALIZING_NAME).exists()
            agent = _read_agent_metadata(directory, inactive_catalog=inactive)
            key = name_key(agent.name)
            if key in names or agent.agent_id in identities:
                raise StoreError("Agent name or identity is ambiguous")
            names.add(key)
            identities.add(agent.agent_id)
            if not inactive:
                agents.append(agent)
        return agents

    def find_agent(self, *, name: str | None = None, agent_id: str | None = None) -> Agent:
        if (name is None) == (agent_id is None):
            raise StoreError("Select exactly one agent name or identity")
        key = name_key(name) if name is not None else None
        identity = valid_id(agent_id) if agent_id is not None else None
        matches = [agent for agent in self.list_agents()
                   if name_key(agent.name) == key or agent.agent_id == identity]
        if len(matches) != 1:
            raise StoreError("Agent is absent or ambiguous")
        return matches[0]

    def conversation_directory(self, agent: Agent, conversation_id: str) -> Path:
        # Do not trust an Agent object supplied by an unvalidated caller.
        canonical = self.find_agent(agent_id=agent.agent_id)
        if canonical.directory != agent.directory or canonical.name != agent.name:
            raise StoreError("Agent location disagrees with the catalog")
        return _unlinked(canonical.conversations_root / valid_id(conversation_id))

    def list_conversations(self, agent: Agent) -> list[Path]:
        canonical = self.find_agent(agent_id=agent.agent_id)
        root = _unlinked(canonical.conversations_root)
        if not root.exists():
            return []
        try:
            directories = sorted(root.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            raise StoreError("Conversation catalog cannot be inspected") from exc
        result = []
        for directory in directories:
            _unlinked(directory)
            if directory.is_dir():
                valid_id(directory.name)
                result.append(directory)
        return result

    def locate_conversation(self, conversation_id: str) -> Path:
        identity = valid_id(conversation_id)
        matches = [directory for agent in self.list_agents()
                   for directory in self.list_conversations(agent) if directory.name == identity]
        if len(matches) != 1:
            raise StoreError("Conversation is absent or has ambiguous ownership")
        return matches[0]

    def legacy_conversation(self, conversation_id: str) -> Path:
        """Read-only backup location; callers must never use it as a new writer."""
        return _unlinked(self.legacy_root / valid_id(conversation_id))

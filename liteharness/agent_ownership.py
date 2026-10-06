"""Opt-in child ownership foundations for agent stores (T0308).

CANONICAL: liteharness-oss/liteharness/agent_ownership.py, byte-mirrored in
LiteTUI. Importing this module never activates a store or registers presence.
The launched CHILD acquires ownership before registration, ready, or writes;
parent preflight is not ownership. Keep the session for the whole process,
including /new and compaction. Conversation leases remain a separate concern.

The kernel byte/flock algorithm matches LiteTUI shared_state.Lease. Unlike its
legacy convenience API, acquisition never creates parent directories and every
uninspectable/inaccessible lock fails closed. No PID, age, timeout or takeover.
Path checks are not a sandbox against concurrent external filesystem changes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import BinaryIO, Callable, TypeVar
from uuid import uuid4

from .agent_store import (
    AGENT_SEED_FILES, CONVERSATIONS_DIR, MEMORIES_DIR,
    Agent, AgentStore, INITIALIZING_NAME, SCHEMA_VERSION, SETTINGS_NAME, StoreError, _unlinked,
    name_key, valid_id, valid_name,
)

AGENT_LEASE_NAME = ".agent.lease"
CATALOG_LEASE_NAME = ".agents.catalog.lease"
T = TypeVar("T")


class OwnershipError(OSError):
    """Owned or inaccessible; neither permits registration or mutation."""


class _KernelLease:
    def __init__(self, path: Path):
        self.path = path
        self.handle: BinaryIO | None = None
        self.pid = os.getpid()

    def acquire(self) -> None:
        if self.pid != os.getpid():
            raise OwnershipError("Agent ownership cannot be inherited by a child")
        if self.handle is not None:
            return
        handle = None
        try:
            _unlinked(self.path)
            # No mkdir, no truncation, no replacing the persistent lock inode.
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self.path, flags, 0o600)
            try:
                os.set_inheritable(fd, False)
                handle = os.fdopen(fd, "r+b")
            except BaseException:
                os.close(fd)
                raise
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock first: even initialization belongs to the kernel owner.
            if not os.fstat(handle.fileno()).st_size:
                handle.write(b"\0")
                handle.flush()
        except (OSError, StoreError) as exc:
            if handle is not None:
                handle.close()
            raise OwnershipError(f"Resource owned or inaccessible: {self.path.name}") from exc
        except BaseException:
            if handle is not None:
                handle.close()
            raise
        self.handle = handle

    def release(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


@dataclass(frozen=True)
class AgentAuthority:
    """Only settled folder-authoritative launch fields, not preferences/history."""
    name: str
    agent_id: str
    backend: str
    model: str | None
    thinking_level: str

    @classmethod
    def from_agent(cls, agent: Agent) -> AgentAuthority:
        execution = agent.settings["execution"]
        return cls(agent.name, agent.agent_id, execution["backend"],
                   execution["model"], execution["thinking_level"])


class AgentSession:
    """A child-owned capability; construct with acquire_existing/create_fresh.

    No registry or names index is read. register_presence invokes the official
    caller's registration adapter with the exact folder identity/execution only
    AFTER ownership. The adapter must fail rather than rename/rebind an agent.
    This module does not claim successful transport registration means ready.
    """
    def __init__(self, store: AgentStore, agent: Agent, lease: _KernelLease):
        self.store = store
        self._agent = agent
        self._lease = lease
        self._authority = AgentAuthority.from_agent(agent)
        self.initial_conversation_id: str | None = None

    @classmethod
    def acquire_existing(cls, store: AgentStore, *, name: str | None = None,
                         agent_id: str | None = None) -> AgentSession:
        agent = store.find_agent(name=name, agent_id=agent_id)
        lease = _KernelLease(agent.directory / AGENT_LEASE_NAME)
        lease.acquire()
        try:
            # Re-resolve after the lock; caller objects/catalog pointers are not truth.
            current = store.find_agent(agent_id=agent.agent_id)
            if (current.directory != agent.directory or current.name != agent.name
                    or current.settings != agent.settings):
                raise StoreError("Agent authority changed during acquisition")
            return cls(store, current, lease)
        except BaseException:
            lease.release()
            raise

    @classmethod
    def create_fresh(cls, store: AgentStore, *, name: str, agent_id: str,
                     backend: str, model: str | None, thinking_level: str,
                     allow_unchosen: bool = False) -> AgentSession:
        """Reserve one caller-generated name/UUID, never overwrite or rename.

        Callers use liteharness.naming.generate_name; collisions are explicit,
        not suffix guesses. Catalog serialization also covers casefold names
        on case-sensitive hosts. Failures after mkdir leave diagnostic evidence,
        never delete a possibly published folder or guess how to repair it.
        The data root must already exist. Migration uses its own gated protocol.
        """
        name, agent_id = valid_name(name), valid_id(agent_id)
        execution = {"backend": backend, "model": model, "thinking_level": thinking_level}
        if allow_unchosen and model is None:
            execution['model_selection'] = 'unchosen'
        if (any(not isinstance(execution[k], str) or not execution[k].strip()
                for k in ('backend', 'thinking_level'))
                or (not (allow_unchosen and model is None)
                    and (not isinstance(model, str) or not model.strip()))):
            raise StoreError('Agent execution authority is incomplete')
        with _KernelLease(store.data_root / CATALOG_LEASE_NAME):
            agents = store.list_agents()
            if any(name_key(a.name) == name_key(name) or a.agent_id == agent_id for a in agents):
                raise StoreError("Agent name or identity already exists")
            _unlinked(store.root).mkdir(exist_ok=True)
            directory = store.agent_directory(name)
            directory.mkdir()  # atomic no-overwrite reservation
            lease = _KernelLease(directory / AGENT_LEASE_NAME)
            lease.acquire()
            try:
                marker = _unlinked(directory / INITIALIZING_NAME)
                with marker.open("x", encoding="utf-8") as handle:
                    handle.write("Initialization incomplete; do not activate.\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                settings = {"schema_version": SCHEMA_VERSION, "name": name,
                            "agent_id": agent_id, "execution": execution}
                # Resolver rejects the marker until initialization is complete.
                # Failed publication retains diagnostics without guessed recovery.
                initial = _unlinked(directory / ".settings.initializing.json")
                with initial.open("x", encoding="utf-8") as handle:
                    json.dump(settings, handle, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                # Seed only this reserved home, before removing the blocking marker.
                # Interrupted initialization is never activated or repaired on read.
                _unlinked(directory / MEMORIES_DIR).mkdir()
                conversation_id = str(uuid4())
                conversations = _unlinked(directory / CONVERSATIONS_DIR)
                conversations.mkdir()
                _unlinked(conversations / conversation_id).mkdir()
                for filename, seed in AGENT_SEED_FILES.items():
                    with _unlinked(directory / filename).open("x", encoding="utf-8") as handle:
                        handle.write(seed)
                        handle.flush()
                        os.fsync(handle.fileno())
                session = cls(store, Agent(name, agent_id, directory, settings), lease)
                session.initial_conversation_id = conversation_id
                published = _unlinked(directory / SETTINGS_NAME)
                os.link(initial, published)  # fails if any destination already exists
                if not initial.samefile(published):
                    raise StoreError("Agent initializer publication changed")
                # Only this newly created disposable alias is removed, never an
                # original/agent folder. Cleanup failure retains the BLOCKING marker.
                initial.unlink()
                marker.unlink()  # LAST: a single settings name now owns authority
                return session
            except BaseException:
                lease.release()
                raise

    def _require_owned(self) -> None:
        if self._lease.handle is None or self._lease.pid != os.getpid():
            raise OwnershipError("Agent session is not owned by this process")
        current = self.store.find_agent(agent_id=self._authority.agent_id)
        if (current.directory != self._agent.directory
                or AgentAuthority.from_agent(current) != self._authority):
            raise StoreError("Owned agent authority changed; reopen explicitly")

    @property
    def authority(self) -> AgentAuthority:
        self._require_owned()
        return self._authority

    @property
    def memory_root(self) -> Path:
        self._require_owned()
        return self._agent.memory_root

    def conversation_directory(self, conversation_id: str) -> Path:
        self._require_owned()
        return self.store.conversation_directory(self._agent, conversation_id)

    def update_execution(self, *, backend: str, model: str, thinking_level: str) -> AgentAuthority:
        """Publish execution under this process lease; retain failed temp evidence.

        Atomic replace leaves old authority intact on prepublication failure. The
        in-memory capability refreshes only after exact readback. No registry or
        conversation snapshot becomes a second execution authority.
        """
        from copy import deepcopy
        self._require_owned()
        execution = {'backend': backend, 'model': model, 'thinking_level': thinking_level}
        if any(not isinstance(v, str) or not v.strip() for v in execution.values()):
            raise StoreError('Agent execution authority is incomplete')
        before = self.store.find_agent(agent_id=self._authority.agent_id)
        settings = deepcopy(before.settings)
        settings['execution'].update(execution)
        settings['execution']['model_selection'] = 'chosen'
        target = _unlinked(self._agent.directory / SETTINGS_NAME)
        temporary = _unlinked(self._agent.directory / ('.settings.update.' + str(uuid4()) + '.json'))
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(settings, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        self._require_owned()
        if self.store.find_agent(agent_id=self._authority.agent_id).settings != before.settings:
            raise StoreError('Agent settings changed before execution publication')
        os.replace(temporary, target)
        current = self.store.find_agent(agent_id=self._authority.agent_id)
        if current.settings != settings or current.directory != self._agent.directory:
            raise StoreError('Agent execution publication changed; reopen explicitly')
        self._agent = current
        self._authority = AgentAuthority.from_agent(current)
        return self.authority

    def register_presence(self, register: Callable[[AgentAuthority], T]) -> T:
        self._require_owned()
        result = register(self._authority)
        self._require_owned()  # transport success cannot mask lost/changed authority
        return result

    def release(self) -> None:
        self._lease.release()

    def __enter__(self) -> AgentSession:
        self._require_owned()
        return self

    def __exit__(self, *_):
        self.release()

    def __del__(self):
        self.release()

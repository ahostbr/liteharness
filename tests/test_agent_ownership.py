"""T0308 child ownership: temporary fixtures only, no runtime activation."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from liteharness import agent_ownership as runtime, agent_store as store

AID = '11111111-1111-4111-8111-111111111111'
BID = '22222222-2222-4222-8222-222222222222'
CID = '33333333-3333-4333-8333-333333333333'
DID = '44444444-4444-4444-8444-444444444444'
PACKAGE = runtime.__package__


def agent(root, name='QuietHelm', identity=AID):
    directory = root / '.agents' / name
    directory.mkdir(parents=True)
    (directory / 'settings.json').write_text(json.dumps({
        'schema_version': 1, 'name': name, 'agent_id': identity,
        'execution': {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'},
        'tool_policy_profile': 'not-inherited',
    }), encoding='utf-8')
    return directory


def child_code(root, identity=AID, *, fresh=None):
    if fresh is None:
        acquire = f"AgentSession.acquire_existing(AgentStore({str(root)!r}), agent_id={identity!r})"
    else:
        acquire = (f"AgentSession.create_fresh(AgentStore({str(root)!r}), name={fresh!r}, "
                   f"agent_id={identity!r}, backend='codex', model='fixture', thinking_level='high')")
    # The stdlib child runs from a temporary data root under the real interpreter,
    # not pytest's import context. Bind its source to the module actually tested.
    package_root = str(Path(runtime.__file__).resolve().parents[1])
    return ("import sys\n"
            f"sys.path.insert(0, {package_root!r})\n"
            f"from {PACKAGE}.agent_ownership import AgentSession\n"
            f"from {PACKAGE}.agent_store import AgentStore\n"
            "import sys, os\n"
            "sys.stdin.readline()\n"
            "try:\n"
            f"    session = {acquire}\n"
            "except Exception as exc:\n"
            "    print(type(exc).__name__, flush=True)\n"
            "    sys.exit(3)\n"
            "print('OWNED ' + str(os.getpid()), flush=True)\n"
            "sys.stdin.readline()\n"
            "session.release()\n")


def launch(root, identity=AID, *, fresh=None):
    # Windows venv redirectors can outlive their Popen wrapper; launch the
    # already-installed real interpreter for these stdlib-only fixture children.
    executable = sys._base_executable if sys.platform == 'win32' else sys.executable
    return subprocess.Popen([executable, '-u', '-c', child_code(root, identity, fresh=fresh)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=root)


def finish(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=15)


def outcome(process):
    # communicate has a timeout; never hang a slot on a child readiness error.
    output, errors = process.communicate('start\nstop\n', timeout=15)
    output = output.strip()
    if output.startswith('OWNED '):
        assert int(output.split()[1]) == process.pid
        output = 'OWNED'
    return process.returncode, output, errors


def test_absent_agent_acquire_never_materializes_root(tmp_path):
    root = tmp_path / 'missing'
    with pytest.raises(store.StoreError):
        runtime.AgentSession.acquire_existing(store.AgentStore(root), agent_id=AID)
    assert not root.exists()
    assert list(tmp_path.iterdir()) == []


def test_same_agent_different_conversations_duplicate_refused(tmp_path):
    directory = agent(tmp_path)
    resolver = store.AgentStore(tmp_path)
    with runtime.AgentSession.acquire_existing(resolver, agent_id=AID) as owned:
        assert owned.conversation_directory(CID) == directory / 'conversations' / CID
        assert owned.conversation_directory(DID) == directory / 'conversations' / DID
        for cid in (CID, DID):
            assert not owned.conversation_directory(cid).exists()
            child = launch(tmp_path)
            try:
                rc, output, errors = outcome(child)
                assert (rc, output, errors) == (3, 'OwnershipError', '')
            finally:
                finish(child)
        with pytest.raises(runtime.OwnershipError):
            runtime.AgentSession.acquire_existing(resolver, name='QuietHelm')
    assert directory.joinpath(runtime.AGENT_LEASE_NAME).exists()
    with runtime.AgentSession.acquire_existing(resolver, agent_id=AID):
        pass  # Persistent lock file is not ownership.


def test_independent_agents_run_in_parallel(tmp_path):
    agent(tmp_path)
    agent(tmp_path, 'KindGrid', BID)
    with runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID):
        child = launch(tmp_path, BID)
        try:
            assert outcome(child) == (0, 'OWNED', '')
        finally:
            finish(child)


def test_crash_releases_kernel_ownership_without_deleting_lock(tmp_path):
    directory = agent(tmp_path)
    child = launch(tmp_path)
    try:
        child.stdin.write('start\n')
        child.stdin.flush()
        # A bounded probe proves the child obtained the real kernel lock.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(child.stdout.readline)
            try:
                ready = future.result(timeout=15).strip()
                assert ready == f'OWNED {child.pid}'
            except BaseException:
                child.kill()
                raise
        with pytest.raises(runtime.OwnershipError):
            runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
        lock = directory / runtime.AGENT_LEASE_NAME
        before_inode = lock.stat().st_ino
        child.kill()
        child.wait(timeout=15)
        assert child.poll() is not None and child.returncode != 0
        with runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID) as session:
            assert lock.stat().st_ino == before_inode
            # Windows byte locks reject reads through a separate file handle.
            session._lease.handle.seek(0)
            assert session._lease.handle.read() == b'\0'
    finally:
        finish(child)


def test_folder_execution_only_and_same_id_registration_after_ownership(tmp_path):
    directory = agent(tmp_path)
    # Poisoned/missing registry and derived catalog must have no launch authority.
    (tmp_path / 'names.json').write_text(json.dumps({'QuietHelm': {'backend': 'claude', 'agent_id': BID}}))
    (tmp_path / 'registry.json').write_text('{invalid')
    calls = []
    session = runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), name='quiethelm')
    try:
        authority = session.authority
        assert authority == runtime.AgentAuthority('QuietHelm', AID, 'codex', 'fixture', 'high')
        assert not hasattr(authority, 'tool_policy_profile')
        assert session.memory_root == directory
        def official_registration(value):
            with pytest.raises(runtime.OwnershipError):
                runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
            calls.append(value)
            return 'registered'
        assert session.register_presence(official_registration) == 'registered'
        assert calls == [authority]
    finally:
        session.release()
    with pytest.raises(runtime.OwnershipError):
        session.register_presence(lambda value: calls.append(value))
    with pytest.raises(runtime.OwnershipError):
        _ = session.memory_root
    assert calls == [authority]


@pytest.mark.parametrize('failure', ['uninspectable', 'permission', 'directory'])
def test_inaccessible_lock_fails_closed(tmp_path, monkeypatch, failure):
    directory = agent(tmp_path)
    lock = directory / runtime.AGENT_LEASE_NAME
    if failure == 'directory':
        lock.mkdir()
    elif failure == 'permission':
        original = runtime.os.open
        def denied(path, *args, **kwargs):
            if Path(path) == lock:
                raise PermissionError('fixture inaccessible')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(runtime.os, 'open', denied)
    else:
        original = Path.lstat
        def denied(path, *args, **kwargs):
            if path == lock:
                raise PermissionError('fixture uninspectable')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', denied)
    with pytest.raises(runtime.OwnershipError):
        runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
    assert not (directory / 'conversations').exists()


@pytest.mark.parametrize('error', [OSError, RuntimeError])
def test_failed_lock_initialization_closes_real_handle(tmp_path, monkeypatch, error):
    directory = agent(tmp_path)
    handles = []
    original = runtime.os.fdopen
    class BrokenWriter:
        def __init__(self, handle):
            self.handle = handle
        def fileno(self):
            return self.handle.fileno()
        def write(self, _):
            raise error('fixture initialization failed')
        def close(self):
            self.handle.close()
    def broken(fd, *args, **kwargs):
        handle = original(fd, *args, **kwargs)
        handles.append(handle)
        return BrokenWriter(handle)
    with monkeypatch.context() as patch:
        patch.setattr(runtime.os, 'fdopen', broken)
        with pytest.raises(runtime.OwnershipError if error is OSError else RuntimeError):
            runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
    assert len(handles) == 1 and handles[0].closed
    assert (directory / runtime.AGENT_LEASE_NAME).read_bytes() == b''
    with runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID):
        pass


def test_changed_authority_during_acquisition_releases_lock(tmp_path, monkeypatch):
    directory = agent(tmp_path)
    original = runtime._KernelLease.acquire
    def changed(lease):
        original(lease)
        path = directory / 'settings.json'
        settings = json.loads(path.read_text())
        settings['execution']['backend'] = 'claude'
        path.write_text(json.dumps(settings))
    with monkeypatch.context() as patch:
        patch.setattr(runtime._KernelLease, 'acquire', changed)
        with pytest.raises(store.StoreError, match='changed'):
            runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
    with runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID) as session:
        assert session.authority.backend == 'claude'


def test_pid_reuse_or_inheritance_never_grants_ownership(tmp_path, monkeypatch):
    agent(tmp_path)
    with runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID) as session:
        with monkeypatch.context() as patch:
            patch.setattr(runtime.os, 'getpid', lambda: session._lease.pid + 1)
            with pytest.raises(runtime.OwnershipError):
                session.register_presence(lambda _: pytest.fail('must not register'))
        with pytest.raises(runtime.OwnershipError):
            runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)


def test_fresh_reservation_is_owned_before_authority_write(tmp_path, monkeypatch):
    resolver = store.AgentStore(tmp_path)
    original = runtime.json.dump
    def inspect(settings, handle, **kwargs):
        lock = runtime._KernelLease(resolver.agent_directory('QuietHelm') / runtime.AGENT_LEASE_NAME)
        with pytest.raises(runtime.OwnershipError):
            lock.acquire()
        return original(settings, handle, **kwargs)
    monkeypatch.setattr(runtime.json, 'dump', inspect)
    with runtime.AgentSession.create_fresh(resolver, name='QuietHelm', agent_id=AID,
                                          backend='codex', model='fixture', thinking_level='high') as session:
        assert resolver.find_agent(agent_id=AID).agent_id == session.authority.agent_id
        assert session.memory_root.name == 'QuietHelm'
        assert not (session.memory_root / '.settings.initializing.json').exists()
        assert not (session.memory_root / store.INITIALIZING_NAME).exists()
        assert (session.memory_root / 'settings.json').stat().st_nlink == 1
        with pytest.raises(store.StoreError, match='already exists'):
            runtime.AgentSession.create_fresh(resolver, name='quiethelm', agent_id=BID,
                                             backend='codex', model='fixture', thinking_level='high')


@pytest.mark.parametrize('failure', ['dump', 'fsync', 'publish'])
def test_partial_fresh_initialization_keeps_evidence_but_no_valid_authority(tmp_path, monkeypatch, failure):
    resolver = store.AgentStore(tmp_path)
    def failed(*_, **__):
        raise OSError('fixture interrupted initialization')
    with monkeypatch.context() as patch:
        if failure == 'dump':
            patch.setattr(runtime.json, 'dump', failed)
        else:
            if failure == 'fsync':
                original = runtime.os.fsync
                calls = 0
                def init_fsync(fd):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        failed()
                    return original(fd)
                patch.setattr(runtime.os, 'fsync', init_fsync)
            else:
                patch.setattr(runtime.os, 'link', failed)
        with pytest.raises(OSError):
            runtime.AgentSession.create_fresh(resolver, name='QuietHelm', agent_id=AID,
                                             backend='codex', model='fixture', thinking_level='high')
    directory = resolver.agent_directory('QuietHelm')
    assert directory.exists()
    assert (directory / '.settings.initializing.json').exists()
    assert not (directory / 'settings.json').exists()
    with pytest.raises(store.StoreError):
        resolver.find_agent(agent_id=AID)
    with runtime._KernelLease(directory / runtime.AGENT_LEASE_NAME):
        pass
    with runtime._KernelLease(tmp_path / runtime.CATALOG_LEASE_NAME):
        pass


@pytest.mark.parametrize('name2,id2', [('quiethelm', BID), ('KindGrid', AID)])
def test_concurrent_creation_never_publishes_duplicate_name_or_identity(tmp_path, name2, id2):
    # Both processes start at the same stdin barrier. Holding the winner's agent
    # lease is independent of catalog serialization and does not block lookup.
    children = [launch(tmp_path, AID, fresh='QuietHelm'), launch(tmp_path, id2, fresh=name2)]
    try:
        for process in children:
            process.stdin.write('start\nstop\n')
            process.stdin.flush()
        results = [process.communicate(timeout=15) for process in children]
        assert sorted(process.returncode for process in children) == [0, 3]
        for process, (output, _) in zip(children, results):
            if output.startswith('OWNED '):
                assert int(output.split()[1]) == process.pid
        assert sorted('OWNED' if output.startswith('OWNED ') else output.strip()
                      for output, _ in results) in (
            ['OWNED', 'OwnershipError'], ['OWNED', 'StoreError'])
        assert all(not errors for _, errors in results)
        assert len(store.AgentStore(tmp_path).list_agents()) == 1
    finally:
        for process in children:
            finish(process)


def test_linked_lock_and_reparse_ancestor_rejected(tmp_path, monkeypatch):
    directory = agent(tmp_path)
    lock = directory / runtime.AGENT_LEASE_NAME
    real = tmp_path / 'lock'
    real.write_bytes(b'untouched')
    try:
        lock.symlink_to(real)
    except OSError:
        pytest.skip('host cannot create symlinks')
    with pytest.raises(runtime.OwnershipError):
        runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)
    assert real.read_bytes() == b'untouched'
    original = Path.lstat
    def reparse(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == directory:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', reparse)
    with pytest.raises(store.StoreError, match='Linked'):
        runtime.AgentSession.acquire_existing(store.AgentStore(tmp_path), agent_id=AID)


def test_stdlib_contract_and_canonical_mirror_parity():
    source = Path(runtime.__file__).read_bytes()
    tree = ast.parse(source)
    modules = {node.module.split('.')[0] for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and node.module}
    modules.update(alias.name.split('.')[0] for node in ast.walk(tree)
                   if isinstance(node, ast.Import) for alias in node.names)
    assert modules <= {'__future__', 'dataclasses', 'json', 'os', 'pathlib', 'sys',
                       'typing', 'msvcrt', 'fcntl', 'agent_store'}
    counterpart = os.environ.get('T0308_COUNTERPART_AGENT_OWNERSHIP')
    if not counterpart:
        pytest.skip('paired checkout not provided; package contract tested above')
    assert Path(counterpart).read_bytes() == source

@pytest.mark.parametrize('boundary', ['mkdir', 'lease', 'marker', 'marker_fsync', 'init',
                                    'link_after', 'alias_unlink', 'marker_unlink'])
def test_interruption_at_every_publication_boundary_blocks_authority(tmp_path, monkeypatch, boundary):
    resolver = store.AgentStore(tmp_path)
    directory = resolver.agent_directory('QuietHelm')
    original_open, original_unlink = Path.open, Path.unlink
    original_mkdir, original_fsync = Path.mkdir, runtime.os.fsync
    original_link, original_acquire = runtime.os.link, runtime._KernelLease.acquire
    def fail():
        raise OSError('fixture interrupted at ' + boundary)
    def mkdir(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == directory and boundary == 'mkdir':
            fail()
        return result
    def acquire(lease):
        if lease.path == directory / runtime.AGENT_LEASE_NAME and boundary == 'lease':
            fail()
        return original_acquire(lease)
    def opened(path, *args, **kwargs):
        if boundary == 'marker' and path == directory / store.INITIALIZING_NAME:
            fail()
        if boundary == 'init' and path == directory / '.settings.initializing.json':
            fail()
        return original_open(path, *args, **kwargs)
    def fsync(fd):
        if boundary == 'marker_fsync':
            fail()
        return original_fsync(fd)
    def link(src, dst, *args, **kwargs):
        result = original_link(src, dst, *args, **kwargs)
        if boundary == 'link_after':
            fail()
        return result
    def unlink(path, *args, **kwargs):
        if ((boundary == 'alias_unlink' and path.name == '.settings.initializing.json')
                or (boundary == 'marker_unlink' and path.name == store.INITIALIZING_NAME)):
            fail()
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'mkdir', mkdir)
        patch.setattr(Path, 'open', opened)
        patch.setattr(Path, 'unlink', unlink)
        patch.setattr(runtime.os, 'fsync', fsync)
        patch.setattr(runtime.os, 'link', link)
        patch.setattr(runtime._KernelLease, 'acquire', acquire)
        with pytest.raises(OSError):
            runtime.AgentSession.create_fresh(resolver, name='QuietHelm', agent_id=AID,
                                             backend='codex', model='fixture', thinking_level='high')
    assert directory.exists()
    if boundary in {'link_after', 'alias_unlink', 'marker_unlink'}:
        # Published valid metadata remains reserved, but never ready.
        assert resolver.list_agents() == []
    else:
        with pytest.raises(store.StoreError):
            resolver.list_agents()
    for lookup in (lambda: store.read_agent(directory),
                   lambda: resolver.find_agent(agent_id=AID),
                   lambda: runtime.AgentSession.acquire_existing(resolver, agent_id=AID)):
        with pytest.raises(store.StoreError):
            lookup()
    with runtime._KernelLease(tmp_path / runtime.CATALOG_LEASE_NAME):
        pass
    with runtime._KernelLease(directory / runtime.AGENT_LEASE_NAME):
        pass
    if (directory / 'settings.json').exists():
        assert (directory / store.INITIALIZING_NAME).exists()


def test_destination_created_during_publication_is_never_overwritten(tmp_path, monkeypatch):
    resolver = store.AgentStore(tmp_path)
    original = runtime.os.link
    def raced(src, dst):
        Path(dst).write_bytes(b'preexisting-raced-original')
        return original(src, dst)
    monkeypatch.setattr(runtime.os, 'link', raced)
    with pytest.raises(FileExistsError):
        runtime.AgentSession.create_fresh(resolver, name='QuietHelm', agent_id=AID,
                                         backend='codex', model='fixture', thinking_level='high')
    directory = resolver.agent_directory('QuietHelm')
    assert (directory / 'settings.json').read_bytes() == b'preexisting-raced-original'
    assert (directory / '.settings.initializing.json').exists()
    assert (directory / store.INITIALIZING_NAME).exists()
    with pytest.raises(store.StoreError):
        resolver.list_agents()


@pytest.mark.parametrize('kind', ['empty', 'malformed', 'directory', 'unreadable', 'reparse'])
def test_initializing_marker_blocks_all_folder_authority_and_registration(tmp_path, monkeypatch, kind):
    directory = agent(tmp_path)
    resolver = store.AgentStore(tmp_path)
    session = runtime.AgentSession.acquire_existing(resolver, agent_id=AID)
    marker = directory / store.INITIALIZING_NAME
    if kind == 'directory':
        marker.mkdir()
    else:
        marker.write_bytes(b'' if kind == 'empty' else b'not-json')
    original = Path.lstat
    def inspected(path, *args, **kwargs):
        if path == marker and kind == 'unreadable':
            raise PermissionError('fixture marker inaccessible')
        info = original(path, *args, **kwargs)
        if path == marker and kind == 'reparse':
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
        return info
    monkeypatch.setattr(Path, 'lstat', inspected)
    try:
        if kind in {'unreadable', 'reparse'}:
            with pytest.raises(store.StoreError):
                resolver.list_agents()
        else:
            # Valid inactive metadata reserves identity but is not launchable.
            assert resolver.list_agents() == []
        for lookup in (lambda: store.read_agent(directory),
                       lambda: resolver.find_agent(agent_id=AID),
                       lambda: session.authority, lambda: session.memory_root,
                       lambda: session.conversation_directory(CID),
                       lambda: session.register_presence(lambda _: pytest.fail('never register')),
                       lambda: runtime.AgentSession.acquire_existing(resolver, agent_id=AID)):
            with pytest.raises(store.StoreError):
                lookup()
    finally:
        session.release()

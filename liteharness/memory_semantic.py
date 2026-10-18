"""Explicit, copied optional CPU environment. Reads never provision or load models.

The adopted interpreter, its underlying base and the copied child are distinct
identities. The child receives the exact adopted local wheel offline, not a
system-site/PYTHONPATH lookup or a newly selected harness release.
"""
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname
import venv
import zipfile

# All executable collaborators in this retrieval/install path. Pin their actual
# installed bytes across adopted interpreter, approved wheel and child install.
CODE_FILES = ('__init__.py', 'agent_store.py', 'embed_service.py', 'memory_lookup.py',
              'memory_semantic.py', 'memory_legacy.py', *(
                  'catalog/skills/ls-conversation-lookup/' + name for name in (
                      'find_conversation.py', 'conversation_sources.py', 'embedding_backend.py',
                      'lookup_evidence.py', 'lookup_events.py', 'memory_sources.py', 'litetui_source.py')))
NETWORK_PIP_ENV = frozenset(('PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL', 'PIP_TRUSTED_HOST',
                            'PIP_CERT', 'PIP_CLIENT_CERT', 'PIP_PROXY', 'PIP_TIMEOUT',
                            'PIP_RETRIES', 'PIP_NO_INDEX', 'PIP_FIND_LINKS'))


def modules():
    from liteharness.memory_lookup import engine
    api = engine()
    import memory_sources
    return api, memory_sources


def code_digest(read):
    digest = hashlib.sha256()
    for name in CODE_FILES:
        data = read(name)
        digest.update(name.encode('utf-8') + b'\0' + hashlib.sha256(data).digest())
    return digest.hexdigest()


def distribution_identity():
    """Identify the module actually executing, not a coincidental metadata hit."""
    try:
        distribution = importlib.metadata.distribution('liteharness')
        root = Path(distribution.locate_file('liteharness')).resolve(strict=True)
        if Path(__file__).resolve().parent != root:
            raise RuntimeError('Semantic provisioning requires the adopted installed harness, not checkout/PYTHONPATH code')
        if not any(root.is_relative_to(Path(prefix).resolve(strict=True)) for prefix in (sys.prefix, sys.base_prefix)):
            raise RuntimeError('The adopted installed harness must belong to a known interpreter prefix, not an editable checkout')
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        archive = direct.get('archive_info', {})
        wheel_hash = archive.get('hashes', {}).get('sha256')
        if not wheel_hash and str(archive.get('hash', '')).startswith('sha256='):
            wheel_hash = archive['hash'][7:]
        return {'executable': str(Path(sys.executable).resolve(strict=True)),
                'baseExecutable': str(Path(getattr(sys, '_base_executable', sys.executable)).resolve(strict=True)),
                'harnessVersion': distribution.version, 'harnessRoot': str(root),
                'codeHash': code_digest(lambda name: (root / name).read_bytes()),
                'wheelHash': wheel_hash}
    except (OSError, importlib.metadata.PackageNotFoundError) as error:
        raise RuntimeError('The adopted installed harness is unavailable; repair LiteHarness setup') from error


# Kept as the parent-facing name; never recompute it as an adopted identity in a child.
base_identity = distribution_identity


def paths():
    _, sources = modules()
    directory = sources.unlinked(sources.home() / 'semantic-python')
    python = sources.unlinked(directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
    return directory, python, sources.unlinked(sources.home() / 'semantic-ready.json')


def _confined_root(root, directory):
    _, sources = modules()
    root = sources.unlinked(Path(root))
    root.relative_to(directory)
    return root


def validate_environment(expected, require_marker=True):
    _, sources = modules()
    directory, python, marker = paths()
    if not python.is_file():
        raise RuntimeError('Optional semantic interpreter is absent')
    config = sources.unlinked(directory / 'pyvenv.cfg').read_text(encoding='utf-8')
    values = dict(line.split(' = ', 1) for line in config.splitlines() if ' = ' in line)
    # CPython can record the creator (adopted venv) here, while the actual child
    # _base_executable is the underlying global Python. Both are known identities.
    creators = {Path(expected['executable']), Path(expected['baseExecutable'])}
    if values.get('include-system-site-packages') != 'true' or Path(values.get('executable', '')).resolve() not in creators:
        raise RuntimeError('Optional Python creator/base identity differs; explicit reinstall required')
    if require_marker:
        record = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or record.get('version') != 2 or record.get('base') != expected or record.get('python') != str(python):
            raise RuntimeError('Optional semantic identity changed; explicit reinstall required')
        child = record['child']
        root = _confined_root(child['harnessRoot'], directory)
        if (child['executable'] != str(python) or child['harnessVersion'] != expected['harnessVersion']
                or child['codeHash'] != expected['codeHash'] or code_digest(lambda name: (root / name).read_bytes()) != expected['codeHash']
                or child['wheelHash'] != record['wheel']['sha256']):
            raise RuntimeError('Optional child harness provenance changed')
        artifacts = record.get('artifacts')
        if not isinstance(artifacts, dict) or set(artifacts) != {'tokenizer.json', 'onnx/model.onnx'}:
            raise RuntimeError('Incomplete semantic installation marker')
        for name, size in artifacts.items():
            path = sources.unlinked(sources.home() / 'models/all-MiniLM-L6-v2' / name)
            if type(size) is not int or size <= 0 or not path.is_file() or path.stat().st_size != size:
                raise RuntimeError('Optional semantic artifacts incomplete')
    return python


def readiness():
    """Filesystem/installed metadata only. No child, model import or provisioning."""
    try:
        validate_environment(base_identity())
        return True, None
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as error:
        return False, str(error)[:512]


def pip_environment(base, directory):
    """-I does not isolate pip. Disable config and every destination override."""
    env = {key: value for key, value in base.items()
           if (not key.upper().startswith(('PIP_', 'PYTHON', 'UV_')) or key.upper() in NETWORK_PIP_ENV)}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1',
               PIP_REQUIRE_VIRTUALENV='true', VIRTUAL_ENV=str(directory), PYTHONIOENCODING='utf-8')
    return env


def pip_install(python, directory, arguments):
    _, sources = modules()
    directory = sources.unlinked(directory)
    python = sources.unlinked(python)
    python.relative_to(directory)
    subprocess.run([str(python), '-I', '-m', 'pip', '--isolated', '--require-virtualenv', '--no-cache-dir', 'install',
                    '--prefix', str(directory), '--no-user', *arguments],
                   check=True, stdout=sys.stderr, stderr=sys.stderr, timeout=600,
                   env=pip_environment(os.environ, directory))


def approved_wheel(expected):
    """Resolve an existing local wheel and prove its version/code/hash match adoption."""
    _, sources = modules()
    supplied = os.environ.get('LITEHARNESS_MEMORY_WHEEL')
    if supplied:
        candidate = Path(supplied)
    else:
        distribution = importlib.metadata.distribution('liteharness')
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        parsed = urlparse(direct.get('url', ''))
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise RuntimeError('Exact local LiteHarness wheel unavailable; repair setup or provide LITEHARNESS_MEMORY_WHEEL')
        candidate = Path(url2pathname(parsed.path))
    if not candidate.is_absolute() or candidate.suffix.lower() != '.whl' or '..' in candidate.parts:
        raise RuntimeError('Approved harness wheel must be an existing absolute local .whl')
    candidate = sources.unlinked(candidate)
    if not candidate.is_file() or candidate.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeError('Exact local LiteHarness wheel unavailable or oversized; repair setup')
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    if expected.get('wheelHash') and expected['wheelHash'] != digest:
        raise RuntimeError('Local wheel hash differs from adopted installed artifact')
    with zipfile.ZipFile(candidate) as archive:
        metadata = [n for n in archive.namelist() if n.startswith('liteharness-') and n.endswith('.dist-info/METADATA')]
        if len(metadata) != 1 or f"Version: {expected['harnessVersion']}" not in archive.read(metadata[0]).decode().splitlines():
            raise RuntimeError('Local harness wheel version differs from adopted distribution')
        if code_digest(lambda name: archive.read('liteharness/' + name)) != expected['codeHash']:
            raise RuntimeError('Local harness wheel code differs from adopted installed distribution')
    target_dir = sources.unlinked(sources.home() / 'semantic-wheel')
    target_dir.mkdir(parents=True, exist_ok=True)
    target = sources.unlinked(target_dir / candidate.name)
    if target != candidate:
        atomic_bytes(target, candidate.read_bytes())
    return {'path': str(target), 'sha256': digest}


def provision_record(expected):
    _, sources = modules()
    path = sources.unlinked(sources.home() / 'semantic-provisioning.json')
    record = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(record, dict) or record.get('adopted') != expected or record.get('version') != 2:
        raise RuntimeError('Optional provisioning ownership differs')
    return record


def validate_child(expected, require_marker=True):
    directory, python, _ = paths()
    validate_environment(expected, require_marker)
    actual = distribution_identity()
    _confined_root(actual['harnessRoot'], directory)
    wheel = provision_record(expected)['wheel']
    if (Path(sys.prefix).resolve() != directory.resolve() or Path(actual['executable']) != python
            or Path(actual['baseExecutable']) not in {Path(expected['baseExecutable']), Path(expected['executable'])}
            or actual['harnessVersion'] != expected['harnessVersion'] or actual['codeHash'] != expected['codeHash']
            or actual['wheelHash'] != wheel['sha256']):
        raise RuntimeError('Optional child interpreter/harness identity differs from approved installation')
    return actual


def run_owned(action, request, expected, require_marker=True):
    python = validate_environment(expected, require_marker)
    process = subprocess.run([str(python), '-I', '-m', 'liteharness.memory_semantic', action],
        input=json.dumps({'base': expected, 'request': request}), text=True, encoding='utf-8',
        stdout=subprocess.PIPE, stderr=sys.stderr, timeout=900,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    if len(process.stdout.encode('utf-8')) > 1024 * 1024:
        raise RuntimeError('Optional semantic reply exceeded bound')
    envelope = json.loads(process.stdout)
    if process.returncode or envelope.get('type') != 'result':
        raise RuntimeError(envelope.get('message', 'Optional semantic subprocess failed'))
    return envelope['data']


@contextlib.contextmanager
def install_lock():
    _, sources = modules()
    sources.home().mkdir(parents=True, exist_ok=True)
    with open(sources.home() / 'semantic-install.lock', 'a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('Another semantic install is running') from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def atomic_bytes(path, data):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.stem + '-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, json.dumps(value).encode('utf-8'))


def install(emit):
    api, sources = modules()
    expected = base_identity()
    directory, python, marker = paths()
    with install_lock():
        marker.unlink(missing_ok=True)
        staging = sources.unlinked(sources.home() / 'semantic-provisioning.json')
        if directory.exists():
            if not staging.is_file():
                raise RuntimeError('Optional environment has unknown ownership; repair setup explicitly')
            previous = json.loads(staging.read_text(encoding='utf-8'))
            if previous != expected and previous.get('adopted') != expected:
                raise RuntimeError('Optional environment adoption changed; repair setup explicitly')
        wheel = approved_wheel(expected)
        atomic_json(staging, {'version': 2, 'adopted': expected, 'wheel': wheel})
        if python.is_file() and (directory / 'pyvenv.cfg').is_file():
            validate_environment(expected, require_marker=False)
        else:
            emit({'operation': 'install-semantic', 'state': 'running', 'percent': None, 'message': 'Creating app-owned optional Python (copies, no links)'})
            venv.EnvBuilder(system_site_packages=True, symlinks=False, with_pip=True).create(directory)
            validate_environment(expected, require_marker=False)
        # --ignore-installed forces an owned copy even when system-site-packages
        # exposes another harness; it never uninstalls/rewrites that other copy.
        pip_install(python, directory, ['--no-index', '--no-deps', '--ignore-installed', wheel['path']])
        run_owned('identity', {}, expected, require_marker=False)
        emit({'operation': 'install-semantic', 'state': 'running', 'percent': None, 'message': 'Installing optional extras and validating model in app-owned Python'})
        result = run_owned('install', {}, expected, require_marker=False)
        atomic_json(marker, {'version': 2, 'base': expected, 'python': str(python), 'wheel': wheel,
                             'child': result['identity'], 'artifacts': result['artifacts']})
        validate_environment(expected)
    return api


def download_artifacts(model):
    from liteharness.embed_service import download_model
    try:
        download_model(model)
    except SystemExit as error:
        raise RuntimeError(f'Optional model download failed (exit {error.code})') from error


def child(action, envelope):
    os.environ['LITEHARNESS_CONVO_EMBED_BACKEND'] = 'onnx'
    expected = envelope['base']
    actual = validate_child(expected, require_marker=action not in ('identity', 'install'))
    if action == 'identity':
        return actual
    api, sources = modules()
    if action == 'install':
        import importlib.util
        missing = any(importlib.util.find_spec(name) is None for name in ('onnxruntime', 'numpy', 'tokenizers', 'huggingface_hub'))
        if missing:
            directory, python, _ = paths()
            pip_install(python, directory, ['onnxruntime>=1.18.0', 'numpy>=1.26.0', 'tokenizers>=0.19.0', 'huggingface_hub>=0.23.0'])
        from liteharness.embed_service import EmbedModel
        model = sources.unlinked(sources.home() / 'models/all-MiniLM-L6-v2')
        sources.unlinked(model / 'tokenizer.json')
        sources.unlinked(model / 'onnx/model.onnx')
        with contextlib.redirect_stdout(sys.stderr):
            download_artifacts(model)
            EmbedModel(model).encode(['optional installation validation'])
            api.cmd_index_embeddings()
        return {'identity': actual, 'artifacts': {name: (model / name).stat().st_size for name in ('tokenizer.json', 'onnx/model.onnx')}}
    if action == 'search':
        from liteharness.memory_lookup import dispatch
        # Pass the validated adopted identity, never a truthy flag that can be
        # recomputed against the incompatible child executable/root.
        return dispatch('search', envelope['request'], semantic_child=expected)
    raise ValueError('Unsupported semantic action')


def main():
    try:
        raw = sys.stdin.buffer.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024 or len(sys.argv) != 2:
            raise ValueError('Invalid semantic request')
        result = child(sys.argv[1], json.loads(raw))
        print(json.dumps({'type': 'result', 'data': result}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'type': 'error', 'message': str(error)[:2048]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())

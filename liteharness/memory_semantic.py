"""Explicit optional CPU environment, confined to conversation home. Stdlib reads.

Provisioning is install-only. Keyword/config/status never launch this environment.
The one canonical engine/model implementation is reused, never copied/reimplemented.
"""
import contextlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def modules():
    from liteharness.memory_lookup import engine
    api = engine()
    import memory_sources
    return api, memory_sources


def base_identity():
    try:
        distribution = importlib.metadata.distribution('liteharness')
        root = Path(distribution.locate_file('liteharness')).resolve(strict=True)
    except (OSError, importlib.metadata.PackageNotFoundError) as error:
        raise RuntimeError('The adopted installed harness is unavailable; repair LiteHarness setup') from error
    if Path(__file__).resolve().parent != root:
        raise RuntimeError('Semantic provisioning requires the adopted installed harness, not checkout/PYTHONPATH code')
    return {'executable': str(Path(sys.executable).resolve(strict=True)),
            'baseExecutable': str(Path(getattr(sys, '_base_executable', sys.executable)).resolve(strict=True)),
            'harnessVersion': distribution.version, 'harnessRoot': str(root)}


def paths():
    _, sources = modules()
    directory = sources.unlinked(sources.home() / 'semantic-python')
    python = sources.unlinked(directory / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))
    marker = sources.unlinked(sources.home() / 'semantic-ready.json')
    return directory, python, marker


def validate_environment(expected, require_marker=True):
    _, sources = modules()
    directory, python, marker = paths()
    if not python.is_file():
        raise RuntimeError('Optional semantic interpreter is absent')
    config = sources.unlinked(directory / 'pyvenv.cfg').read_text(encoding='utf-8')
    values = dict(line.split(' = ', 1) for line in config.splitlines() if ' = ' in line)
    if values.get('include-system-site-packages') != 'true' or Path(values.get('executable', '')).resolve() != Path(expected['baseExecutable']):
        raise RuntimeError('Optional Python base identity differs; explicit reinstall required')
    if require_marker:
        record = json.loads(marker.read_text(encoding='utf-8'))
        if not isinstance(record, dict) or type(record.get('version')) is not int or record['version'] != 1 or record.get('base') != expected or record.get('python') != str(python) or not isinstance(record.get('artifacts'), dict):
            raise RuntimeError('Optional semantic identity changed; explicit reinstall required')
        for name, size in record.get('artifacts', {}).items():
            if name not in ('tokenizer.json', 'onnx/model.onnx') or not isinstance(size, int) or size <= 0:
                raise RuntimeError('Invalid semantic artifact marker')
            path = sources.unlinked(sources.home() / 'models/all-MiniLM-L6-v2' / name)
            if not path.is_file() or path.stat().st_size != size:
                raise RuntimeError('Optional semantic artifacts incomplete')
        if set(record.get('artifacts', {})) != {'tokenizer.json', 'onnx/model.onnx'}:
            raise RuntimeError('Incomplete semantic installation marker')
    return python


def readiness():
    """Filesystem/metadata only. Never imports a model, launches or provisions."""
    try:
        validate_environment(base_identity())
        return True, None
    except (OSError, ValueError, RuntimeError, importlib.metadata.PackageNotFoundError) as error:
        return False, str(error)[:512]


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
        lock.write(b'0') if lock.tell() == 0 else None
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


def atomic_json(path, value):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.stem + '-', suffix='.tmp')
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(emit):
    api, sources = modules()
    expected = base_identity()
    directory, python, marker = paths()
    with install_lock():
        # Remove readiness before any mutating provisioning; partial/retry is not ready.
        marker.unlink(missing_ok=True)
        staging = sources.unlinked(sources.home() / 'semantic-provisioning.json')
        complete_environment = python.is_file() and (directory / 'pyvenv.cfg').is_file()
        if directory.exists() and complete_environment:
            validate_environment(expected, require_marker=False)
        else:
            if directory.exists():
                if not staging.is_file() or json.loads(staging.read_text(encoding='utf-8')) != expected:
                    raise RuntimeError('Incomplete optional environment has unknown ownership; repair setup explicitly')
            else:
                atomic_json(staging, expected)
            emit({'operation': 'install-semantic', 'state': 'running', 'percent': None, 'message': 'Creating app-owned optional Python (copies, no links)'})
            venv.EnvBuilder(system_site_packages=True, symlinks=False, with_pip=True).create(directory)
        emit({'operation': 'install-semantic', 'state': 'running', 'percent': None, 'message': 'Installing optional extras and validating model in app-owned Python'})
        result = run_owned('install', {}, expected, require_marker=False)
        record = {'version': 1, 'base': expected, 'python': str(python), 'artifacts': result['artifacts']}
        atomic_json(marker, record)
        validate_environment(expected)
    return api


def download_artifacts(model):
    from liteharness.embed_service import download_model
    try:
        download_model(model)
    except SystemExit as error:
        # Normalize only the known helper boundary, never cancellation/interrupt.
        raise RuntimeError(f'Optional model download failed (exit {error.code})') from error


def child(action, envelope):
    os.environ['LITEHARNESS_CONVO_EMBED_BACKEND'] = 'onnx'
    api, sources = modules()
    expected = envelope['base']
    directory, python, _ = paths()
    validate_environment(expected, require_marker=action != 'install')
    actual = base_identity()
    if (Path(sys.executable).resolve() != python.resolve() or sys.prefix == sys.base_prefix
            or any(actual[key] != expected[key] for key in ('baseExecutable', 'harnessVersion', 'harnessRoot'))):
        raise RuntimeError('Optional environment/harness identity differs from adopted installation')
    if action == 'install':
        import importlib.util
        missing = any(importlib.util.find_spec(name) is None for name in ('onnxruntime', 'numpy', 'tokenizers', 'huggingface_hub'))
        if missing:
            subprocess.run([sys.executable, '-I', '-m', 'pip', 'install', 'onnxruntime>=1.18.0', 'numpy>=1.26.0', 'tokenizers>=0.19.0', 'huggingface_hub>=0.23.0'], check=True, stdout=sys.stderr, stderr=sys.stderr, timeout=600)
        from liteharness.embed_service import EmbedModel
        model = sources.unlinked(sources.home() / 'models/all-MiniLM-L6-v2')
        sources.unlinked(model / 'tokenizer.json')
        sources.unlinked(model / 'onnx/model.onnx')
        with contextlib.redirect_stdout(sys.stderr):
            download_artifacts(model)
            EmbedModel(model).encode(['optional installation validation'])
            api.cmd_index_embeddings()
        return {'artifacts': {name: (model / name).stat().st_size for name in ('tokenizer.json', 'onnx/model.onnx')}}
    if action == 'search':
        from liteharness.memory_lookup import dispatch
        request = envelope['request']
        # A validated owned process executes directly, without recursive routing.
        return dispatch('search', request, semantic_child=True)
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

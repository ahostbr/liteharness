"""Real offline wheel -> adopted venv -> copied owned child; fake model boundary only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def clean_env(base):
    env = {key: value for key, value in base.items() if not key.upper().startswith(('PIP_', 'PYTHON'))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONIOENCODING='utf-8')
    return env


def run(args, *, env, input=None, check=True, timeout=240):
    result = subprocess.run([str(arg) for arg in args], input=input, env=env, text=True, encoding='utf-8', capture_output=True, timeout=timeout)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope='module')
def installed(tmp_path_factory):
    root = tmp_path_factory.mktemp('memory-installed-semantic')
    source = root / 'source'
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    for name in filter(None, files):
        original = ROOT / name
        if original.is_file():
            assert not original.is_symlink()
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
    env = clean_env(os.environ)
    result = subprocess.run([sys.executable, '-m', 'build', '--no-isolation', '--wheel', '--outdir', str(root / 'wheels')], cwd=source, env=env, text=True, capture_output=True, timeout=240)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel, = (root / 'wheels').glob('*.whl')
    adopted = root / 'adopted'
    run([sys.executable, '-m', 'venv', '--copies', '--without-pip', adopted], env=env)
    python = adopted / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    run([sys.executable, '-m', 'pip', '--python', python, 'install', '--no-index', '--no-deps', wheel], env=env)
    return root, python, wheel, env


# Loaded solely in the temporary owned child's site-packages. Product code is
# unchanged: substitute the external model/download boundary, not retrieval,
# ownership, installation, subprocess delegation, protocol or event generation.
MODEL_BOUNDARY = r'''
from pathlib import Path
import os
from liteharness import embed_service
class Model:
    def __init__(self, directory): pass
    def encode(self, texts):
        with (Path(os.environ['LITEHARNESS_CONVO_HOME']) / 'model-boundary.log').open('a') as stream:
            stream.write('encode\n')
        return [[1., 0.] if ('alpha' in text or 'semanticneedle' in text) else [0., 1.] for text in texts]
def download(directory):
    directory = Path(directory)
    (directory / 'onnx').mkdir(parents=True, exist_ok=True)
    (directory / 'tokenizer.json').write_bytes(b'FAKE TOKENIZER; NEVER LOADED')
    (directory / 'onnx/model.onnx').write_bytes(b'FAKE ARTIFACT; NEVER LOADED')
embed_service.EmbedModel = Model
embed_service.download_model = download
'''

INSTALL_DRIVER = r'''
import json, os, pathlib, subprocess, sys
from liteharness import memory_semantic as semantic
real = semantic.run_owned
def model_boundary(action, request, expected, require_marker=True):
    if action == 'install':
        directory, python, marker = semantic.paths()
        probe = subprocess.run([str(python), '-I', '-c', 'import sysconfig,importlib.util; assert importlib.util.find_spec("numpy") is not None, "existing NumPy required for offline deterministic vector proof"; print(sysconfig.get_paths()["purelib"])'], capture_output=True, text=True, check=True)
        site = pathlib.Path(probe.stdout.strip())
        site.mkdir(parents=True, exist_ok=True)
        (site / '_t0585_model_boundary.py').write_text(os.environ['T0585_MODEL_BOUNDARY'], encoding='utf-8')
        (site / 't0585_model_boundary.pth').write_text('import _t0585_model_boundary\n', encoding='utf-8')
        for name in ('onnxruntime', 'tokenizers', 'huggingface_hub'):
            (site / (name + '.py')).write_text('# offline optional dependency boundary\n')
    return real(action, request, expected, require_marker=require_marker)
semantic.run_owned = model_boundary
from liteharness.memory_lookup import main
sys.argv = ['memory_lookup', 'install-semantic']
raise SystemExit(main())
'''


def test_real_adopted_venv_owned_install_and_actual_vector_routes(installed, tmp_path):
    _, python, wheel, base_env = installed
    env = {**base_env, 'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index'), 'CLAUDE_CONFIG_DIR': str(tmp_path / 'claude'), 'CODEX_HOME': str(tmp_path / 'codex'), 'T0585_MODEL_BOUNDARY': MODEL_BOUNDARY}
    path = tmp_path / 'claude/projects/demo/alpha-session.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'type': 'user', 'message': {'content': 'alpha astronomy'}}) + '\n')
    path.with_name('beta-session.jsonl').write_text(json.dumps({'type': 'user', 'message': {'content': 'beta botany'}}) + '\n')
    run([python, '-I', '-m', 'liteharness.memory_lookup', 'index'], env=env, input='{}')
    # No network or real optional-extra install is needed; the real child must
    # import the adopted exact wheel from its OWN site-packages before model work.
    install = run([python, '-I', '-c', INSTALL_DRIVER], env=env, input='{}', check=False)
    assert install.returncode == 0, install.stdout + install.stderr
    result = json.loads(install.stdout.splitlines()[-1])
    assert result['type'] == 'result' and result['data']['semantic']['state'] == 'ready'
    assert result['data']['semantic']['vectorCount'] == 2
    home = tmp_path / 'index'
    owned = home / 'semantic-python' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    origin = run([owned, '-I', '-c', 'import liteharness.memory_semantic as m; print(m.__file__)'], env=env).stdout.strip()
    assert str(home / 'semantic-python').casefold() in origin.casefold(), origin
    assert str(python.parent.parent).casefold() not in origin.casefold(), origin
    # No keyword overlap. A silent BM25 fallback returns zero and cannot pass.
    for mode in ('semantic', 'hybrid'):
        answer = run([python, '-I', '-m', 'liteharness.memory_lookup', 'search'], env=env, input=json.dumps({'query': 'semanticneedle', 'mode': mode}))
        hits = json.loads(answer.stdout)['data']['results']
        assert hits and hits[0]['conversation_id'] == 'alpha-session'
        recent = json.loads(run([python, '-I', '-m', 'liteharness.memory_lookup', 'recent'], env=env, input='{}').stdout)['data']
        assert recent[0]['mode'] == mode and recent[0]['fallback'] is None
    for args in (['--search', 'semanticneedle', '--mode', 'semantic'], ['--search', 'semanticneedle', '--mode', 'hybrid'], ['--index-embeddings']):
        direct = run([owned, '-I', '-m', 'liteharness.memory_legacy', *args, '--no-refresh'], env=env)
        before = len((home / 'lookup-events.jsonl').read_text().splitlines())
        answer = run([python, '-I', '-m', 'liteharness.memory_legacy', *args, '--no-refresh'], env=env)
        assert (answer.returncode, answer.stdout, answer.stderr) == (direct.returncode, direct.stdout, direct.stderr)
        if '--search' in args:
            assert 'alpha-session' in answer.stdout
            assert len((home / 'lookup-events.jsonl').read_text().splitlines()) == before + 1
        else:
            assert 'Embeddings are up to date.' in answer.stdout
            assert len((home / 'lookup-events.jsonl').read_text().splitlines()) == before
    assert (home / 'model-boundary.log').read_text().count('encode') >= 5
    valid = json.loads((home / 'lookup-events.jsonl').read_text().splitlines()[-1])
    with (home / 'lookup-events.jsonl').open('a') as stream:
        stream.write(json.dumps({**valid, 'id': 'hostile-huge-duration', 'durationMs': 10 ** 400}) + '\n')
    recent = json.loads(run([python, '-I', '-m', 'liteharness.memory_lookup', 'recent'], env=env, input='{}').stdout)['data']
    assert valid['id'] in {row['id'] for row in recent}
    assert 'hostile-huge-duration' not in {row['id'] for row in recent}
    print('OWNED_SEMANTIC_PROOF=' + json.dumps({'adopted': str(python), 'ownedOrigin': origin, 'wheel': wheel.name, 'wheelSha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'vectors': 2, 'boundary': 'deterministic model; real canonical vector retrieval/event/delegation'}))


def test_actual_isolated_legacy_entry_reaches_engine_without_sibling_import_failure(installed, tmp_path):
    _, python, _, base_env = installed
    env = {**base_env, 'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index')}
    answer = run([python, '-I', '-m', 'liteharness.memory_legacy', '--stats'], env=env, check=False)
    assert answer.returncode == 0, answer.stdout + answer.stderr
    assert 'No index.' in answer.stdout


def test_real_pip_parser_and_offline_install_cannot_escape_owned_prefix(installed, tmp_path):
    _, adopted, wheel, base_env = installed
    target = tmp_path / 'owned-pip-target'
    run([sys.executable, '-m', 'venv', '--copies', target], env=base_env)
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    escape = tmp_path / 'MUST-NOT-BE-CREATED'
    config = tmp_path / 'hostile-pip.ini'
    config.write_text('[install]\ntarget = ' + str(escape / 'config-target') + '\nprefix = ' + str(escape / 'config-prefix') + '\nuser = true\nroot = ' + str(escape / 'config-root') + '\n')
    before = config.read_bytes()
    hostile = {**base_env, 'PIP_TARGET': str(escape / 'env-target'), 'PIP_PREFIX': str(escape / 'env-prefix'),
               'PIP_ROOT': str(escape / 'env-root'), 'PIP_USER': '1', 'PIP_CONFIG_FILE': str(config),
               'PIP_PYTHON': str(adopted), 'PYTHONUSERBASE': str(escape / 'userbase')}
    parser = r'''from pip._internal.commands import create_command
import json,sys
command = create_command('install', isolated=len(sys.argv)>1)
options,args = command.parse_args(['--prefix',sys.argv[1],'--no-user'] if len(sys.argv)>1 else [])
print(json.dumps({'target':options.target_dir,'prefix':options.prefix_path,'user':options.use_user_site,'root':options.root_path}))'''
    # Read-only red control: -I by itself accepts hostile env/config destinations.
    unsafe = json.loads(run([python, '-I', '-c', parser], env=hostile).stdout)
    assert unsafe['target'] == str(escape / 'env-target') and unsafe['user']
    assert not escape.exists()
    driver = r'''import json,os,subprocess,sys
from pathlib import Path
from liteharness.memory_semantic import pip_environment,pip_install
python,directory,wheel = sys.argv[1:4]
safe = pip_environment(os.environ,Path(directory))
probe = subprocess.run([python,'-I','-c',os.environ['T0585_PARSER'],directory],env=safe,text=True,capture_output=True,check=True)
print(probe.stdout.strip(),flush=True)
pip_install(Path(python),Path(directory),['--no-index','--no-deps','--ignore-installed',wheel])
origin = subprocess.run([python,'-I','-c','import liteharness.memory_semantic as m; print(m.__file__)'],env=safe,text=True,capture_output=True,check=True)
print(json.dumps({'origin':origin.stdout.strip()}))'''
    safe = run([adopted, '-I', '-c', driver, python, target, wheel], env={**hostile, 'T0585_PARSER': parser})
    rows = [json.loads(line) for line in safe.stdout.splitlines()]
    assert rows[0] == {'target': None, 'prefix': str(target), 'user': False, 'root': None}
    assert str(target).casefold() in rows[-1]['origin'].casefold()
    assert not escape.exists() and config.read_bytes() == before
    print('PIP_DESTINATION_PROOF=' + json.dumps({'hostileParser': unsafe, 'safeParser': rows[0], 'installedOrigin': rows[-1]['origin'], 'outsideWrites': False}))


def test_real_adopted_wheel_rejects_same_version_modified_artifact(installed, tmp_path):
    import zipfile
    _, python, wheel, base_env = installed
    altered = tmp_path / wheel.name
    with zipfile.ZipFile(wheel) as original, zipfile.ZipFile(altered, 'w') as target:
        for member in original.infolist():
            content = original.read(member)
            if member.filename == 'liteharness/embed_service.py': content += b'\n# unapproved same-version wheel\n'
            target.writestr(member, content)
    env = {**base_env, 'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index'), 'LITEHARNESS_MEMORY_WHEEL': str(altered)}
    answer = run([python, '-I', '-m', 'liteharness.memory_lookup', 'install-semantic'], env=env, input='{}', check=False)
    assert answer.returncode == 1
    terminal = json.loads(answer.stdout.splitlines()[-1])
    assert terminal['type'] == 'error' and 'wheel hash differs' in terminal['message']
    assert not (tmp_path / 'index/semantic-python').exists()


@pytest.mark.parametrize('already_cached', [False, True])
def test_real_adopted_wheel_snapshot_survives_replacement_before_cache(installed, tmp_path, already_cached):
    import io
    import zipfile
    _, python, wheel, base_env = installed
    candidate = (tmp_path / 'index/semantic-wheel' if already_cached else tmp_path) / wheel.name
    candidate.parent.mkdir(parents=True, exist_ok=True)
    original = wheel.read_bytes()
    candidate.write_bytes(original)
    changed = io.BytesIO(original)
    with zipfile.ZipFile(changed, 'a') as archive:
        archive.comment = b'benign same-version replacement; never installed or executed'
    replacement = tmp_path / 'replacement.bytes'
    replacement.write_bytes(changed.getvalue())
    driver = r'''import hashlib,json,os
from pathlib import Path
from liteharness import memory_semantic as semantic
expected = semantic.base_identity()
original_digest = semantic.code_digest
candidate = Path(os.environ['LITEHARNESS_MEMORY_WHEEL'])
replacement = Path(os.environ['T0585_REPLACEMENT']).read_bytes()
replaced = []
def replace_after_validation(read):
    digest = original_digest(read)
    candidate.write_bytes(replacement)
    replaced.append(hashlib.sha256(candidate.read_bytes()).hexdigest())
    return digest
semantic.code_digest = replace_after_validation
def forbidden(*args, **kwargs): raise AssertionError('No pip/child execution allowed for replacement fixture')
semantic.pip_install = semantic.run_owned = forbidden
approved = semantic.approved_wheel(expected)
cached = Path(approved['path']).read_bytes()
assert len(replaced) == 1
print(json.dumps({'approved':approved['sha256'], 'cached':hashlib.sha256(cached).hexdigest(),
                  'replaced':replaced[0], 'candidate':hashlib.sha256(candidate.read_bytes()).hexdigest()}))'''
    env = {**base_env, 'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index'),
           'LITEHARNESS_MEMORY_WHEEL': str(candidate), 'T0585_REPLACEMENT': str(replacement)}
    answer = run([python, '-I', '-c', driver], env=env)
    proof = json.loads(answer.stdout)
    assert proof['replaced'] != proof['approved'], 'Replacement hook must actually change the external file'
    assert proof['approved'] == hashlib.sha256(original).hexdigest()
    assert proof['cached'] == proof['approved'], proof
    assert (proof['candidate'] == proof['approved']) is already_cached
    assert not (tmp_path / 'index/semantic-python').exists()
    print('WHEEL_SNAPSHOT_PROOF=' + json.dumps(proof))


def test_real_adopted_wheel_enforces_size_bound_during_read(installed, tmp_path):
    _, python, wheel, base_env = installed
    candidate = tmp_path / wheel.name
    candidate.write_bytes(wheel.read_bytes())
    driver = r'''import json,os
from pathlib import Path
from liteharness import memory_semantic as semantic
expected = semantic.base_identity()
candidate = Path(os.environ['LITEHARNESS_MEMORY_WHEEL'])
real_open = Path.open
limit = 32 * 1024 * 1024
reads = []
class BoundedReader:
    def __init__(self, stream): self.stream = stream
    def __enter__(self): return self
    def __exit__(self, *args): self.stream.close()
    def read(self, size=-1):
        reads.append(size)
        assert 0 < size <= limit + 1, f'Wheel read must enforce bound, not trust earlier stat (requested {size})'
        return self.stream.read(size)
def grow_on_open(path, *args, **kwargs):
    if path == candidate and (args[0] if args else kwargs.get('mode', 'r')) == 'rb':
        # Real growth after stat. Oversized bytes are data only, never installed.
        with real_open(path, 'wb') as stream: stream.truncate(limit + 1)
        assert path.stat().st_size == limit + 1, 'Growth injection must actually happen'
        return BoundedReader(real_open(path, *args, **kwargs))
    return real_open(path, *args, **kwargs)
Path.open = grow_on_open
def forbidden(*args, **kwargs): raise AssertionError('No pip/child execution allowed for oversized fixture')
semantic.pip_install = semantic.run_owned = forbidden
try:
    semantic.approved_wheel(expected)
except RuntimeError as error:
    assert 'oversized' in str(error), str(error)
else:
    raise AssertionError('Grown wheel must be refused before validation/cache/install')
assert reads == [limit + 1], reads
print(json.dumps({'readSizes':reads, 'oversized':True}))'''
    env = {**base_env, 'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index'),
           'LITEHARNESS_MEMORY_WHEEL': str(candidate)}
    answer = run([python, '-I', '-c', driver], env=env)
    assert json.loads(answer.stdout) == {'readSizes': [32 * 1024 * 1024 + 1], 'oversized': True}
    assert not (tmp_path / 'index/semantic-wheel').exists()
    assert not (tmp_path / 'index/semantic-python').exists()

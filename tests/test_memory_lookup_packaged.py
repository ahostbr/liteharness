"""Real wheel/isolated interpreter proof; offline, no global installation or models."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, **kwargs):
    process = subprocess.run(args, capture_output=True, text=True, timeout=180, **kwargs)
    assert process.returncode == 0, process.stdout + process.stderr
    return process


def model_members(archive):
    return [{'name': member.filename, 'bytes': member.file_size} for member in archive.infolist()
            if Path(member.filename).suffix.lower() in ('.onnx', '.gguf', '.safetensors')
            or (Path(member.filename).suffix.lower() == '.bin' and member.file_size > 5 * 1024 * 1024)]


def test_own_wheel_contains_canonical_code_and_runs_keyword_without_external_runtime(tmp_path):
    # Stage tracked OWN working files, not live catalog caches, locks, user data or
    # another checkout. No worktree/link creation; build writes only to temp.
    files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    source = tmp_path / 'source'
    for name in filter(None, files):
        original = ROOT / name
        if not original.is_file():
            continue
        assert not original.is_symlink(), 'Do not create links in package proof'
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    wheels = tmp_path / 'wheels'
    run([sys.executable, '-m', 'build', '--no-isolation', '--wheel', '--outdir', str(wheels)], cwd=source)
    wheel, = wheels.glob('*.whl')
    with zipfile.ZipFile(wheel) as archive:
        members = archive.namelist()
        # Positive controls establish the archive scanner can see actual code.
        for name in ['liteharness/memory_lookup.py', 'liteharness/agent_store.py',
                     'liteharness/catalog/skills/ls-conversation-lookup/find_conversation.py',
                     'liteharness/catalog/skills/ls-conversation-lookup/litetui_source.py',
                     'liteharness/catalog/skills/ls-conversation-lookup/memory_sources.py',
                     'liteharness/catalog/skills/ls-conversation-lookup/lookup_events.py']:
            assert archive.read(name), name
        matches = model_members(archive)
        assert not matches, matches
    # Scanner negative control: an actual archive with a fake forbidden model.
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, 'w') as archive:
        archive.writestr('known-code.py', 'pass')
        archive.writestr('fake-model.onnx', b'model fixture')
    with zipfile.ZipFile(bad) as archive:
        assert archive.read('known-code.py')
        assert model_members(archive) == [{'name': 'fake-model.onnx', 'bytes': len(b'model fixture')}]
    venv = tmp_path / 'venv'
    run([sys.executable, '-m', 'venv', '--copies', '--without-pip', str(venv)])
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    env = {**os.environ, 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PIP_CONFIG_FILE': os.devnull,
           'LITEHARNESS_CONVO_HOME': str(tmp_path / 'index'), 'CLAUDE_CONFIG_DIR': str(tmp_path / 'claude'), 'CODEX_HOME': str(tmp_path / 'codex')}
    run([sys.executable, '-m', 'pip', '--python', str(python), 'install', '--no-index', '--no-deps', str(wheel)], env=env)
    # -I proves no checkout PYTHONPATH/user site supplied the implementation.
    origin = run([str(python), '-I', '-c', 'import liteharness.memory_lookup as m; print(m.__file__)'], env=env).stdout.strip()
    assert str(venv).casefold() in origin.casefold(), origin
    path = tmp_path / 'claude/projects/demo/fixture.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'type': 'user', 'message': {'content': 'packagedcanary'}}) + '\n')
    import uuid
    agent = tmp_path / 'tui/.agents/Fixture'
    agent.mkdir(parents=True)
    (agent / 'settings.json').write_text(json.dumps({'schema_version': 1, 'name': 'Fixture', 'agent_id': str(uuid.uuid4()), 'execution': {'backend': 'codex', 'model': 'fixture', 'thinking_level': 'high'}}))
    transcript = agent / 'conversations' / str(uuid.uuid4()) / 'convo.jsonl'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'type': 'msg', 'message': {'role': 'user', 'content': 'packagedcanary'}}) + '\n')
    config = json.loads(run([str(python), '-I', '-m', 'liteharness.memory_lookup', 'get-config'], input='{}', env=env).stdout)['data']
    config['sources'].append({'id': 'owned', 'kind': 'litetui', 'enabled': True, 'path': str(tmp_path / 'tui')})
    run([str(python), '-I', '-m', 'liteharness.memory_lookup', 'save-config'], input=json.dumps({'config': config}), env=env)
    for action in ('get-config', 'status', 'index'):
        result = run([str(python), '-I', '-m', 'liteharness.memory_lookup', action], input='{}', env=env)
        assert json.loads(result.stdout.splitlines()[-1])['type'] == 'result'
    result = run([str(python), '-I', '-m', 'liteharness.memory_lookup', 'search'], input=json.dumps({'query': 'packagedcanary'}), env=env)
    assert len(json.loads(result.stdout)['data']['results']) == 2
    assert not (tmp_path / 'index/models').exists()
    print('WHEEL_PROOF=' + json.dumps({'wheel': wheel.name, 'module': origin, 'archiveModelMatches': matches, 'source': str(ROOT)}))

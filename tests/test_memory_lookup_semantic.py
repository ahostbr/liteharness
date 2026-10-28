"""Fast optional-environment controls; installed subprocess proofs are separate."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from test_memory_lookup_sources import corpus
from liteharness import memory_lookup, memory_semantic as semantic
import memory_sources as sources

ROOT = Path(semantic.__file__).parent
WHEEL = {'path': '/fixture/local.whl', 'sha256': 'a' * 64}


def identity(root):
    return {'executable': str(Path(sys.executable).resolve()), 'baseExecutable': str(Path(getattr(sys, '_base_executable', sys.executable)).resolve()), 'harnessVersion': 'fixture-1', 'harnessRoot': str(root / 'installed/liteharness'), 'codeHash': semantic.code_digest(lambda name: (ROOT / name).read_bytes()), 'wheelHash': WHEEL['sha256']}


def provision_fake(expected):
    directory, python, _ = semantic.paths()
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b'fake interpreter, never executed')
    (directory / 'pyvenv.cfg').write_text('include-system-site-packages = true\nexecutable = ' + expected['executable'] + '\n')
    child = {**expected, 'executable': str(python), 'harnessRoot': str(directory / 'owned-site/liteharness')}
    for name in semantic.CODE_FILES:
        path = Path(child['harnessRoot']) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
    return child


def fake_artifacts():
    model = sources.home() / 'models/all-MiniLM-L6-v2'
    (model / 'onnx').mkdir(parents=True, exist_ok=True)
    (model / 'tokenizer.json').write_bytes(b'fake tokenizer')
    (model / 'onnx/model.onnx').write_bytes(b'fake model; not valid; never loaded')
    return {name: (model / name).stat().st_size for name in ('tokenizer.json', 'onnx/model.onnx')}


def test_explicit_install_copies_only_atomic_ready_repeat_and_partial_retry(corpus):
    root, _ = corpus
    expected = identity(root)
    attempts = []
    class Builder:
        def __init__(self, **kwargs):
            assert kwargs == {'system_site_packages': True, 'symlinks': False, 'with_pip': True}
        def create(self, directory): provision_fake(expected)
    def owned(action, request, base, require_marker=True):
        assert base == expected and require_marker is False
        child = provision_fake(expected)
        if action == 'identity': return child
        assert action == 'install'
        assert not semantic.paths()[2].exists()
        attempts.append(action)
        if len(attempts) == 1:
            raise RuntimeError('fixture cancelled during provisioning')
        return {'identity': child, 'artifacts': fake_artifacts()}
    with patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic.venv, 'EnvBuilder', Builder), patch.object(semantic, 'approved_wheel', return_value=WHEEL), patch.object(semantic, 'pip_install'), patch.object(semantic, 'run_owned', owned):
        with pytest.raises(RuntimeError, match='cancelled'):
            semantic.install(lambda _progress: None)
        assert semantic.readiness()[0] is False
        semantic.install(lambda _progress: None)
        assert semantic.readiness() == (True, None)
        semantic.install(lambda _progress: None)
        assert semantic.readiness() == (True, None)
    assert len(attempts) == 3


def test_wrong_base_harness_marker_and_reparse_are_not_ready(corpus):
    root, _ = corpus
    expected = identity(root)
    child = provision_fake(expected)
    artifacts = fake_artifacts()
    directory, python, marker = semantic.paths()
    valid = {'version': 2, 'base': expected, 'python': str(python), 'child': child, 'wheel': WHEEL, 'artifacts': artifacts}
    with patch.object(semantic, 'base_identity', return_value=expected):
        assert semantic.readiness()[0] is False
        for record in [{**valid, 'base': {**expected, 'executable': '/wrong/python'}},
                       {**valid, 'child': {**child, 'harnessRoot': '/other/harness'}},
                       {**valid, 'child': {**child, 'harnessVersion': 'different'}},
                       {**valid, 'child': {**child, 'wheelHash': 'b' * 64}},
                       {**valid, 'python': '/escape/python'}, {**valid, 'artifacts': {}}]:
            marker.write_text(json.dumps(record))
            assert semantic.readiness()[0] is False
        marker.write_text(json.dumps(valid))
        assert semantic.readiness()[0] is True
        (directory / 'pyvenv.cfg').write_text('include-system-site-packages = true\nexecutable = /wrong/base\n')
        assert semantic.readiness()[0] is False
        original = sources.unlinked
        def reject(path):
            if Path(path) == directory: raise ValueError('reparse fixture')
            return original(path)
        with patch.object(sources, 'unlinked', reject): assert semantic.readiness()[0] is False


def test_read_keyword_index_status_never_route_or_provision_even_if_ready(corpus):
    with patch.object(semantic, 'readiness', return_value=(True, None)), patch.object(semantic, 'run_owned') as run, patch.object(semantic, 'install') as install:
        for action in ('get-config', 'detect', 'status', 'recent', 'index'): memory_lookup.dispatch(action, {})
        memory_lookup.dispatch('search', {'query': 'absent'})
    run.assert_not_called(); install.assert_not_called()


def test_semantic_only_routes_once_through_validated_environment(corpus):
    root, _ = corpus
    expected = identity(root)
    with patch.object(semantic, 'readiness', return_value=(True, None)), patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic, 'run_owned', return_value={'results': ['fixture']}) as run:
        assert memory_lookup.dispatch('search', {'query': 'canary', 'mode': 'semantic'}) == {'results': ['fixture']}
    run.assert_called_once_with('search', {'query': 'canary', 'mode': 'semantic'}, expected)
    with pytest.raises(ValueError, match='validated adopted identity'):
        memory_lookup.dispatch('search', {'query': 'canary', 'mode': 'semantic'}, semantic_child=True)


@pytest.mark.parametrize('mode', ['semantic', 'hybrid'])
@pytest.mark.parametrize('boundary', ['validation', 'spawn'])
def test_semantic_start_failure_records_one_error_and_preserves_exception(corpus, mode, boundary, capsys):
    import lookup_events as events
    root, _ = corpus
    expected = identity(root)
    failure = OSError('fixture semantic process could not start')
    with patch.object(semantic, 'readiness', return_value=(True, None)), \
            patch.object(semantic, 'base_identity', return_value=expected), \
            patch.object(semantic, 'validate_environment', side_effect=failure if boundary == 'validation' else None, return_value=Path(sys.executable)), \
            patch.object(semantic.subprocess, 'run', side_effect=failure) as run:
        with pytest.raises(OSError) as caught:
            memory_lookup.dispatch('search', {'query': 'spawn canary', 'mode': mode, 'seat': 'fixture-seat'})
    assert caught.value is failure
    assert run.call_count == (boundary == 'spawn')
    assert capsys.readouterr().out == ''
    spool = sources.home() / 'lookup-events.jsonl'
    rows = [json.loads(line) for line in spool.read_text(encoding='utf-8').splitlines()] if spool.exists() else []
    assert len(rows) == 1, 'pre-child failure must leave exactly one lookup event'
    event = rows[0]
    assert events.valid_event(event)
    assert event['query'] == 'spawn canary'
    assert event['status'] == 'error'
    assert event['mode'] == mode
    assert event['seat'] == 'fixture-seat'
    assert event['error'] == str(failure)
    assert event['hits'] == [] and event['fallback'] is None


@pytest.mark.parametrize('action', ['identity', 'install'])
@pytest.mark.parametrize('boundary', ['validation', 'spawn'])
def test_non_search_start_failure_records_no_lookup(corpus, action, boundary):
    root, _ = corpus
    failure = OSError('fixture startup failure')
    with patch.object(semantic, 'validate_environment', side_effect=failure if boundary == 'validation' else None, return_value=Path(sys.executable)), \
            patch.object(semantic.subprocess, 'run', side_effect=failure):
        with pytest.raises(OSError) as caught:
            semantic.run_owned(action, {}, identity(root))
    assert caught.value is failure
    assert not (sources.home() / 'lookup-events.jsonl').exists()


@pytest.mark.parametrize('spool_fails', [False, True])
def test_validation_runtime_error_survives_telemetry_failure(corpus, spool_fails):
    import lookup_events as events
    root, _ = corpus
    failure = RuntimeError('fixture identity changed')
    with patch.object(semantic, 'validate_environment', side_effect=failure), \
            patch.object(semantic.subprocess, 'run') as run, \
            patch.object(events, 'append', wraps=events.append, side_effect=OSError('disk full') if spool_fails else None):
        with pytest.raises(RuntimeError) as caught:
            semantic.run_owned('search', {'query': 'validation canary', 'mode': 'semantic'}, identity(root))
    assert caught.value is failure
    run.assert_not_called()
    rows = events.recent()
    assert len(rows) == (0 if spool_fails else 1)
    if rows:
        assert rows[0]['status'] == 'error' and rows[0]['error'] == str(failure)


@pytest.mark.parametrize('outcome,child_captured', [
    ('result', True), ('error', True), ('timeout', True), ('crash', True),
    ('timeout', False), ('crash', False),
])
def test_started_child_remains_only_event_owner(corpus, outcome, child_captured):
    import lookup_events as events
    import lookup_evidence
    root, _ = corpus
    expected = identity(root)
    request = {'query': 'child canary', 'mode': 'semantic'}
    child_failure = RuntimeError('fixture child lookup failed')
    timeout = semantic.subprocess.TimeoutExpired('fixture child', 900)

    def child_process(argv, **kwargs):
        envelope = {'type': 'result', 'data': {'results': []}}
        if child_captured:
            try:
                envelope['data'] = memory_lookup.dispatch('search', request, semantic_child=expected)
            except RuntimeError as error:
                envelope = {'type': 'error', 'message': str(error)}
        if outcome == 'timeout':
            raise timeout
        return semantic.subprocess.CompletedProcess(argv, 0 if outcome == 'result' else 1,
                                                     stdout='' if outcome == 'crash' else json.dumps(envelope))

    with patch.object(semantic, 'readiness', return_value=(True, None)), \
            patch.object(semantic, 'base_identity', return_value=expected), \
            patch.object(semantic, 'validate_environment', return_value=Path(sys.executable)), \
            patch.object(semantic, 'validate_child'), \
            patch.object(lookup_evidence, 'search_evidence', return_value={'results': []}, side_effect=child_failure if outcome == 'error' else None), \
            patch.object(semantic.subprocess, 'run', side_effect=child_process):
        if outcome == 'result':
            assert memory_lookup.dispatch('search', request) == {'results': []}
        else:
            error_type = {'error': RuntimeError, 'timeout': semantic.subprocess.TimeoutExpired, 'crash': json.JSONDecodeError}[outcome]
            with pytest.raises(error_type) as caught:
                memory_lookup.dispatch('search', request)
            if outcome == 'timeout':
                assert caught.value is timeout
            elif outcome == 'error':
                assert str(caught.value) == str(child_failure)
    spool = sources.home() / 'lookup-events.jsonl'
    rows = [json.loads(line) for line in spool.read_text(encoding='utf-8').splitlines()] if spool.exists() else []
    assert len(rows) == int(child_captured)
    if rows:
        assert rows[0]['status'] == ('error' if outcome == 'error' else 'empty')
        assert events.valid_event(rows[0])


def test_spawn_failure_keeps_one_terminal_stdout_envelope(corpus, capsys):
    import io
    root, _ = corpus
    failure = OSError('fixture spawn failed')
    request = {'query': 'protocol canary', 'mode': 'semantic'}
    with patch.object(semantic, 'readiness', return_value=(True, None)), \
            patch.object(semantic, 'base_identity', return_value=identity(root)), \
            patch.object(semantic, 'validate_environment', return_value=Path(sys.executable)), \
            patch.object(semantic.subprocess, 'run', side_effect=failure), \
            patch.object(sys, 'argv', ['memory_lookup', 'search']), \
            patch.object(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(request).encode()))):
        assert memory_lookup.main() == 1
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {'type': 'error', 'message': str(failure)}]


def test_pip_targets_owned_only_and_strips_destination_configuration(corpus):
    root, _ = corpus
    expected = identity(root)
    provision_fake(expected)
    directory, python, _ = semantic.paths()
    with patch.dict(os.environ, {'PIP_TARGET': '/outside', 'PIP_PREFIX': '/outside', 'PIP_USER': '1', 'PIP_CONFIG_FILE': '/outside/config', 'PIP_INDEX_URL': 'https://deliberate.invalid/simple'}), patch.object(semantic.subprocess, 'run') as pip:
        semantic.pip_install(python, directory, ['--no-index', '--no-deps', '/fixture/local.whl'])
    argv = pip.call_args.args[0]
    assert argv[:8] == [str(python), '-I', '-m', 'pip', '--isolated', '--require-virtualenv', '--no-cache-dir', 'install']
    assert argv[8:11] == ['--prefix', str(directory), '--no-user']
    env = pip.call_args.kwargs['env']
    assert 'PIP_TARGET' not in env and 'PIP_PREFIX' not in env and 'PIP_USER' not in env
    assert env['PIP_CONFIG_FILE'] == os.devnull and env['PIP_REQUIRE_VIRTUALENV'] == 'true'
    assert env['PIP_INDEX_URL'] == 'https://deliberate.invalid/simple'


def test_explicit_retry_resumes_only_owned_incomplete_environment(corpus):
    root, _ = corpus
    expected = identity(root)
    attempts = []
    class Builder:
        def __init__(self, **kwargs): assert kwargs['symlinks'] is False
        def create(self, directory):
            attempts.append(directory)
            if len(attempts) == 1:
                directory.mkdir(parents=True)
                raise RuntimeError('fixture interrupted while copying Python')
            provision_fake(expected)
    def owned(*args, **kwargs): return {'identity': provision_fake(expected), 'artifacts': fake_artifacts()}
    with patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic.venv, 'EnvBuilder', Builder), patch.object(semantic, 'approved_wheel', return_value=WHEEL), patch.object(semantic, 'pip_install'), patch.object(semantic, 'run_owned', owned):
        with pytest.raises(RuntimeError, match='interrupted'): semantic.install(lambda _p: None)
        assert semantic.readiness()[0] is False
        semantic.install(lambda _p: None)
        assert semantic.readiness()[0] is True
    assert len(attempts) == 2

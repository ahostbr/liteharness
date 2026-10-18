"""Optional provisioning contracts with fake artifacts/process boundaries only."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from test_memory_lookup_sources import corpus
from liteharness import memory_lookup, memory_semantic as semantic
import memory_sources as sources


def identity(root):
    return {'executable': str(Path(sys.executable).resolve()), 'baseExecutable': str(Path(getattr(sys, '_base_executable', sys.executable)).resolve()), 'harnessVersion': 'fixture-1', 'harnessRoot': str(root / 'installed/liteharness')}


def provision_fake(expected):
    directory, python, _ = semantic.paths()
    python.parent.mkdir(parents=True)
    python.write_bytes(b'fake interpreter, never executed')
    (directory / 'pyvenv.cfg').write_text('include-system-site-packages = true\nexecutable = ' + expected['baseExecutable'] + '\n')


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
        def create(self, directory):
            assert directory == sources.home() / 'semantic-python'
            provision_fake(expected)
    def owned(action, request, base, require_marker=True):
        assert action == 'install' and base == expected and require_marker is False
        assert not semantic.paths()[2].exists()
        attempts.append(action)
        if len(attempts) == 1:
            raise RuntimeError('fixture cancelled during provisioning')
        return {'artifacts': fake_artifacts()}
    with patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic.venv, 'EnvBuilder', Builder), patch.object(semantic, 'run_owned', owned):
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
    provision_fake(expected)
    artifacts = fake_artifacts()
    directory, python, marker = semantic.paths()
    valid = {'version': 1, 'base': expected, 'python': str(python), 'artifacts': artifacts}
    with patch.object(semantic, 'base_identity', return_value=expected):
        assert semantic.readiness()[0] is False  # No marker, never infer ready from files.
        for record in [{**valid, 'base': {**expected, 'executable': '/wrong/python'}},
                       {**valid, 'base': {**expected, 'harnessRoot': '/other/harness'}},
                       {**valid, 'base': {**expected, 'harnessVersion': 'different'}},
                       {**valid, 'python': '/escape/python'}, {**valid, 'artifacts': {}}]:
            marker.write_text(json.dumps(record))
            assert semantic.readiness()[0] is False
        marker.write_text(json.dumps(valid))
        assert semantic.readiness()[0] is True
        (directory / 'pyvenv.cfg').write_text('include-system-site-packages = true\nexecutable = /wrong/base\n')
        assert semantic.readiness()[0] is False
        original = sources.unlinked
        def reject(path):
            if Path(path) == directory:
                raise ValueError('reparse fixture')
            return original(path)
        with patch.object(sources, 'unlinked', reject):
            assert semantic.readiness()[0] is False


def test_read_keyword_index_status_never_route_or_provision_even_if_ready(corpus):
    root, _ = corpus
    with patch.object(semantic, 'readiness', return_value=(True, None)), patch.object(semantic, 'run_owned') as run, patch.object(semantic, 'install') as install:
        for action in ('get-config', 'detect', 'status', 'recent', 'index'):
            memory_lookup.dispatch(action, {})
        memory_lookup.dispatch('search', {'query': 'absent'})
    run.assert_not_called(); install.assert_not_called()


def test_semantic_only_routes_once_through_validated_environment(corpus):
    root, _ = corpus
    expected = identity(root)
    with patch.object(semantic, 'readiness', return_value=(True, None)), patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic, 'run_owned', return_value={'results': ['fixture']}) as run:
        assert memory_lookup.dispatch('search', {'query': 'canary', 'mode': 'semantic'}) == {'results': ['fixture']}
    run.assert_called_once_with('search', {'query': 'canary', 'mode': 'semantic'}, expected)


def test_child_pip_targets_owned_only_and_shipped_helper_exit_is_normalized(corpus):
    root, api = corpus
    expected = identity(root)
    provision_fake(expected)
    directory, python, _ = semantic.paths()
    model = sources.home() / 'models/all-MiniLM-L6-v2'
    class Model:
        def __init__(self, path): assert path == model
        def encode(self, texts): return [[1., 0.]]
    def download(path): fake_artifacts()
    fake_module = SimpleNamespace(download_model=download, EmbedModel=Model)
    with patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic, 'modules', return_value=(api, sources)), patch.object(sys, 'executable', str(python)), patch.object(sys, 'prefix', str(directory)), patch('importlib.util.find_spec', return_value=None), patch.object(semantic.subprocess, 'run') as pip, patch.dict(sys.modules, {'liteharness.embed_service': fake_module}), patch.object(api, 'cmd_index_embeddings'):
        result = semantic.child('install', {'base': expected, 'request': {}})
    assert result['artifacts']
    argv = pip.call_args.args[0]
    assert argv[:5] == [str(python), '-I', '-m', 'pip', 'install']
    assert '--user' not in argv and '--system' not in argv
    assert str(python) != expected['executable']


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
    with patch.object(semantic, 'base_identity', return_value=expected), patch.object(semantic.venv, 'EnvBuilder', Builder), patch.object(semantic, 'run_owned', side_effect=lambda *args, **kwargs: {'artifacts': fake_artifacts()}):
        with pytest.raises(RuntimeError, match='interrupted'):
            semantic.install(lambda _p: None)
        assert semantic.readiness()[0] is False
        semantic.install(lambda _p: None)
        assert semantic.readiness()[0] is True
    assert len(attempts) == 2

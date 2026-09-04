"""Real subprocess/maildir proof, isolated from user inbox and UI."""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

from liteharness import cli

MODULE = 'liteharness.cli_scripts.codex.liteharness_watcher_supervisor'


def launch(root, agent='stdout-recipient'):
    return subprocess.Popen(
        [sys.executable, '-u', '-m', MODULE, '--root', str(root), '--agent-id', agent,
         '--model', 'test-model'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
    )


def until(lines, expected, timeout=15):
    deadline = time.monotonic() + timeout
    observed = []
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(.01, deadline - time.monotonic()))
        except queue.Empty:
            break
        observed.append(line)
        if expected in line:
            return ''.join(observed)
    raise AssertionError(f'Missing {expected!r}; output={observed!r}')


def test_delivery_duplicate_refusal_and_restart(tmp_path):
    new = tmp_path / 'inbox' / 'new'
    new.mkdir(parents=True)
    proc = launch(tmp_path)
    lines = queue.Queue()
    reader = threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True)
    reader.start()
    try:
        until(lines, 'Watching inbox')
        other = launch(tmp_path)
        output, _ = other.communicate(timeout=10)
        assert other.returncode == 3, output
        assert 'already owns' in output
        for ident, target in [('wrong', 'another-seat'), ('right', 'stdout-recipient')]:
            (new / f'{ident}.json').write_text(json.dumps({
                'id': ident, 'to': target, 'from': 'test-sender',
                'type': 'notification', 'body': f'probe-{ident}',
            }), encoding='utf-8')
        body = until(lines, '[END OF MESSAGE right')
        assert 'probe-right' in body and 'probe-wrong' not in body
        deadline = time.monotonic() + 5
        while not (tmp_path / 'inbox' / 'done' / 'right.json').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path / 'inbox' / 'done' / 'right.json').exists()
        assert (new / 'wrong.json').exists()
        # Registration used to kill hooks-watch processes and spawn pythonw.
        manual = 'liteharness.cli_scripts.codex.manual_liteharness'
        env = {**os.environ, 'LITEHARNESS_AGENT_ID': 'stdout-recipient'}
        result = subprocess.run([sys.executable, '-m', manual, '--root', str(tmp_path),
                                 'start', '--check-now'], capture_output=True, text=True,
                                env=env, timeout=20)
        assert result.returncode == 0, result.stderr
        assert 'did not claim' in result.stdout
        assert proc.poll() is None
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        reader.join(timeout=2)
        proc.stdout.close()
    replacement = launch(tmp_path)
    try:
        lines2 = queue.Queue()
        reader2 = threading.Thread(target=lambda: [lines2.put(l) for l in replacement.stdout], daemon=True)
        reader2.start()
        until(lines2, 'Watching inbox')
    finally:
        replacement.terminate()
        replacement.wait(timeout=10)
        reader2.join(timeout=2)
        replacement.stdout.close()


def test_installer_updates_all_codex_aliases(tmp_path):
    with mock.patch.object(Path, 'home', return_value=tmp_path):
        assert cli._install_cli_scripts('codex-cli')
    root = tmp_path / '.codex' / 'skills'
    package = Path(cli.__file__).parent
    source = package / 'cli_scripts' / 'codex'
    for name in ['liteharness_notify.py', 'liteharness_inbox_watcher.py',
                 'liteharness_watcher_supervisor.py', 'manual_liteharness.py']:
        assert (root / 'liteharness' / 'scripts' / name).read_bytes() == (source / name).read_bytes()
    assert (root / 'liteharness-manual-start' / 'scripts' / 'manual_liteharness.py').read_bytes() == (source / 'manual_liteharness.py').read_bytes()
    canonical = (package / 'catalog' / 'skills' / 'ls-liteharness' / 'SKILL.md').read_bytes()
    for alias in ['liteharness', 'ls-liteharness']:
        assert (root / alias / 'SKILL.md').read_bytes() == canonical

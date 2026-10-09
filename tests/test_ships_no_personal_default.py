"""What ships gives a fresh user no personal default.

The pre-commit checker prints a name as a note and does not block on it, so this
test is the half of the gate that blocks: no file that ships may name the author's
own orchestrator or the author as an identity. It reuses the checker's scan_file,
which keeps one definition of the stable identifiers (COMPATIBILITY) and of the
legal-author fields.

It reads bytes; it does not run a first boot. The checker matches whole words, so
a name inside a run-together word is not found.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ship_guard', ROOT / 'scripts/check_pii.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SHIPPED = ('liteharness', 'README.md', 'pyproject.toml', 'LICENSE')


def name_findings(relative, data):
    return [(line, label) for line, label in guard.scan_file(relative, data) if label in guard.NOTES]


def test_no_shipped_file_names_a_personal_default():
    # Control first, so a blind scan cannot pass: a made-up shipped default is found.
    for encoded, label in (('73656e74696e656c', 'private orchestrator identity'),
                           ('7279616e', 'private human identity')):
        made_up = ('name = "' + bytes.fromhex(encoded).decode() + '"\n').encode()
        assert name_findings('liteharness/made_up_default.py', made_up) == [(1, label)]

    listed = subprocess.check_output(['git', 'ls-files', '-z', '--', *SHIPPED], cwd=ROOT)
    shipped = [name for name in listed.decode('utf-8').split('\0') if name]
    assert len(shipped) > 400 and 'LICENSE' in shipped and 'pyproject.toml' in shipped
    found = {name: hits for name in shipped
             if (hits := name_findings(name, (ROOT / name).read_bytes()))}
    assert not found, 'shipped files name a personal default: ' + repr(found)

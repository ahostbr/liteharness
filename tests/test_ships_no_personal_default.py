"""What ships carries nothing private: no machine path, no personal email, no private identifier.

The maintainer's ruling of 2026-11-11: names are fine (his own and his seats' are public
already); folders, keys, build logs and emails are what must not ship. So a name is no longer a
finding here. Every OTHER label of the pre-commit checker still is: this test is the half of
the gate that blocks on the whole shipped tree, where the checker in staged mode sees only the
files of one commit. It reuses the checker's scan_file, which keeps one definition of the
machine paths, the hashed private identifiers and the legal-author fields.

It reads bytes; it does not run a first boot. The checker matches whole words, so an
identifier inside a run-together word is not found. It has no detector for secret keys: none
is claimed here.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ship_guard', ROOT / 'scripts/check_pii.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
SHIPPED = ('liteharness', 'README.md', 'pyproject.toml', 'LICENSE')


def private_findings(relative, data):
    return [(line, label) for line, label in guard.scan_file(relative, data) if label not in guard.NOTES]


def test_names_are_notes_and_nothing_else_is():
    # The rule this file used to enforce the other way round: a name alone is not a finding.
    for encoded in ('73656e74696e656c', '7279616e'):
        made_up = ('name = "' + bytes.fromhex(encoded).decode() + '"\n').encode()
        assert guard.scan_file('liteharness/made_up_default.py', made_up), 'the checker still sees the name'
        assert private_findings('liteharness/made_up_default.py', made_up) == []
    assert guard.NOTES == {'private orchestrator identity', 'private human identity'}, \
        'a label added to NOTES stops blocking everywhere: change this line on purpose'


def test_no_shipped_file_carries_a_private_path_email_or_identifier():
    # Controls first, so a blind scan cannot pass: each kind of made-up leak is found.
    folder = 'SA' + 'S'
    home = 'Us' + 'ers'
    planted = {
        'cwd = "G:' + '\\' + folder + '\\x"\n': 'private drive',
        'cwd = "e:/' + folder + '/x"\n': 'private drive',
        'root = "C:' + '\\' + 'Pro' + 'jects\\x"\n': 'machine project root',
        'home = "C:' + '\\' + home + '\\somebody\\x"\n': 'username path',
    }
    for text, label in planted.items():
        assert private_findings('liteharness/made_up_default.py', text.encode()) == [(1, label)], label
    assert private_findings('liteharness/made_up_default.py', ('home = "C:/' + home + '/testuser/x"\n').encode()) == []

    listed = subprocess.check_output(['git', 'ls-files', '-z', '--', *SHIPPED], cwd=ROOT)
    shipped = [name for name in listed.decode('utf-8').split('\0') if name]
    assert len(shipped) > 400 and 'LICENSE' in shipped and 'pyproject.toml' in shipped
    found = {name: hits for name in shipped
             if (hits := private_findings(name, (ROOT / name).read_bytes()))}
    assert not found, 'shipped files carry private material: ' + repr(found)

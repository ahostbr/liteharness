"""Read-only relocated Git must pass; schedule writers never gain its waiver."""
import pytest

from liteharness import deny_floor, deny_gate

READS = ['worktree list', 'worktree list --porcelain', 'worktree list --porcelain -z',
         'status', 'log -- jobs.json', 'show HEAD:jobs.json', 'diff -- jobs.json',
         'rev-parse --show-toplevel', 'branch --list', 'branch --list feature*',
         'branch --all --list', 'branch --list --verbose', 'branch --show-current',
         'merge-base --is-ancestor ' + 'a' * 40 + ' ' + 'B' * 40]
RELOCATIONS = ['-C {root}', '-C{root}', '--git-dir={root}/.git',
               '--git-dir {root}/.git', '--git-dir={root}/.git --work-tree={root}',
               '--no-pager -C {root} --git-dir {root}/.git']


@pytest.fixture
def roots(tmp_path):
    protected = tmp_path / 'protected'
    (protected / 'src' / 'litetui').mkdir(parents=True)
    (protected / 'jobs.json').write_text('[]', encoding='utf-8')
    plain = tmp_path / 'plain'
    plain.mkdir()
    return protected, plain


@pytest.mark.parametrize('shell', [None, 'bash', 'powershell', 'cmd'])
@pytest.mark.parametrize('relocation', RELOCATIONS)
@pytest.mark.parametrize('reader', READS)
def test_literal_relocated_reads_pass_floor_and_hook(roots, shell, relocation, reader):
    protected, plain = roots
    command = f'git {relocation.format(root=protected.as_posix())} {reader}'
    assert deny_floor.refusal(command, plain, shell=shell) is None
    assert deny_gate.decide({'tool_name': 'Bash', 'tool_input': {'command': command},
                            'cwd': str(plain)}) is None


@pytest.mark.parametrize('relocation', RELOCATIONS)
@pytest.mark.parametrize('writer', ['commit -m message', 'checkout -- jobs.json',
                                   'worktree add another', 'worktree remove another',
                                   'config user.name example', 'push', 'branch -D old',
                                   'branch --list --set-upstream-to=other',
                                   'branch --list -m old new'])
def test_relocated_writes_do_not_gain_reader_waiver(roots, relocation, writer):
    protected, plain = roots
    command = f'git {relocation.format(root=protected.as_posix())} {writer}'
    reason = deny_floor.refusal(command, plain)
    assert reason and '[jobs-file]' in reason
    result = deny_gate.decide({'tool_name': 'Bash', 'tool_input': {'command': command},
                              'cwd': str(plain)})
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('tail', [
    'worktree list > {root}/jobs.json', 'log --output={root}/jobs.json',
    'diff -o {root}/jobs.json', 'worktree list --unknown-option',
    'worktree list --porcelain add another', 'branch --list --edit-description',
    'branch --list --delete old', 'branch --list --create-reflog',
    'status; git -C {root} checkout -- jobs.json',
    'status && git --git-dir={root}/.git push',
    'branch --show-current --delete old',
    'branch --show-current > {root}/jobs.json',
    'branch --show-current; git -C {root} checkout -- jobs.json',
    'merge-base --is-ancestor ' + 'a' * 40 + ' ' + 'b' * 40 + ' > {root}/jobs.json',
    'merge-base --is-ancestor ' + 'a' * 40 + ' ' + 'b' * 40 + '; Remove-Item {root}/jobs.json',
    'merge-base --is-ancestor HEAD other',
    'merge-base --is-ancestor ' + 'a' * 40 + ' ' + 'b' * 40 + ' --octopus',
])
def test_output_and_compound_writers_remain_refused(roots, tail):
    protected, plain = roots
    command = f'git -C {protected.as_posix()} {tail.format(root=protected.as_posix())}'
    assert '[jobs-file]' in (deny_floor.refusal(command, plain) or '')


@pytest.mark.parametrize('command', [
    'git -C "$root" status', 'git --git-dir=$root status',
    'git -C {root} -c alias.status=push status',
    'git -C {root} --git-dir', 'git -C',
    'git -C {root} "status; git push"',
    'git -C {root} unknown status',
])
def test_ambiguous_relocation_does_not_infer_reader_from_arguments(roots, command):
    protected, plain = roots
    command = command.format(root=protected.as_posix())
    assert '[jobs-file]' in (deny_floor.refusal(command, plain) or '')


@pytest.mark.parametrize('command', [
    'bash -c "git --git-dir={root}/.git worktree list"',
    'pwsh -Command "git -C {root} branch --list"',
    'cmd /c "git --git-dir={root}/.git status"',
])
def test_literal_nested_shell_reader(roots, command):
    protected, plain = roots
    assert deny_floor.refusal(command.format(root=protected.as_posix()), plain) is None


@pytest.mark.parametrize('execution', [
    'diff --ext-diff', 'diff --textconv', 'show --ext-diff HEAD',
    'show --textconv HEAD', 'log --ext-diff -p', 'log --textconv -p',
    '-c diff.external=program diff', '--config=diff.external=program diff',
    '--config-env=diff.external=PROGRAM diff', '--exec-path=program diff',
    '--paginate diff', '-p diff', 'difftool', 'diff --pager=program',
    'diff --ext-diff=program',
])
def test_external_execution_is_not_a_relocated_reader(roots, execution):
    protected, plain = roots
    command = f'git -C {protected.as_posix()} {execution}'
    assert '[jobs-file]' in (deny_floor.refusal(command, plain) or '')
    out = deny_gate.decide({'tool_name': 'Bash', 'tool_input': {'command': command},
                           'cwd': str(plain)})
    assert out['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('reader', ['diff --no-ext-diff', 'diff --no-textconv',
                                   'show --no-ext-diff --no-textconv HEAD',
                                   'log --no-ext-diff -p'])
def test_explicit_no_execution_reader_options_pass(roots, reader):
    protected, plain = roots
    assert deny_floor.refusal(f'git -C {protected.as_posix()} {reader}', plain) is None

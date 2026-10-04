"""Actual operator CLI flags, fixture stores only; never real apply."""
import json

import pytest

from liteharness import agent_migration_cli as cli
from test_agent_migration import snapshot
from test_agent_migration_copy import candidate as candidate_fixture

candidate = candidate_fixture


def args(candidate):
    root, registry, _, _ = candidate
    return ['--copy-agent', 'QuietHelm', '--root', str(root), '--registry-root', str(registry)]


def test_actual_cli_dry_run_default_no_writes_or_copy(candidate, monkeypatch, capsys):
    root, registry, _, _ = candidate
    before = snapshot(root)
    monkeypatch.setattr(cli, 'copy_named_agent', lambda *a, **k: pytest.fail('dry-run must not copy'))
    cli.main(args(candidate))
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'dry-run' and not result['apply'] and not result['activated']
    assert result['agent']['name'] == 'QuietHelm'
    assert snapshot(root) == before
    assert not (root / '.agents').exists()
    assert (registry / 'names.json').is_file()


def test_actual_cli_apply_explicit_routes_exact_approved_plan_and_agent(candidate, monkeypatch, capsys):
    root, registry, manifest, receipt = candidate
    plan_path, receipt_path = root / 'fixture-plan.json', root / 'fixture-receipt.json'
    plan_path.write_text(json.dumps(manifest))
    receipt_path.write_text(json.dumps(receipt))
    calls = []
    def apply(plan, **kwargs):
        calls.append((plan, kwargs))
        return {'status': 'fixture-call-only', 'activated': False}
    monkeypatch.setattr(cli, 'copy_named_agent', apply)
    cli.main([*args(candidate), '--apply', '--manifest', str(plan_path), '--merge-receipt', str(receipt_path)])
    assert len(calls) == 1 and calls[0][0] == manifest
    assert calls[0][1] == {'agent_id': manifest['agents'][0]['agent_id'],
                           'registry_root': registry, 'merge_receipt': receipt}
    assert json.loads(capsys.readouterr().out)['status'] == 'fixture-call-only'
    assert not (root / '.agents').exists()


@pytest.mark.parametrize('flags', [['--apply'], ['--manifest', 'missing.json'],
    ['--merge-receipt', 'missing.json'], ['--manifest', 'm.json', '--merge-receipt', 'r.json'],
    ['--apply', '--manifest', 'm.json'], ['--apply', '--merge-receipt', 'r.json'],
    ['--unknown'], ['--copy-agent', '../escape']])
def test_invalid_cli_combinations_fail_before_copy_or_mutation(candidate, monkeypatch, flags):
    root, _, _, _ = candidate
    before = snapshot(root)
    monkeypatch.setattr(cli, 'copy_named_agent', lambda *a, **k: pytest.fail('invalid flags cannot copy'))
    with pytest.raises(SystemExit) as error:
        cli.main([*args(candidate), *flags])
    assert error.value.code == 2
    assert snapshot(root) == before


def test_apply_missing_or_mismatching_manifest_defers(candidate, monkeypatch):
    root, _, manifest, receipt = candidate
    plan_path, receipt_path = root / 'fixture-plan.json', root / 'fixture-receipt.json'
    manifest['root'] = str(root / 'not-approved')
    plan_path.write_text(json.dumps(manifest))
    receipt_path.write_text(json.dumps(receipt))
    monkeypatch.setattr(cli, 'copy_named_agent', lambda *a, **k: pytest.fail('mismatched roots cannot copy'))
    with pytest.raises(SystemExit) as error:
        cli.main([*args(candidate), '--apply', '--manifest', str(plan_path), '--merge-receipt', str(receipt_path)])
    assert error.value.code == 2

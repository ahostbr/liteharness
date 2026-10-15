from dataclasses import replace

import pytest

from liteharness.checkpoint_contract import (Blocker, ContractError, Counts, ExpectedSeat,
    Manifest, Request, Result, SeatResult, decode, legacy_snapshot_summary,
    validate_runtime_versions, wire)
from checkpoint_fixtures import manifest, request, uid


def test_manifest_round_trip_and_four_unconfirmed_decisions(tmp_path):
    value = manifest(tmp_path)
    assert decode(Manifest, wire(value)) == value
    assert set(wire(value)['decisions']) == {'roster', 'unsafe_stop', 'backup_scope', 'placement'}
    assert all(v['basis'] == 'unconfirmed-recommendation' for v in wire(value)['decisions'].values())
    assert value.coordinator.mode == 'external-manual-return'
    assert value.coordinator.agent_id in [s.agent_id for s in value.expected]


@pytest.mark.parametrize('field,value', [('schema_version', 2), ('schema_version', True),
                                       ('state', 'ready'), ('environment', {'secret': 'no'})])
def test_closed_versioned_schema(tmp_path, field, value):
    payload = wire(manifest(tmp_path))
    payload[field] = value
    with pytest.raises(ContractError):
        decode(Manifest, payload)


def test_unknown_cli_and_runtime_version_refuse(tmp_path):
    m = manifest(tmp_path)
    payload = wire(m)
    payload['seats'][0]['cli'] = 'unknown-cli'
    with pytest.raises(ContractError):
        decode(Manifest, payload)
    with pytest.raises(ContractError):
        validate_runtime_versions(m, {'harness': {'other'}})
    validate_runtime_versions(m, {'harness': {'fixture-v1'}})


@pytest.mark.parametrize('change', ['missing', 'nondurable', 'stale', 'wrong-checkpoint', 'unsupported', 'blocked'])
def test_complete_requires_matching_durable_receipts(tmp_path, change):
    m = manifest(tmp_path)
    if change == 'missing':
        m = replace(m, receipts=m.receipts[:-1])
    elif change == 'blocked':
        m = replace(m, blockers=[Blocker('unsafe', uid(1), 'Reconcile uncertain work')])
    else:
        kwargs = {'nondurable': {'durable': False}, 'stale': {'source_revision': 'old'},
                  'wrong-checkpoint': {'checkpoint_id': uid(501)},
                  'unsupported': {'assurance': 'unsupported'}}[change]
        m = replace(m, receipts=[replace(m.receipts[0], **kwargs), *m.receipts[1:]])
    with pytest.raises(ContractError):
        wire(m)


def test_unresolved_expected_row_stays_in_incomplete_denominator(tmp_path):
    m = manifest(tmp_path, complete=False)
    missing = ExpectedSeat(uid(9), 'Offline-Unknown', 'explicit-offline')
    m = replace(m, expected=[*m.expected, missing], blockers=[Blocker('unknown-cli', uid(9), 'Resolve CLI')])
    assert len(decode(Manifest, wire(m)).expected) == 2
    with pytest.raises(ContractError):
        wire(replace(m, state='complete'))
    with pytest.raises(ContractError):
        wire(replace(m, blockers=[]))


def test_legacy_is_partial_not_new_manifest(tmp_path):
    assert legacy_snapshot_summary({'schema_version': 2, 'sessions': [{}]})['state'] == 'legacy-partial'
    with pytest.raises(ContractError):
        decode(Manifest, {'schema_version': 2, 'sessions': []})


@pytest.mark.parametrize('mutate', [
    {'request_id': None}, {'checkpoint_id': None}, {'operation_id': uid(3)},
    {'seat_ids': [uid(4)]}, {'dry_run': 'yes'}, {'action': 'launch'}, {'request_id': '../bad'},
])
def test_request_contract_refusals(mutate):
    payload = wire(request())
    payload.update(mutate)
    with pytest.raises(ContractError):
        decode(Request, payload)


def test_request_action_matrix_and_lookup():
    for value in (request(), request(action='save'), replace(request(), dry_run=True),
                  Request(1, 'status', None, None, None, [], False),
                  Request(1, 'status', uid(7), None, None, [], False),
                  Request(1, 'cancel', uid(7), None, uid(8), [], False),
                  Request(1, 'retry', uid(7), None, uid(8), [uid(1)], False)):
        assert decode(Request, wire(value)) == value
    with pytest.raises(ContractError):
        wire(Request(1, 'cancel', uid(7), None, uid(8), [uid(1)], False))
    with pytest.raises(ContractError):
        wire(Request(1, 'retry', uid(7), None, uid(8), [], False))


def test_result_counts_are_derived_and_unknown_cannot_retry():
    rows = [SeatResult(uid(1), 'ready-held', [], [], ['inspect']),
            SeatResult(uid(2), 'unknown', [], [], ['reconcile'])]
    result = Result(1, uid(8), uid(500), 'running', Counts(2, 1, 1, 0, 1), rows, [], ['status'])
    assert decode(Result, wire(result)) == result
    with pytest.raises(ContractError):
        wire(replace(result, counts=Counts(1, 1, 0, 0, 0)))
    with pytest.raises(ContractError):
        wire(replace(result, state='complete'))
    with pytest.raises(ContractError):
        wire(replace(rows[1], allowed_next_actions=['retry']))


def test_no_missing_coordinator_or_duplicate_conversation(tmp_path):
    m = manifest(tmp_path)
    with pytest.raises(ContractError):
        wire(replace(m, coordinator=replace(m.coordinator, agent_id=uid(9))))
    second = replace(m.seats[0], name='Other', agent_id=uid(2))
    with pytest.raises(ContractError):
        wire(replace(m, expected=[*m.expected, ExpectedSeat(uid(2), 'Other', second.selection)],
                     seats=[*m.seats, second]))


def test_compaction_must_follow_handoff_and_precede_final_boundary(tmp_path):
    m = manifest(tmp_path)
    receipts = list(m.receipts)
    receipts[1] = replace(receipts[1], observed_at='2025-12-31T23:59:59+00:00')
    with pytest.raises(ContractError):
        wire(replace(m, receipts=receipts))


def test_owner_requires_preserved_profile_and_known_role(tmp_path):
    m = manifest(tmp_path)
    for role in ('owner', 'leader', 'unrecognized'):
        with pytest.raises(ContractError):
            wire(replace(m.seats[0], role=role))


def test_result_must_join_frozen_manifest_roster(tmp_path):
    from liteharness.checkpoint_contract import validate_result_roster
    m = manifest(tmp_path, complete=False)
    m = replace(m, expected=[*m.expected, ExpectedSeat(uid(9), 'Offline', 'explicit-offline')],
                blockers=[Blocker('missing', uid(9), 'Reconcile')])
    result = Result(1, uid(8), m.checkpoint_id, 'complete', Counts(1, 1, 0, 0, 0),
                    [SeatResult(uid(1), 'ready-idle', [], [], [])], [], [])
    with pytest.raises(ContractError):
        validate_result_roster(result, m)


def test_excluded_names_cannot_conflict_with_expected(tmp_path):
    from liteharness.checkpoint_contract import Exclusion
    m = manifest(tmp_path)
    with pytest.raises(ContractError):
        wire(replace(m, exclusions=[Exclusion(m.expected[0].name.lower(), uid(9), 'Not selected')]))
    with pytest.raises(ContractError):
        wire(Exclusion('../bad-name', uid(9), 'Not selected'))

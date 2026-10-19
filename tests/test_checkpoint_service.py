"""Durable operations: submit, status, cancel, retry, the detached worker, and the CLI front door."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import time

import pytest

from liteharness import checkpoint_cli, checkpoint_worker
from liteharness.checkpoint_contract import Request, wire
from liteharness.checkpoint_ops import OperationLog, make_result, read_params, seat_result
from liteharness.checkpoint_restore import RestoreParams
from liteharness.checkpoint_service import Service
from liteharness.checkpoint_store import CheckpointStore
from checkpoint_fixtures import uid
from test_checkpoint_restore import CID, COORD, manifest, seat

WORKTREE = Path(__file__).resolve().parent.parent


def restore_request(n=700, cid=CID):
    return Request(1, 'restore', uid(n), cid, None, [], False)


def params(root, cid=CID):
    return RestoreParams(str(root), COORD, cid).wire()


@pytest.fixture
def svc(tmp_path):
    root = tmp_path / 'harness'
    root.mkdir()
    store = CheckpointStore(root)
    store.publish(manifest(tmp_path, [seat(2, root=tmp_path)]), authorized_reference_roots=[tmp_path])
    launched = []

    def launcher(operation_id, harness_root, log_path):
        # The operation must already be durable when the worker starts.
        assert store.operation(operation_id) is not None
        launched.append(operation_id)
        return os.getpid()
    service = Service(store, root, launcher=launcher)
    service.launched = launched
    return service


# -- submit / idempotency / exclusivity ----------------------------------------------------------

def test_submit_persists_before_launch_and_acknowledges_with_an_operation_id(svc, tmp_path):
    result = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert result.state == 'accepted' and result.operation_id == svc.launched[0]
    assert result.counts.expected == 2 and read_params(svc.store, result.operation_id)['checkpoint_id'] == CID
    assert svc.status(Request(1, 'status', None, None, result.operation_id, [], False)).operation_id == result.operation_id


def test_same_request_id_returns_the_same_operation_and_launches_once(svc):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    again = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert again.operation_id == first.operation_id and len(svc.launched) == 1


def test_request_id_reused_for_another_payload_or_actor_is_refused(svc, tmp_path):
    svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    other = svc.submit(Request(1, 'restore', uid(700), CID, None, [], True), actor_id=COORD, params={})
    assert other.state == 'rejected' and other.blockers[0].code == 'request-conflict'
    stranger = svc.submit(restore_request(), actor_id=uid(55), params={})
    assert stranger.state == 'rejected'


def test_second_mutating_request_is_rejected_naming_the_active_operation(svc):
    first = svc.submit(restore_request(700), actor_id=COORD, params=params(svc.harness_root))
    second = svc.submit(restore_request(701), actor_id=COORD, params=params(svc.harness_root))
    assert second.state == 'rejected' and second.blockers[0].code == 'operation-active'
    assert first.operation_id in second.blockers[0].remedy and second.operation_id == first.operation_id
    assert len(svc.launched) == 1


def test_dead_worker_with_unfinished_state_is_unknown_never_running(svc):
    first = svc.submit(restore_request(700), actor_id=COORD, params=params(svc.harness_root))
    svc.worker_alive_probe = lambda pid, created: False
    log = OperationLog(svc.store, first.operation_id)
    log.event('plan', None, make_result(first.operation_id, CID, 'running',
                                        [seat_result(e.agent_id, 'preparing') for e in svc.store.read_manifest(CID).expected]))
    status = svc.status(Request(1, 'status', None, None, first.operation_id, [], False))
    assert status.state == 'unknown' and status.blockers[-1].code == 'worker-dead'
    # An unknown operation does not block the next one.
    assert svc.submit(restore_request(701), actor_id=COORD, params=params(svc.harness_root)).state == 'accepted'


def test_never_started_worker_is_unknown(svc):
    first = svc.submit(restore_request(700), actor_id=COORD, params=params(svc.harness_root))
    svc.worker_alive_probe = lambda pid, created: False
    assert svc.status(Request(1, 'status', None, None, first.operation_id, [], False)).state == 'unknown'


def test_status_by_request_id_by_checkpoint_and_unknown_selector(svc):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    by_request = svc.status(Request(1, 'status', uid(700), None, None, [], False))
    assert by_request.operation_id == first.operation_id
    by_checkpoint = svc.status(Request(1, 'status', None, CID, None, [], False))
    assert by_checkpoint.checkpoint_id == CID and by_checkpoint.operation_id is None
    assert svc.status(Request(1, 'status', uid(999), None, None, [], False)).state == 'rejected'


# -- cancel / retry ------------------------------------------------------------------------------------

def test_cancel_requests_cancellation_once_and_a_finished_operation_is_left_alone(svc):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    cancel = Request(1, 'cancel', uid(710), None, first.operation_id, [], False)
    assert svc.cancel(cancel).state == 'canceling'
    svc.cancel(cancel)
    stages = [e.stage for e in svc.store.operation(first.operation_id).events]
    assert stages.count('cancel-requested') == 1
    log = OperationLog(svc.store, first.operation_id)
    done = make_result(first.operation_id, CID, 'canceled', [seat_result(e.agent_id, 'preparing')
                                                            for e in svc.store.read_manifest(CID).expected])
    log.event('final', None, done)
    assert svc.cancel(cancel).state == 'canceled'


def finished_restore(svc, states):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    expected = svc.store.read_manifest(CID).expected
    rows = [seat_result(e.agent_id, states.get(e.agent_id, 'ready-idle')) for e in expected]
    OperationLog(svc.store, first.operation_id).event('final', None, make_result(first.operation_id, CID, 'incomplete', rows))
    return first


def test_retry_takes_only_reconciled_failed_rows(svc):
    first = finished_restore(svc, {uid(2): 'failed'})
    retry = lambda seats, rid=720: Request(1, 'retry', uid(rid), None, first.operation_id, seats, False)
    assert svc.retry(retry([COORD]), actor_id=COORD, reconciled=lambda s, w: True).blockers[0].code == 'not-retryable'
    assert svc.retry(retry([uid(2)]), actor_id=COORD, reconciled=lambda s, w: False).blockers[0].code == 'not-reconciled'
    accepted = svc.retry(retry([uid(2)], 721), actor_id=COORD, reconciled=lambda s, w: w == 'dead')
    assert accepted.state == 'accepted'
    params_ = read_params(svc.store, accepted.operation_id)
    assert (params_['kind'], params_['seat_ids'], params_['retry_of']) == ('restore', [uid(2)], first.operation_id)


def test_unknown_rows_need_reconciliation_not_retry(svc):
    first = finished_restore(svc, {uid(2): 'unknown'})
    result = svc.retry(Request(1, 'retry', uid(722), None, first.operation_id, [uid(2)], False), actor_id=COORD,
                       reconciled=lambda s, w: True)
    assert result.state == 'rejected' and 'reconcil' in result.blockers[0].remedy


# -- worker ----------------------------------------------------------------------------------------------

def test_worker_error_before_any_progress_is_a_terminal_refusal(svc):
    first = svc.submit(restore_request(), actor_id=COORD, params={'kind': 'mystery'})
    with pytest.raises(Exception):
        checkpoint_worker.run(first.operation_id, svc.harness_root)
    status = svc.status(Request(1, 'status', None, None, first.operation_id, [], False))
    assert status.state == 'rejected' and status.blockers[-1].code == 'worker-error'


def test_worker_error_mid_flight_marks_in_flight_seats_unknown(svc):
    first = svc.submit(restore_request(), actor_id=COORD, params={'kind': 'mystery'})
    rows = [seat_result(e.agent_id, 'starting' if e.agent_id == uid(2) else 'ready-idle')
            for e in svc.store.read_manifest(CID).expected]
    OperationLog(svc.store, first.operation_id).event('plan', None, make_result(first.operation_id, CID, 'running', rows))
    with pytest.raises(Exception):
        checkpoint_worker.run(first.operation_id, svc.harness_root)
    status = svc.status(Request(1, 'status', None, None, first.operation_id, [], False))
    assert status.state == 'incomplete' and status.blockers[-1].code == 'worker-error'
    assert {s.seat_id: s.state for s in status.seats} == {COORD: 'ready-idle', uid(2): 'unknown'}


def test_detached_worker_outlives_the_process_that_submitted_it(tmp_path):
    """A real subprocess submits and exits at once; a real detached worker finishes the restore."""
    root, home = tmp_path / 'harness', tmp_path / 'home'
    home.mkdir()
    root.mkdir()
    only_coordinator = manifest(tmp_path, [])
    CheckpointStore(root).publish(only_coordinator, authorized_reference_roots=[tmp_path])
    script = textwrap.dedent(f'''
        import json, sys
        from pathlib import Path
        from liteharness.checkpoint_contract import Request
        from liteharness.checkpoint_restore import RestoreParams
        from liteharness.checkpoint_service import Service
        from liteharness.checkpoint_store import CheckpointStore
        root = Path({str(root)!r})
        result = Service(CheckpointStore(root), root).submit(
            Request(1, 'restore', {uid(700)!r}, {CID!r}, None, [], False), actor_id={COORD!r},
            params=RestoreParams(str(root), {COORD!r}, {CID!r}).wire())
        print(result.state, result.operation_id)
    ''')
    env = {**os.environ, 'USERPROFILE': str(home), 'HOME': str(home), 'LITESUITE_BRIDGE_URL': 'http://127.0.0.1:9',
           'PYTHONPATH': str(WORKTREE)}
    submitted = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=60)
    assert submitted.returncode == 0, submitted.stderr
    state, operation_id = submitted.stdout.split()
    assert state == 'accepted'
    store = CheckpointStore(root)
    deadline = time.time() + 60
    while time.time() < deadline:
        status = Service(store, root).status(Request(1, 'status', None, None, operation_id, [], False))
        if status.state not in ('accepted', 'running'):
            break
        time.sleep(0.5)
    assert status.state == 'complete', (status.state, status.blockers)
    assert (root / 'sessions' / 'checkpoints' / 'logs' / f'{operation_id}.log').exists()


# -- CLI ---------------------------------------------------------------------------------------------------

def cli_args(*argv, root):
    return checkpoint_cli.build_parser().parse_args([*argv, '--harness-root', str(root)])


def test_json_output_is_exactly_the_wire_result(svc, capsys, monkeypatch):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    code = checkpoint_cli.main(['status', '--operation-id', first.operation_id, '--json', '--harness-root',
                                str(svc.harness_root)])
    printed = json.loads(capsys.readouterr().out)
    assert printed == wire(svc.status(Request(1, 'status', None, None, first.operation_id, [], False)))
    assert code == 0


def test_unauthorised_actor_is_refused_and_nothing_is_persisted(svc):
    def deny(actor, leaf, require):
        raise checkpoint_cli.Unauthorized('A live registry ORCHESTRATOR is required to change checkpoints')
    args = cli_args('restore', '--checkpoint-id', CID, '--actor-id', uid(55), root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=deny, service=svc)
    assert result.state == 'rejected' and result.blockers[0].code == 'unauthorized'
    assert svc.launched == [] and svc._operations() == []


def test_dry_run_needs_no_authority_launches_nothing_and_journals_nothing(svc):
    def must_not_be_asked(*a):
        raise AssertionError('a dry run must not need authorization')
    args = cli_args('restore', '--checkpoint-id', CID, '--dry-run', '--actor-id', COORD, root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=must_not_be_asked, service=svc)
    assert result.operation_id is None and result.state in ('accepted', 'incomplete')
    assert svc.launched == [] and svc._operations() == []


def test_restore_accepts_latest_and_submits_with_the_authorized_actor(svc):
    seen = {}
    args = cli_args('restore', '--checkpoint-id', 'latest', '--request-id', uid(730), '--actor-id', COORD,
                    root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: seen.update(actor=a) or a, service=svc)
    assert result.state == 'accepted' and result.checkpoint_id == CID and seen['actor'] == COORD
    assert svc.store.operation(result.operation_id).actor_id == COORD


def test_save_without_a_coordinator_handoff_is_an_invalid_request(svc):
    args = cli_args('save', '--actor-id', COORD, root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: a, service=svc)
    assert result.state == 'rejected' and result.blockers[0].code == 'invalid-request'


def test_partial_status_prints_not_ready_and_exits_nonzero(svc, capsys):
    first = finished_restore(svc, {uid(2): 'failed'})
    code = checkpoint_cli.main(['status', '--operation-id', first.operation_id, '--harness-root', str(svc.harness_root)])
    out = capsys.readouterr().out
    assert code == 2 and 'NOT READY' in out and 'INCOMPLETE' in out


def test_checkpoint_is_reachable_from_sessions_and_legacy_verbs_still_parse(svc, capsys):
    from liteharness import session_manager
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    with pytest.raises(SystemExit) as stop:
        session_manager.main(['checkpoint', 'status', '--operation-id', first.operation_id, '--json',
                              '--harness-root', str(svc.harness_root)])
    assert stop.value.code == 0 and json.loads(capsys.readouterr().out)['operation_id'] == first.operation_id
    assert session_manager.build_parser().parse_args(['list']).command == 'list'


# -- authority ------------------------------------------------------------------------------------------------

def test_authorize_refuses_a_non_orchestrator_row_without_trusting_the_leaf(tmp_path, monkeypatch):
    from liteharness import config
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    now = time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents' / f'{uid(60)}.json').write_text(json.dumps({
        'agent_id': uid(60), 'role': 'builder', 'last_seen': now, 'registered_at': now, 'leaf_id': 'leaf-1',
        'session_pid': os.getpid(), 'session_process_started_at': 1.0}))
    with pytest.raises(checkpoint_cli.Unauthorized):
        checkpoint_cli.authorize_orchestrator(uid(60), 'leaf-1', True)
    with pytest.raises(checkpoint_cli.Unauthorized):
        checkpoint_cli.authorize_orchestrator('not-an-id')
    with pytest.raises(checkpoint_cli.Unauthorized):
        checkpoint_cli.authorize_orchestrator(uid(61))


def test_authorize_requires_the_leaf_on_the_tool_path_and_it_must_match(tmp_path, monkeypatch):
    from liteharness import config, recovered_destination
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    monkeypatch.setattr(recovered_destination, 'issuer', lambda actor: {'identity': actor})
    (tmp_path / 'agents').mkdir()
    (tmp_path / 'agents' / f'{uid(60)}.json').write_text(json.dumps({'agent_id': uid(60), 'leaf_id': 'leaf-1'}))
    assert checkpoint_cli.authorize_orchestrator(uid(60)) == uid(60)
    assert checkpoint_cli.authorize_orchestrator(uid(60), 'leaf-1', True) == uid(60)
    with pytest.raises(checkpoint_cli.Unauthorized):
        checkpoint_cli.authorize_orchestrator(uid(60), None, True)
    with pytest.raises(checkpoint_cli.Unauthorized):
        checkpoint_cli.authorize_orchestrator(uid(60), 'someone-elses-leaf', True)

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
from liteharness.checkpoint_contract import ContractError, Request, wire
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


def test_unauthorised_actor_is_refused_and_nothing_is_persisted(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
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


def test_restore_accepts_latest_and_submits_with_the_authorized_actor(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
    seen = {}
    args = cli_args('restore', '--checkpoint-id', 'latest', '--request-id', uid(730), '--actor-id', COORD,
                    root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: seen.update(actor=a) or a, service=svc)
    assert result.state == 'accepted' and result.checkpoint_id == CID and seen['actor'] == COORD
    assert svc.store.operation(result.operation_id).actor_id == COORD


def test_save_without_a_coordinator_handoff_is_an_invalid_request(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
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


# -- critic round: H1, M1, L1, L2 ------------------------------------------------------------------------

@pytest.mark.parametrize('action,extra', [('cancel', ['--operation-id', uid(5)]),
                                          ('retry', ['--operation-id', uid(5), '--seat-id', uid(2)]),
                                          ('status', [])])
def test_dry_run_is_refused_for_actions_that_have_no_dry_run(svc, monkeypatch, action, extra):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
    asked = []
    args = cli_args(action, *extra, '--dry-run', '--actor-id', COORD, root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda *a: asked.append(a) or COORD, service=svc)
    assert result.state == 'rejected' and result.blockers[0].code == 'invalid-request'
    assert 'dry-run' in result.blockers[0].remedy and asked == []
    assert svc.launched == [] and svc._operations() == []


def test_retry_with_dry_run_and_a_live_orchestrator_but_no_leaf_launches_nothing(svc, monkeypatch):
    monkeypatch.delenv('LITESUITE_LEAF_ID', raising=False)
    first = finished_restore(svc, {uid(2): 'failed'})
    before = len(svc.launched)
    args = cli_args('retry', '--operation-id', first.operation_id, '--seat-id', uid(2), '--dry-run',
                    '--actor-id', COORD, root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: a, service=svc)
    assert result.state == 'rejected' and len(svc.launched) == before


def test_actor_and_leaf_flags_are_honoured_only_when_the_bridge_set_a_leaf(svc, monkeypatch):
    from liteharness import config
    monkeypatch.setattr(config, 'get_agent_id', lambda: uid(70))
    asked = []

    def ask(actor, leaf, require):
        asked.append((actor, leaf, require))
        return actor
    args = cli_args('restore', '--checkpoint-id', CID, '--actor-id', uid(55), '--leaf-id', 'leaf-9', '--require-leaf',
                    root=svc.harness_root)
    monkeypatch.delenv('LITESUITE_LEAF_ID', raising=False)
    checkpoint_cli.run(args, authorize=ask, service=svc)
    assert asked == [(uid(70), None, False)], 'a typed actor id is ignored outside the tool path'
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-9')
    asked.clear()
    checkpoint_cli.run(cli_args('restore', '--checkpoint-id', CID, '--actor-id', uid(55), '--leaf-id', 'leaf-9',
                                '--request-id', uid(741), '--require-leaf', root=svc.harness_root),
                       authorize=ask, service=svc)
    assert asked == [(uid(55), 'leaf-9', True)]


def test_a_leaf_flag_that_differs_from_the_bridge_leaf_is_refused(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-9')
    args = cli_args('restore', '--checkpoint-id', CID, '--actor-id', COORD, '--leaf-id', 'someone-elses', '--require-leaf',
                    root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: a, service=svc)
    assert result.state == 'rejected' and result.blockers[0].code == 'unauthorized'
    assert svc.launched == []


def test_canceled_with_unfinished_seats_prints_not_ready_and_exits_nonzero(svc, capsys):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    svc.cancel(Request(1, 'cancel', uid(711), None, first.operation_id, [], False))
    log = OperationLog(svc.store, first.operation_id)
    rows = [seat_result(e.agent_id, 'preparing') for e in svc.store.read_manifest(CID).expected]
    log.event('final', None, make_result(first.operation_id, CID, 'canceled', rows))
    code = checkpoint_cli.main(['status', '--operation-id', first.operation_id, '--harness-root', str(svc.harness_root)])
    assert code == 2 and 'NOT READY' in capsys.readouterr().out


def test_cancel_that_the_journal_refuses_is_a_typed_result_not_a_traceback(svc, monkeypatch):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))

    def refuse(*a, **k):
        raise ContractError('Journal sequence changed; reconcile before append')
    monkeypatch.setattr(svc.store, 'append', refuse)
    result = svc.cancel(Request(1, 'cancel', uid(712), None, first.operation_id, [], False))
    assert result.state == 'rejected' and result.blockers[0].code == 'cancel-refused'


# -- critic round: M2 duplicate windows ---------------------------------------------------------------------

def test_admission_check_runs_under_the_store_lock_and_refusal_creates_nothing(svc):
    seen = {}

    def admit():
        seen['locked'] = (svc.store.root / '.writer-lock').exists()
        return 'busy elsewhere'
    from liteharness.checkpoint_store import AdmissionRefused
    with pytest.raises(AdmissionRefused, match='busy elsewhere'):
        svc.store.submit(restore_request(), actor_id=COORD, admit=admit)
    assert seen == {'locked': True} and svc._operations() == []


def test_a_worker_that_has_not_written_its_record_yet_counts_as_active_within_the_grace(svc, monkeypatch):
    def slow_launcher(operation_id, root, log):
        # The submit-time record exists before the process does, with no pid yet.
        record = json.loads(svc._worker_path(operation_id).read_text())
        assert record['pid'] is None
        return os.getpid()
    svc.launcher = slow_launcher
    first = svc.submit(restore_request(700), actor_id=COORD, params=params(svc.harness_root))
    record = svc._worker_path(first.operation_id)
    record.write_text(json.dumps({'pid': None, 'created': None, 'submitted': svc.now()}))
    assert svc.worker_alive(first.operation_id) is True
    record.write_text(json.dumps({'pid': None, 'created': None, 'submitted': svc.now() - 3600}))
    assert svc.worker_alive(first.operation_id) is False


def test_a_failure_recording_the_worker_after_it_started_is_not_reported_as_not_started(svc, monkeypatch):
    original = svc._record_worker
    calls = []

    def flaky(operation_id, pid):
        calls.append(pid)
        if pid is not None:
            raise OSError('disk')
        return original(operation_id, pid)
    monkeypatch.setattr(svc, '_record_worker', flaky)
    result = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert result.state == 'accepted' and svc.launched, 'the worker ran; the record failure must not say otherwise'


def test_a_launcher_that_cannot_start_the_worker_says_so(svc):
    def broken(operation_id, root, log):
        raise OSError('cannot spawn')
    svc.launcher = broken
    result = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert result.state == 'unknown' and result.blockers[0].code == 'worker-not-started'


@pytest.mark.skipif(os.name != 'nt', reason='Windows console flag')
def test_child_processes_of_the_worker_are_started_without_a_console_window(monkeypatch):
    from liteharness import checkpoint_restore, checkpoint_save
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, '', '')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    checkpoint_restore._spawn(['x'], {}, 5)
    checkpoint_save._run(['y'], 5)
    assert [k.get('creationflags') for k in seen] == [subprocess.CREATE_NO_WINDOW] * 2


# -- critic round 2: N4 lock backoff, N5 worker self-record, N7 remedy ---------------------------------------

def test_journal_append_retries_while_the_store_is_locked_then_succeeds(svc, monkeypatch):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    real = svc.store.append
    attempts = []

    def flaky(operation_id, event, *, expected_sequence):
        attempts.append(expected_sequence)
        if len(attempts) < 3:
            raise ContractError('Checkpoint store locked; reconcile owner before retry')
        return real(operation_id, event, expected_sequence=expected_sequence)
    monkeypatch.setattr(svc.store, 'append', flaky)
    naps = []
    OperationLog(svc.store, first.operation_id, sleep=naps.append).event('plan', None, None)
    assert len(attempts) == 3 and len(naps) == 2 and svc.store.operation(first.operation_id).events[0].stage == 'plan'


def test_journal_append_gives_up_eventually_and_other_errors_are_not_retried(svc, monkeypatch):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    calls = []

    def always_locked(*a, **k):
        calls.append(1)
        raise ContractError('Checkpoint store locked; reconcile owner before retry')
    monkeypatch.setattr(svc.store, 'append', always_locked)
    with pytest.raises(ContractError, match='locked'):
        OperationLog(svc.store, first.operation_id, sleep=lambda s: None).event('plan', None, None)
    assert len(calls) > 3
    calls.clear()
    monkeypatch.setattr(svc.store, 'append', lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(ContractError('Unknown operation')))
    with pytest.raises(ContractError, match='Unknown'):
        OperationLog(svc.store, first.operation_id, sleep=lambda s: None).event('plan', None, None)
    assert len(calls) == 1


def test_the_worker_records_its_own_pid_so_a_swallowed_submit_side_write_cannot_orphan_it(svc, monkeypatch):
    original = svc._record_worker

    def flaky(operation_id, pid):
        if pid is not None:
            raise OSError('disk')
        return original(operation_id, pid)
    monkeypatch.setattr(svc, '_record_worker', flaky)
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    path = svc._worker_path(first.operation_id)
    path.write_text(json.dumps({'pid': None, 'created': None, 'submitted': svc.now() - 3600}))  # grace long over
    assert svc.worker_alive(first.operation_id) is False
    checkpoint_worker.register_self(svc.store, first.operation_id)  # what the worker does first thing
    assert svc.worker_alive(first.operation_id) is True
    assert json.loads(path.read_text())['pid'] == os.getpid()


def test_worker_not_started_tells_the_caller_to_use_a_new_request_id(svc):
    def broken(operation_id, root, log):
        raise OSError('cannot spawn')
    svc.launcher = broken
    result = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert 'NEW request_id' in result.blockers[0].remedy and 'same request_id' not in result.blockers[0].remedy
    again = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    assert again.operation_id == result.operation_id, 'the same request_id returns the same unstarted operation'


def test_a_sequence_collision_is_retried_because_the_sequence_is_reread_each_attempt(svc, monkeypatch):
    first = svc.submit(restore_request(), actor_id=COORD, params=params(svc.harness_root))
    real = svc.store.append
    seen = []

    def racing(operation_id, event, *, expected_sequence):
        seen.append(expected_sequence)
        if len(seen) == 1:  # another writer slipped an event in between our read and our append
            real(operation_id, type(event)(expected_sequence, 'other-writer', None, None), expected_sequence=expected_sequence)
            raise ContractError('Journal sequence changed; reconcile before append')
        return real(operation_id, event, expected_sequence=expected_sequence)
    monkeypatch.setattr(svc.store, 'append', racing)
    OperationLog(svc.store, first.operation_id, sleep=lambda s: None).event('plan', None, None)
    stages = [e.stage for e in svc.store.operation(first.operation_id).events]
    assert stages == ['other-writer', 'plan'] and seen == [0, 1]


# -- round 5: F4 an operator reads names, not ids ---------------------------------------------------------------

def test_human_output_names_each_seat_with_role_cli_model_and_pane_while_json_is_unchanged(svc, capsys, monkeypatch):
    monkeypatch.setattr('liteharness.checkpoint_restore._model_known', lambda agent_id: True)
    code = checkpoint_cli.main(['restore', '--checkpoint-id', CID, '--dry-run', '--actor-id', COORD,
                                '--harness-root', str(svc.harness_root)])
    text = capsys.readouterr().out
    row = next(line for line in text.splitlines() if 'Seat-2' in line)
    assert 'builder' in row and 'claude' in row and 'claude-x' in row and ('preparing' in row or 'starting' in row)
    assert uid(2) not in row, 'the id is not the label'
    capsys.readouterr()
    checkpoint_cli.main(['restore', '--checkpoint-id', CID, '--dry-run', '--actor-id', COORD, '--json',
                         '--harness-root', str(svc.harness_root)])
    printed = json.loads(capsys.readouterr().out)
    assert 'Seat-2' not in json.dumps(printed) and printed['seats'][0]['seat_id']


def test_the_label_includes_the_saved_pane_and_the_blocker_text(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr('liteharness.checkpoint_restore._model_known', lambda agent_id: True)
    root = tmp_path / 'harness'
    root.mkdir()
    store = CheckpointStore(root)
    store.publish(manifest(tmp_path, [seat(2, root=tmp_path, pane='pane-9')], unresolved=[7]), authorized_reference_roots=[tmp_path])
    checkpoint_cli.main(['restore', '--checkpoint-id', CID, '--dry-run', '--actor-id', COORD, '--harness-root', str(root)])
    text = capsys.readouterr().out
    assert 'pane-9' in next(line for line in text.splitlines() if 'Seat-2' in line)
    unresolved = next(line for line in text.splitlines() if 'Seat-7' in line)
    assert 'unresolved-in-checkpoint' in unresolved


def test_a_save_dry_run_labels_seats_from_the_registry(tmp_path, monkeypatch, capsys):
    from liteharness import config
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    from checkpoint_world import World
    w = World(tmp_path, monkeypatch)
    w.harness = tmp_path
    agent = w.claude(5, role='builder')
    monkeypatch.setattr('liteharness.checkpoint_roster._live', lambda row: True)
    checkpoint_cli.main(['save', '--dry-run', '--harness-root', str(tmp_path)])
    text = capsys.readouterr().out
    row = next(line for line in text.splitlines() if 'Claude-5' in line)
    assert 'pane-5' in row and 'claude' in row


def test_allow_default_model_is_restore_only_and_never_a_dry_run_flag(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
    for argv in (['save', '--allow-default-model', '--coordinator-handoff', str(svc.harness_root / 'h.md')],
                 ['status', '--allow-default-model'],
                 ['restore', '--checkpoint-id', CID, '--dry-run', '--allow-default-model']):
        result = checkpoint_cli.run(cli_args(*argv, '--actor-id', COORD, root=svc.harness_root),
                                    authorize=lambda a, l, r: a, service=svc)
        assert result.state == 'rejected' and result.blockers[0].code == 'invalid-request'
        assert 'allow-default-model' in result.blockers[0].remedy


def test_allow_default_model_reaches_the_restore_params(svc, monkeypatch):
    monkeypatch.setenv('LITESUITE_LEAF_ID', 'leaf-1')
    args = cli_args('restore', '--checkpoint-id', CID, '--allow-default-model', '--request-id', uid(750),
                    '--actor-id', COORD, root=svc.harness_root)
    result = checkpoint_cli.run(args, authorize=lambda a, l, r: a, service=svc)
    assert result.state == 'accepted' and read_params(svc.store, result.operation_id)['allow_default_model'] is True


def test_save_dry_run_shows_the_resolved_model_and_its_source_not_the_registry_value(tmp_path, monkeypatch, capsys):
    from liteharness import config
    monkeypatch.setattr(config, 'get_root', lambda: tmp_path)
    from checkpoint_world import World
    w = World(tmp_path, monkeypatch)
    w.harness = tmp_path
    unknown, real = w.claude(5), w.claude(6)
    row_path = tmp_path / 'agents' / f'{unknown}.json'
    row = json.loads(row_path.read_text())
    row['model'] = 'unknown'
    row_path.write_text(json.dumps(row))
    transcript = next(w.projects.glob(f'*/{unknown}.jsonl'))
    with transcript.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'assistant', 'message': {'model': 'claude-opus-5-5'}}) + chr(10))
    monkeypatch.setattr('liteharness.checkpoint_roster._live', lambda row: True)
    checkpoint_cli.main(['save', '--dry-run', '--harness-root', str(tmp_path)])
    text = capsys.readouterr().out
    assert 'claude-opus-5-5 (transcript)' in next(line for line in text.splitlines() if 'Claude-5' in line)
    six = next(line for line in text.splitlines() if 'Claude-6' in line)
    assert 'claude-x (registry)' in six and 'claude  unknown' not in text

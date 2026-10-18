"""Real immutable LiteTUI CLI receiver; only terminal/backend readiness is adapted.

Invoked by test_owner_resume_contract in a separate synthetic environment. Never
starts a provider, native session, live harness transport or real terminal loop.
Receipts contain source bindings, hashes and booleans, not SYSTEM/home/lore text.
"""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


def main():
    phase, state_path, output_path, *argv = sys.argv[1:]
    state_file, output_file = Path(state_path), Path(output_path)
    expected_src = Path(os.environ['LITETUI_CONSUMER_SRC']).resolve()
    from litetui import app as app_mod
    from litetui import cli, image_viewer, owner_mode

    assert Path(cli.__file__).resolve() == expected_src / 'litetui' / 'cli.py'
    assert Path(app_mod.__file__).resolve() == expected_src / 'litetui' / 'app.py'
    receipt = {'phase': phase, 'executable': sys.executable,
               'consumer_cli': str(Path(cli.__file__).resolve()),
               'consumer_app': str(Path(app_mod.__file__).resolve()),
               'consumer_app_sha256': hashlib.sha256(Path(app_mod.__file__).read_bytes()).hexdigest(),
               'argv': argv}

    def capture(app, **_kwargs):
        session = app._agent_session
        assert session.authority.agent_id == '11111111-1111-4111-8111-111111111111'
        assert session.authority.name == 'Alpha'
        if phase == 'seed':
            app._materialise_convo()
            messages = app._request_messages()
            first = app.conversation[0]
            suffix = first['content'][len(first['litetui_prompt_base']):]
            assert len([m for m in messages if m['role'] == 'system']) == 1
            app._append({'role': 'user', 'content': 'SYNTHETIC PRIOR USER HISTORY'})
            state_file.write_text(json.dumps({'cid': app.convo_id, 'home': str(session.memory_root),
                                             'suffix_sha256': hashlib.sha256(suffix.encode('utf-8')).hexdigest()}),
                                  encoding='utf-8')
            receipt['seeded_owned_conversation'] = True
            return
        assert phase == 'resume'
        state = json.loads(state_file.read_text(encoding='utf-8'))
        assert str(session.memory_root) == state['home']
        assert app._cli_convo_id == state['cid']
        rows = [json.loads(line) for line in (session.memory_root / 'prompts' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        applied = [row for row in rows if row['phase'] == 'applied' and row['name'] == 'role.md']
        assert len(applied) == 1
        assert applied[0]['who'] == '33333333-3333-4333-8333-333333333333'
        assert applied[0]['actor_provenance']['route'] == 'owned-launch-role'
        assert applied[0]['convo'] == state['cid']
        app._system = lambda *_args, **_kw: None
        app._render_resumed = lambda *_args, **_kw: None
        # Real on_mount ordering: startup resume before external readiness, then
        # the actual CLI-argument path. No ownership/loading/composer is mocked.
        assert app._resume_cli_conversation()
        app._connect_settled = True
        app._connect_done.set()
        app._gui_connection_success = True
        app._cli_initial_model = None
        app._cli_thinking_level = None

        async def registered():
            return True

        app._register_owned_startup = registered
        asyncio.run(app_mod.LiteTUI._apply_cli_args.__wrapped__(app))
        assert not app._cli_launch_error, app._cli_launch_error
        assert app.convo_id == state['cid'] and app.store.owned
        messages = app._request_messages()
        assert len([m for m in messages if m['role'] == 'system']) == 1
        first = app.conversation[0]
        text = messages[0]['content']
        suffix = first['content'][len(first['litetui_prompt_base']):]
        assert hashlib.sha256(suffix.encode('utf-8')).hexdigest() == state['suffix_sha256']
        assert {'role': 'user', 'content': 'SYNTHETIC PRIOR USER HISTORY'} in messages
        assert text.count('# OWNER ROLE \u2014 Owner Mode') == 1
        assert text.count('# POLYMATHIC LINUS') == 1
        assert text.count('NEW SYNTHETIC CALLER BODY') == 1
        assert 'OLD SYNTHETIC DEFAULT BODY' not in text
        receipt.update(same_owned_home=True, requested_old_cid_active=True, history_preserved=True,
                       frozen_suffix_preserved=True, audit_before_request=True,
                       system_messages=1, actual_owner_headers=1, selected_method_headers=1, custom_body_count=1)

    # These are unrelated external executable/terminal doors, not lease/store or
    # CLI ownership checks. All input parsing and resume publication are real.
    with patch.object(app_mod.LiteTUI, 'run', capture), patch.object(image_viewer, 'init_image_backend'), patch.object(owner_mode, 'require_handoff'):
        sys.argv = ['litetui', *argv]
        cli.main()
    output_file.write_text(json.dumps(receipt, ensure_ascii=True), encoding='utf-8')


if __name__ == '__main__':
    main()

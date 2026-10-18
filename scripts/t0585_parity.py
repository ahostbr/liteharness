"""Actual legacy CLI parity on one isolated corpus and identical DB snapshot.

python scripts/t0585_parity.py --baseline-ref c769c6045
--negative-control corrupts a known expected ranking and MUST exit 1.
No live DB/config/model is read. No weights are downloaded or loaded.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REL = Path('liteharness/catalog/skills/ls-conversation-lookup')
QUERIES = [
    ['--search', 'amber'], ['--search', 'canary'], ['--search', 'shared phrase'],
    ['--search', 'quartz'], ['--search', 'cedar'], ['--search', 'amber', '--project', 'demo'],
    ['--search', 'canary', '--type', 'user'], ['--search', 'quartz', '--mode', 'all'],
    ['--search', 'cedar', '--packet'], ['--search', 'shared phrase', '--exact-first'],
]


def run(script, args, env):
    process = subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)
    return {'exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr,
            'ids': re.findall(r'ConvID:.*?\(([^)]+)\)|"conversation_id": "([^"]+)"|^#\d+ \[\w+\] (\S+)', process.stdout, re.MULTILINE)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref', default='c769c6045')
    parser.add_argument('--negative-control', action='store_true')
    parser.add_argument('--timing-samples', type=int, default=0, help='Alternating no-listener CLI timing samples; no models loaded')
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='t0585-parity-') as temporary:
        root = Path(temporary)
        baseline = root / 'baseline'
        baseline.mkdir()
        for name in ('find_conversation.py', 'conversation_sources.py', 'lookup_evidence.py', 'embedding_backend.py', 'ruling_index.py'):
            content = subprocess.run(['git', 'show', f'{options.baseline_ref}:{(REL / name).as_posix()}'], cwd=ROOT, capture_output=True, check=True).stdout
            (baseline / name).write_bytes(content)
        env = {**os.environ, 'LITEHARNESS_CONVO_HOME': str(root / 'index'), 'CLAUDE_CONFIG_DIR': str(root / 'claude'), 'CODEX_HOME': str(root / 'codex'), 'PYTHONPATH': str(ROOT), 'PYTHONIOENCODING': 'utf-8'}
        for index, text in enumerate(('amber canary shared phrase', 'quartz cedar shared phrase', 'amber cedar', 'canary quartz', 'amber canary quartz cedar')):
            path = root / f'claude/projects/demo/session-{index}.jsonl'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'type': 'user', 'uuid': f'msg-{index}', 'timestamp': '2026-01-01T00:00:00Z', 'message': {'content': text}}) + '\n', encoding='utf-8')
        memory = root / 'claude/projects/demo/memory/note.md'
        memory.parent.mkdir()
        memory.write_text('quartz cedar fixture memory', encoding='utf-8')
        for args in (['--index'], ['--index-memory']):
            result = run(baseline / 'find_conversation.py', args, env)
            if result['exit']:
                print(json.dumps({'baselineIndexFailure': result}))
                return 2
        before = [run(baseline / 'find_conversation.py', [*query, '-n', '5', '--no-refresh'], env) for query in QUERIES]
        if options.negative_control:
            assert before[0]['ids'], 'Positive control: baseline must find known amber sessions'
            before[0]['ids'][0] = ('known-corrupted-ranking', '', '')
        after = [run(ROOT / REL / 'find_conversation.py', [*query, '-n', '5', '--no-refresh'], env) for query in QUERIES]
        mismatches = [index for index, (old, new) in enumerate(zip(before, after)) if old != new]
        print(json.dumps({'queries': len(QUERIES), 'baseline': options.baseline_ref,
                          'sameCorpusAndDb': True, 'negativeControl': options.negative_control,
                          'mismatches': mismatches, 'compared': ['top5 IDs/order', 'stdout', 'stderr', 'exit']}))
        if mismatches and not options.negative_control:
            for index in mismatches:
                print(json.dumps({'query': QUERIES[index], 'before': before[index], 'after': after[index]}))
        timing_failed = False
        if options.timing_samples:
            import statistics
            import time
            if not 5 <= options.timing_samples <= 100:
                parser.error('--timing-samples must be 5..100')
            timings = {'baseline': [], 'candidate': []}
            for sample in range(options.timing_samples):
                order = [('baseline', baseline / 'find_conversation.py'), ('candidate', ROOT / REL / 'find_conversation.py')]
                for name, script in (order if sample % 2 else reversed(order)):
                    start = time.perf_counter()
                    result = run(script, ['--search', 'amber', '-n', '5', '--no-refresh'], env)
                    assert result['exit'] == 0
                    timings[name].append((time.perf_counter() - start) * 1000)
            medians = {key: statistics.median(values) for key, values in timings.items()}
            ratio = medians['candidate'] / medians['baseline']
            timing_failed = not .9 <= ratio <= 1.1
            print(json.dumps({'noListenerTiming': 'measured', 'samples': options.timing_samples, 'allSamplesMs': timings, 'medianMs': medians, 'rangeMs': {key: [min(values), max(values)] for key, values in timings.items()}, 'ratio': ratio, 'within10Percent': not timing_failed, 'order': 'paired, alternating candidate/baseline then baseline/candidate', 'warmup': '10 parity queries per variant before timing; no additional warmup', 'conditions': 'same fixture/corpus/DB/options; new interpreter each sample; active-machine load uncontrolled', 'scope': 'isolated tiny keyword fixture including process startup; not a large live-corpus benchmark'}))
        return int(bool(mismatches) or timing_failed)


if __name__ == '__main__':
    sys.exit(main())

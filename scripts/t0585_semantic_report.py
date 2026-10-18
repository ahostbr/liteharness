"""Reproducible optional-backend report. Default inspects artifacts, loads nothing.

--measure-existing explicitly opts into measuring an ALREADY PRESENT CPU model.
Never downloads a model. GPU timings require a separately authorized proxy run.
"""
import argparse
import importlib.metadata
import json
from pathlib import Path
import time


def artifact(path):
    if not path or not path.is_dir():
        return {'state': 'unmeasured', 'artifactBytes': None, 'downloadBytes': None}
    files = [p for p in path.rglob('*') if p.is_file()]
    return {'state': 'measured-artifacts-only', 'artifactBytes': sum(p.stat().st_size for p in files),
            'downloadBytes': None, 'members': [{'name': str(p.relative_to(path)), 'bytes': p.stat().st_size} for p in sorted(files)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-artifacts', type=Path)
    parser.add_argument('--gpu-artifacts', type=Path)
    parser.add_argument('--measure-existing', action='store_true')
    options = parser.parse_args()
    versions = {}
    for package in ('liteharness', 'onnxruntime', 'tokenizers', 'numpy'):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = 'not-installed'
    cpu = {'candidate': 'all-MiniLM-L6-v2 / ONNX CPU', 'implementation': 'liteharness.embed_service.EmbedModel',
           'sharedBy': ['canonical conversation lookup', 'legacy desktop CPU service'],
           'artifacts': artifact(options.cpu_artifacts), 'rssBytes': None, 'firstQueryMs': None,
           'measurement': 'unmeasured', 'versions': versions}
    if options.measure_existing:
        if not options.cpu_artifacts or not (options.cpu_artifacts / 'onnx/model.onnx').is_file() or not (options.cpu_artifacts / 'tokenizer.json').is_file():
            parser.error('Measurement requires existing CPU model artifacts; this command never downloads')
        import psutil
        from liteharness.embed_service import EmbedModel
        start = time.perf_counter()
        model = EmbedModel(options.cpu_artifacts)
        model.encode(['isolated first-query fixture'])
        cpu.update(firstQueryMs=(time.perf_counter() - start) * 1000,
                   rssBytes=psutil.Process().memory_info().rss, measurement='measured',
                   conditions='Current process RSS after cold model construction + first query; no download; query includes model startup')
    gpu = {'candidate': 'GPU GGUF proxy', 'implementation': 'llama-server plus GGUF model',
           'artifacts': artifact(options.gpu_artifacts), 'rssBytes': None, 'firstQueryMs': None,
           'measurement': 'unmeasured', 'conditions': 'Must count llama-server binary, process RSS and GGUF; no proxy is launched by this report'}
    print(json.dumps({'version': 1, 'candidates': [cpu, gpu], 'supportedPath': 'cpu',
                      'basis': 'Reuse one existing ONNX implementation without a second daemon/GGUF stack. Runtime/download comparison is unmeasured, not a measured performance win.'}, indent=2))


if __name__ == '__main__':
    main()

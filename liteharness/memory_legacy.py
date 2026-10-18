"""Installed isolated entrypoint for the unchanged human conversation CLI."""
import json
from pathlib import Path
import sys


def main():
    from liteharness.memory_lookup import engine
    from liteharness.memory_semantic import paths, validate_child
    api = engine()  # Resolves shipped sibling adapters, never cwd/PYTHONPATH.
    _, owned, marker = paths()
    if Path(sys.executable).resolve() == owned.resolve():
        record = json.loads(marker.read_text(encoding='utf-8'))
        validate_child(record['base'])
    return api.main()


if __name__ == '__main__':
    main()

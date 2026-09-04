#!/usr/bin/env python3
"""Legacy watcher filename routed through the single attached stdout consumer."""
from liteharness.cli_scripts.codex.liteharness_watcher_supervisor import main

if __name__ == "__main__":
    raise SystemExit(main())

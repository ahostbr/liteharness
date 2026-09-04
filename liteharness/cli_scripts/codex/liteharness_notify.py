#!/usr/bin/env python3
"""Legacy Codex notify hook: deliberately non-claiming and non-spawning.

Notify cannot provide an attached output channel. Starting a detached consumer
here would consume messages without delivering them to the agent. The agent
starts the stdout watcher from its own tool terminal instead.
"""

def main() -> int:
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

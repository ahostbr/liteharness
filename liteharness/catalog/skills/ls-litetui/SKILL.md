---
name: ls-litetui
description: >-
  Generate rich Textual TUI + Click CLI applications from any software.
  Wraps arbitrary software in a professional terminal interface with
  tabbed layout, sidebar, themes, and shared backend. Use when asked
  to create a TUI, terminal interface, or CLI wrapper for software.
---

# LiteTUI Skill — Generate TUI + CLI from Any Software

## Overview

LiteTUI generates a dual-output terminal application from any software: a
Textual TUI for interactive use and a Click CLI for automation, both powered
by a shared `core/` package. Build once, get two interfaces.

The framework provides:

- **AppShell** — tabbed layout scaffold with sidebar and header
- **FeatureSidebar** — status and metadata panel (always visible)
- **6 reusable components** — panels, logs, tables, command palette
- **4 built-in themes** — dark, light, high_contrast, brand
- **Generator CLI** — bootstrap a new app in one command

## Quick Start

The fastest path to a working TUI is the generator:

> **Requirements:** `uv` must be installed (`pip install uv`). LiteTUI must be installed in your environment (`uv pip install litetui` or install from source). Adjust the path below to wherever LiteTUI is installed.

```bash
cd <path-to-litetui-install>
uv run litetui generate --name <software> --output <path> --description "<one-line description>"
```

Example:

```bash
uv run litetui generate --name ffmpeg --output ./output --description "FFmpeg video processing TUI"
```

This scaffolds the full directory tree, wires up AppShell with FeatureSidebar,
and generates a working `_tui.py` + `_cli.py`. Then customize from there.

## The 7-Phase Pipeline

Follow phases in order. Full details in `harness/TUI-HARNESS.md` (part of the LiteTUI source repo — not bundled with this skill).

### Phase 0: Setup

Verify the toolchain and install LiteTUI:

> **Requirements:** Python >= 3.12, `uv`, and `textual` must be available. Install with: `pip install uv && uv pip install litetui textual-dev`. If working from source, `cd <path-to-litetui-install>` first.

```bash
python --version          # Must be >= 3.12
uv --version
textual --version

cd <path-to-litetui-install>
uv pip install -e .
uv pip install textual-dev
```

Initialize the project tree:

```bash
python -m litetui.generator.init <software>
```

Or let the generator handle it (see Quick Start above).

### Phase 1: Analyze

Read the target software's source, documentation, or API. Map:

- What are the key operations/commands?
- What state needs to persist across interactions?
- What output does it produce (logs, data, streams)?
- What's the natural grouping of features (tabs)?

Document findings in a `<SOFTWARE>.md` analysis file.

### Phase 2: Design

Choose layout and plan before writing code:

- **Tabs** — one per major feature group (aim for 3–6 tabs)
- **Sidebar content** — what status/metadata to show at all times
- **State model** — what lives in `core/session.py` vs. UI state
- **Component selection** — which LiteTUI components match each tab

### Phase 3: Implement

Generate or write the shared core and both entry points:

1. `core/backend.py` — finds the binary, invokes the real software
2. `core/session.py` — stateful session, undo/redo, shared state
3. `<software>_tui.py` — Textual App using LiteTUI components
4. `<software>_cli.py` — Click CLI calling the same `core/` functions

Import pattern:

```python
from litetui.components import AppShell, FeatureSidebar, StatusPanel, StreamingLog
from litetui.themes import load_theme
```

### Phase 4: Theme

Customize `themes/app.tcss` for the target software's personality. Pick a
base theme and extend it:

```python
from litetui.themes import load_theme
css = load_theme("dark")   # base, then append overrides
```

Available base themes: `dark`, `light`, `high_contrast`, `brand`.

### Phase 5: Test Plan

Write `tests/TEST.md` before running tests:

- List every feature to verify
- Define pass/fail criteria
- Note edge cases and failure modes

### Phase 6: Test

Write and run tests in three layers:

```bash
pytest tests/test_core.py      # Unit tests — no external deps
pytest tests/test_tui.py       # Textual pilot tests
pytest tests/test_cli.py       # CLI subprocess tests
```

All three test files are required.

### Phase 7: Document and Publish

1. Auto-generate `skills/SKILL.md` from the app's capabilities
2. Update `README.md` with install and run instructions
3. Make the package pip-installable via `setup.py`

```bash
pip install -e .
```

## Component Library

Use these LiteTUI components inside your Textual App. Import from
`litetui.components`.

### AppShell — Main Scaffold

**Always use.** Provides the outer frame: header, tabbed content area, footer.
Every LiteTUI app starts with AppShell.

```python
from litetui.components import AppShell

class MyApp(AppShell):
    TITLE = "My Software TUI"
```

### FeatureSidebar — Status Metadata Sidebar

**Always use.** A persistent sidebar showing live status, context, and
metadata. Sits alongside the main tabbed area and is always visible.

Use for: connection status, current file/project, resource usage, quick stats.

```python
from litetui.components import FeatureSidebar

sidebar = FeatureSidebar(title="Status")
```

### StatusPanel — Key-Value Live Display

Use for monitoring screens where multiple metrics need to update in real time.
Renders a clean grid of label: value pairs that refresh on data change.

Best for: server stats, process metrics, live configuration display.

```python
from litetui.components import StatusPanel

panel = StatusPanel(data={"CPU": "12%", "RAM": "4.2 GB"})
```

### StreamingLog — Log Tailing

Use for real-time output from long-running processes. Handles ANSI colors,
auto-scrolls to bottom, and supports pause/resume.

Best for: build output, process logs, streaming command output.

```python
from litetui.components import StreamingLog

log = StreamingLog()
log.append_line("[green]Build started[/green]")
```

### DataExplorer — Data Tables

Use for listing and browsing tabular data. Supports sorting, filtering, row
selection, and custom column definitions.

Best for: file listings, database records, API result sets, package lists.

```python
from litetui.components import DataExplorer

explorer = DataExplorer(columns=["Name", "Size", "Modified"])
```

### CommandPalette — Slash Commands

Use for power-user features accessed via a slash command interface. Fuzzy-
searchable list of actions, triggered by `/` keypress.

Best for: editors, project tools, any app with many discrete actions.

```python
from litetui.components import CommandPalette
```

## Themes

Four built-in themes. Pass by name to `load_theme()`:

| Theme           | When to Use                                                 |
| --------------- | ----------------------------------------------------------- |
| `dark`          | Default. Professional dark background, good for most tools. |
| `light`         | Bright environments or user preference.                     |
| `high_contrast` | Accessibility. Maximum legibility.                          |
| `brand`         | Custom branded apps. Accent color is configurable.          |

```python
from litetui.themes import load_theme, AVAILABLE_THEMES
# AVAILABLE_THEMES = ["dark", "light", "high_contrast", "brand"]
css = load_theme("dark")
```

## Output Structure

After generation, the project layout is:

```
<output>/
├── setup.py                        # pip-installable package
├── tui_anything/
│   └── <software>/
│       ├── __init__.py
│       ├── __main__.py             # python -m tui_anything.<software>
│       ├── README.md
│       ├── <software>_tui.py      # Textual TUI entry point
│       ├── <software>_cli.py      # Click CLI entry point
│       ├── core/
│       │   ├── __init__.py
│       │   ├── backend.py         # Finds binary, invokes software
│       │   └── session.py         # Stateful session, shared state
│       ├── tui/
│       │   ├── __init__.py
│       │   ├── app.py             # Textual App subclass
│       │   ├── screens/           # One screen per major workflow
│       │   └── widgets/           # Custom widgets (extends LiteTUI)
│       ├── cli/
│       │   ├── __init__.py
│       │   └── commands/          # One module per command group
│       ├── themes/
│       │   └── app.tcss           # Textual CSS (extends base theme)
│       ├── skills/
│       │   └── SKILL.md           # Agent skill definition (auto-generated)
│       └── tests/
│           ├── TEST.md            # Test plan + results
│           ├── test_core.py
│           ├── test_tui.py
│           └── test_cli.py
```

Note: `tui_anything/` has no `__init__.py` — it is a PEP 420 namespace package
so independently-installed sub-packages can coexist without conflicts.

## For the Full Methodology

See `harness/TUI-HARNESS.md` in the LiteTUI source repo for the complete SOP including:

- Detailed per-phase instructions and checklists
- Widget implementation patterns
- Session state design guidelines
- Test writing examples
- Publication and packaging steps

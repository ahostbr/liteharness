"""Per-CLI install adapters for the LiteHarness skills+agents catalog.

Each CLI has a canonical install layout. This module:

1. Detects which CLIs are installed on this machine (PATH lookup + dir presence).
2. Resolves the canonical install path for a CLI's skills/agents.
3. Copies the bundled catalog into that path — opt-in only.

Per Ryan's rule: NEVER auto-install across all detected CLIs without explicit user
opt-in. The setup wizard renders a checklist, and only ticked CLIs get installed.
For CLIs the user wants to install to that aren't auto-detected, the caller passes
an explicit `--path` override.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from liteharness.catalog import catalog_root, subtree

HOME = Path.home()


# ─────────────────────────────────────────────────────────────────────────────
# Adapter model
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class InstallTarget:
    """A single destination inside one CLI's config tree.

    e.g. for cursor: two targets — `~/.cursor/skills/` and `~/.cursor/skills-cursor/`.
    """

    rel_path: Path  # relative path under the user-resolved CLI root
    source_subtree: str  # name of catalog subtree to copy (skills, agents, ...)
    description: str  # human-friendly label for the wizard


@dataclass
class CliAdapter:
    """Defines how to detect + install into one CLI.

    name              — short identifier ("codex", "pi", ...)
    display_name      — user-facing label
    cli_binary        — name to look for on PATH (None = no PATH check)
    default_root      — default config root if auto-detected (e.g. ~/.codex)
    targets           — list of InstallTarget under that root
    post_install_hook — optional callable for special-case work (regen AGENTS.md, etc.)
    notes             — short tip shown in wizard
    """

    name: str
    display_name: str
    cli_binary: str | None
    default_root: Path
    targets: tuple[InstallTarget, ...]
    post_install_hook: Callable[[Path], None] | None = None
    notes: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Post-install hooks
# ─────────────────────────────────────────────────────────────────────────────


_LITEHARNESS_BLOCK_START = "<!-- liteharness-agents:start -->"
_LITEHARNESS_BLOCK_END = "<!-- liteharness-agents:end -->"


def _build_codex_agents_block(installed_agents_dir: Path) -> str:
    """Build the LiteHarness agents block from the *installed* (not source) agents dir.

    We read from the actually-copied destination so the block can never reference
    agents that failed to install.
    """
    lines = [
        _LITEHARNESS_BLOCK_START,
        "# Agents (synced from LiteHarness)",
        "",
        "Specialist agents available via the LiteHarness catalog. Hand-edit content",
        "OUTSIDE this block — anything between the start/end markers is regenerated",
        "on every `liteharness install --cli codex`.",
        "",
    ]
    if installed_agents_dir.exists():
        for f in sorted(installed_agents_dir.glob("*.md")):
            lines.append(f"- `{f.stem}` — see `~/.codex/skills/agents/{f.name}` for full spec")
    lines.append(_LITEHARNESS_BLOCK_END)
    return "\n".join(lines) + "\n"


def _regen_codex_agents_md(cli_root: Path) -> None:
    """Codex reads ~/.codex/AGENTS.md for an enumeration of available agents.

    NON-DESTRUCTIVE: if AGENTS.md exists with a hand-edited body, we update ONLY
    the marker-wrapped LiteHarness block (or write a sibling file if the user has
    a pre-existing AGENTS.md without markers — never silently overwrite).
    """
    installed_agents_dir = cli_root / "skills" / "agents"
    block = _build_codex_agents_block(installed_agents_dir)
    target = cli_root / "AGENTS.md"

    if not target.exists():
        # Fresh write — include a friendly header so the user knows what this file is.
        target.write_text(
            "# Codex Agents Manifest\n\n"
            "_This file is read by Codex. The block below is managed by LiteHarness;_\n"
            "_edit content above or below the markers freely._\n\n"
            + block,
            encoding="utf-8",
        )
        return

    existing = target.read_text(encoding="utf-8")
    if _LITEHARNESS_BLOCK_START in existing and _LITEHARNESS_BLOCK_END in existing:
        # Replace only the marker block, preserve everything else.
        start = existing.index(_LITEHARNESS_BLOCK_START)
        end = existing.index(_LITEHARNESS_BLOCK_END) + len(_LITEHARNESS_BLOCK_END)
        # Include any trailing newline that was part of the previous block.
        if end < len(existing) and existing[end] == "\n":
            end += 1
        new_content = existing[:start] + block + existing[end:]
        target.write_text(new_content, encoding="utf-8")
        return

    # AGENTS.md exists but no markers — user owns this file. Write to sibling
    # instead of clobbering. The user can @include it or merge manually.
    sibling = cli_root / "AGENTS.liteharness.md"
    sibling.write_text(
        "# LiteHarness Agents (sibling — your AGENTS.md was preserved)\n\n"
        "_To merge: copy the marker block below into your AGENTS.md. Future_\n"
        "_`liteharness install` runs will keep the block in sync._\n\n"
        + block,
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Adapters
# ─────────────────────────────────────────────────────────────────────────────


ADAPTERS: dict[str, CliAdapter] = {
    "codex": CliAdapter(
        name="codex",
        display_name="Codex CLI",
        cli_binary="codex",
        default_root=HOME / ".codex",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        post_install_hook=_regen_codex_agents_md,
        notes="Codex treats skills as the unit; agents are vendored under skills/agents/. AGENTS.md is regenerated.",
    ),
    "copilot": CliAdapter(
        name="copilot",
        display_name="GitHub Copilot CLI",
        cli_binary="copilot",
        default_root=HOME / ".copilot",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Existing skills are preserved; LiteHarness skills are added alongside.",
    ),
    "pi": CliAdapter(
        name="pi",
        display_name="Pi (earendil-works)",
        cli_binary="pi",
        default_root=HOME / ".pi" / "agent",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Pi extensions (inbox, presence, orchestrator, ...) ship separately as a 20-package tree.",
    ),
    "opencode": CliAdapter(
        name="opencode",
        display_name="OpenCode",
        cli_binary="opencode",
        default_root=HOME / ".config" / "opencode",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Plugins go in plugins/ — install the liteharness.ts plugin separately if you want hook integration.",
    ),
    "cursor": CliAdapter(
        name="cursor",
        display_name="Cursor CLI",
        cli_binary="cursor",
        default_root=HOME / ".cursor",
        targets=(
            InstallTarget(Path("skills"), "skills", "Shared skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Cursor also reads ~/.cursor/skills-cursor/ for Cursor-specific overrides; we leave that dir untouched.",
    ),
    "gemini": CliAdapter(
        name="gemini",
        display_name="Google Gemini CLI",
        cli_binary="gemini",
        default_root=HOME / ".gemini",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="GEMINI.md is left untouched; skills only.",
    ),
    "antigravity": CliAdapter(
        name="antigravity",
        display_name="Google Antigravity (agy)",
        cli_binary=None,  # agy.exe lives in PATH-included Programs/Antigravity/bin
        default_root=HOME / ".antigravity",
        targets=(
            InstallTarget(Path("skills"), "skills", "Local Antigravity skills"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Antigravity also reads ~/.gemini/antigravity/{skills,global_skills,global_workflows} — those are managed by the wizard separately.",
    ),
    "continue": CliAdapter(
        name="continue",
        display_name="Continue (VS Code / JetBrains)",
        cli_binary=None,
        default_root=HOME / ".continue",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Continue uses ~/.continue/rules/ for prompt rules — that dir is left untouched. Skills only.",
    ),
    "crush": CliAdapter(
        name="crush",
        display_name="Charm Crush",
        cli_binary="crush",
        default_root=HOME / ".config" / "crush",
        targets=(
            InstallTarget(Path("skills"), "skills", "Skills catalog"),
            InstallTarget(Path("skills/agents"), "agents", "Polymathic + workflow agents"),
        ),
        notes="Crush also has ~/.config/crush/commands/ — left untouched. Skills only.",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Detection
# ─────────────────────────────────────────────────────────────────────────────


def _which(binary: str) -> Path | None:
    found = shutil.which(binary)
    # On Windows shells, .ps1/.cmd shims show up — accept any of them.
    if not found:
        for ext in (".ps1", ".cmd", ".bat", ".exe"):
            found = shutil.which(binary + ext)
            if found:
                break
    return Path(found) if found else None


@dataclass
class DetectionResult:
    name: str
    display_name: str
    cli_binary_path: Path | None
    config_dir_exists: bool
    detected: bool  # binary on PATH OR config dir present
    default_root: Path
    notes: str


def detect_all() -> list[DetectionResult]:
    """Detect which CLIs are present. Used by the setup wizard to pre-tick checkboxes."""
    results: list[DetectionResult] = []
    for adapter in ADAPTERS.values():
        bin_path = _which(adapter.cli_binary) if adapter.cli_binary else None
        dir_exists = adapter.default_root.exists()
        results.append(
            DetectionResult(
                name=adapter.name,
                display_name=adapter.display_name,
                cli_binary_path=bin_path,
                config_dir_exists=dir_exists,
                detected=bool(bin_path) or dir_exists,
                default_root=adapter.default_root,
                notes=adapter.notes,
            )
        )
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Install
# ─────────────────────────────────────────────────────────────────────────────


def _copy_subtree(src: Path, dest: Path, dry_run: bool) -> int:
    """Copy src tree into dest, returning the number of files actually copied.

    We count the SOURCE tree (what we wrote), never the destination (which would
    include pre-existing user files and inflate the reported number).
    """
    source_count = sum(1 for _ in src.rglob("*") if _.is_file())
    if dry_run:
        return source_count
    if dest.exists():
        # Merge — overwrite files but preserve dest-only files outside our namespace.
        for item in src.rglob("*"):
            if item.is_dir():
                (dest / item.relative_to(src)).mkdir(parents=True, exist_ok=True)
            else:
                target = dest / item.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
    else:
        shutil.copytree(src, dest)
    return source_count


@dataclass
class InstallReport:
    cli: str
    root: Path
    targets: list[tuple[Path, int]]  # (resolved_path, file_count)
    dry_run: bool
    post_install_ran: bool


def install_cli(
    name: str,
    override_path: Path | None = None,
    dry_run: bool = False,
    quiet: bool = False,
) -> InstallReport:
    """Install the LiteHarness catalog into one CLI's canonical (or overridden) root.

    When ``quiet`` is True, suppress per-target progress prints so the caller can
    emit machine-readable JSON cleanly.
    """
    if name not in ADAPTERS:
        raise SystemExit(f"Unknown CLI '{name}'. Available: {', '.join(sorted(ADAPTERS))}")
    adapter = ADAPTERS[name]
    root = (override_path or adapter.default_root).expanduser().resolve()

    if not root.exists():
        if dry_run and not quiet:
            print(f"[dry-run] Would create root dir: {root}")
        elif not dry_run:
            root.mkdir(parents=True, exist_ok=True)

    target_reports: list[tuple[Path, int]] = []
    for target in adapter.targets:
        src = subtree(target.source_subtree)
        dest = root / target.rel_path
        count = _copy_subtree(src, dest, dry_run=dry_run)
        if not quiet:
            action = "Would copy" if dry_run else "Copied"
            print(f"  [{name}] {action} {count} files -> {dest}  ({target.description})")
        target_reports.append((dest, count))

    post_ran = False
    if adapter.post_install_hook and not dry_run:
        adapter.post_install_hook(root)
        post_ran = True

    return InstallReport(
        cli=name,
        root=root,
        targets=target_reports,
        dry_run=dry_run,
        post_install_ran=post_ran,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI helpers
# ─────────────────────────────────────────────────────────────────────────────


def list_clis_for_display() -> str:
    """Return a formatted detection table for `liteharness install --list`."""
    results = detect_all()
    lines = [
        f"{'CLI':<14} {'Detected':<10} {'Bin on PATH':<14} {'Config Dir':<14} {'Default Root'}",
        "-" * 100,
    ]
    for r in results:
        bin_disp = "yes" if r.cli_binary_path else "no"
        dir_disp = "yes" if r.config_dir_exists else "no"
        det_disp = "[x]" if r.detected else "[ ]"
        lines.append(
            f"{r.name:<14} {det_disp:<10} {bin_disp:<14} {dir_disp:<14} {r.default_root}"
        )
    return "\n".join(lines)


def list_clis_as_json() -> str:
    """Return the detection results as JSON (the canonical machine-readable form)."""
    import dataclasses
    import json

    results = detect_all()
    return json.dumps(
        [
            {
                "name": r.name,
                "displayName": r.display_name,
                "binOnPath": bool(r.cli_binary_path),
                "binaryPath": str(r.cli_binary_path) if r.cli_binary_path else None,
                "configDirExists": r.config_dir_exists,
                "detected": r.detected,
                "defaultRoot": str(r.default_root),
                "notes": r.notes,
            }
            for r in results
        ]
    )


def install_report_as_json(report: "InstallReport") -> str:
    """Serialize an InstallReport for machine consumption."""
    import json

    return json.dumps(
        {
            "cli": report.cli,
            "ok": True,
            "root": str(report.root),
            "dryRun": report.dry_run,
            "postInstallRan": report.post_install_ran,
            "targets": [
                {"path": str(p), "fileCount": n} for p, n in report.targets
            ],
            "filesCopied": sum(n for _, n in report.targets),
        }
    )

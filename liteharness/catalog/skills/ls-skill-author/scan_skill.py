"""Deterministic security scanner + guarded promoter for authored skills.

This is the fail-closed gate that stands between a *generated* SKILL.md and it
becoming discoverable by Claude Code. Three subcommands:

    python scan_skill.py draft   --name <name> --skill-md <file> [--companion NAME=FILE ...] [--from DIR]
    python scan_skill.py scan    --name <name>
    python scan_skill.py promote --name <name>

Security model (see denylist.yaml and the ls-skill-author SKILL.md):

  draft   Stages generated files into ~/.liteharness/skill-quarantine/<name>/ and
          HARD-ASSERTS the resolved path is OUTSIDE every scanned root
          (~/.claude/skills, ~/.claude/agents, ~/.claude/commands, plugin cache).
          Drafts therefore CANNOT be discovered.

  scan    Runs Layer-1 denylist static analysis over the quarantined skill's
          frontmatter, body, and companion files -> fail-closed JSON verdict
          {verdict: pass|fail, findings:[...]}. Any HIGH severity => hard fail.
          Writes the verdict to <quarantine>/<name>/.scan-verdict.json.

  promote The ONLY path from quarantine to a scanned root
          (~/.claude/skills/<name>/). REFUSES unless ALL of:
            1. a stored passing Layer-1 verdict (.scan-verdict.json), AND
            2. a fresh Layer-1 re-scan also passes (catches post-scan tampering), AND
            3. a Layer-2 GO token (.layer2-go, decision == "GO"), AND
            4. an HITL approve token (.hitl-approve, decision == "APPROVE").
          Fail-closed: any exception / malformed frontmatter => refuse.

Path roots are derived from the home dir so tests can redirect them:
  SKILL_AUTHOR_HOME            overrides the home base (default: ~)
  SKILL_AUTHOR_CLAUDE_ROOT     overrides <home>/.claude
  SKILL_AUTHOR_SKILLS_ROOT     overrides the promote target (<claude>/skills)
  SKILL_AUTHOR_QUARANTINE_ROOT overrides <home>/.liteharness/skill-quarantine
  SKILL_AUTHOR_DENYLIST        overrides ./denylist.yaml
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

import yaml

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SkillAuthorError(Exception):
    """Raised on refusal / hard-assert violation. Callers should fail closed."""


# Control files scan_skill.py owns inside a quarantine dir. Never scanned, never
# copied into the live skill on promote.
CONTROL_FILES = {".scan-verdict.json", ".layer2-go", ".hitl-approve"}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# ---------------------------------------------------------------------------
# Path configuration (env-overridable so tests never touch the real ~/.claude)
# ---------------------------------------------------------------------------


def _home() -> str:
    return os.environ.get("SKILL_AUTHOR_HOME") or os.path.expanduser("~")


def _claude_root() -> str:
    return os.environ.get("SKILL_AUTHOR_CLAUDE_ROOT") or os.path.join(_home(), ".claude")


def _skills_root() -> str:
    return os.environ.get("SKILL_AUTHOR_SKILLS_ROOT") or os.path.join(_claude_root(), "skills")


def _quarantine_root() -> str:
    return os.environ.get("SKILL_AUTHOR_QUARANTINE_ROOT") or os.path.join(
        _home(), ".liteharness", "skill-quarantine"
    )


def _scanned_roots() -> list[str]:
    """Roots where a file is discoverable by Claude Code. Drafts MUST stay out."""
    claude = _claude_root()
    return [
        os.path.join(claude, "skills"),
        os.path.join(claude, "agents"),
        os.path.join(claude, "commands"),
        os.path.join(claude, "plugins", "cache"),
    ]


def _denylist_path() -> str:
    return os.environ.get("SKILL_AUTHOR_DENYLIST") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "denylist.yaml"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_name(name: str) -> str:
    if not name or not NAME_RE.match(name):
        raise SkillAuthorError(
            f"invalid skill name {name!r}: must match {NAME_RE.pattern} "
            "(lowercase alphanumerics, hyphen, underscore; no path separators)"
        )
    return name


def _is_within(child: str, parent: str) -> bool:
    """True if `child` is `parent` or nested under it, path-traversal safe."""
    child_r = os.path.realpath(child)
    parent_r = os.path.realpath(parent)
    if child_r == parent_r:
        return True
    return child_r.startswith(parent_r + os.sep)


def _assert_outside_scanned_roots(path: str) -> None:
    """HARD ASSERT: `path` is not inside any discoverable root. Fail-closed."""
    resolved = os.path.realpath(path)
    for root in _scanned_roots():
        if _is_within(resolved, root):
            raise SkillAuthorError(
                f"REFUSED: draft path {resolved!r} resolves INSIDE scanned root "
                f"{os.path.realpath(root)!r} — an unscanned skill must never live "
                "in a discoverable location"
            )


# ---------------------------------------------------------------------------
# Denylist
# ---------------------------------------------------------------------------


class Rule:
    __slots__ = ("id", "severity", "description", "category", "regex")

    def __init__(self, id, severity, description, category, regex):
        self.id = id
        self.severity = severity
        self.description = description
        self.category = category
        self.regex = regex


_SEVERITIES = {"HIGH", "MEDIUM", "LOW"}


def load_denylist(path: str | None = None) -> tuple[str, list[Rule]]:
    path = path or _denylist_path()
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict) or "rules" not in data:
        raise SkillAuthorError(f"malformed denylist {path!r}: missing 'rules'")
    version = str(data.get("version", "0"))
    rules: list[Rule] = []
    for raw in data["rules"]:
        sev = str(raw["severity"]).upper()
        if sev not in _SEVERITIES:
            raise SkillAuthorError(f"denylist rule {raw.get('id')!r}: bad severity {sev!r}")
        flags = re.IGNORECASE | re.MULTILINE
        if raw.get("case_sensitive"):
            flags = re.MULTILINE
        rules.append(
            Rule(
                id=str(raw["id"]),
                severity=sev,
                description=str(raw.get("description", "")),
                category=str(raw.get("category", "")),
                regex=re.compile(raw["pattern"], flags),
            )
        )
    return version, rules


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, body). Raises SkillAuthorError if absent/broken."""
    if not text.startswith("---"):
        raise SkillAuthorError("missing YAML frontmatter (no leading '---')")
    # Match the first fenced block: ---\n ... \n---
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", text, re.DOTALL)
    if not m:
        raise SkillAuthorError("unterminated YAML frontmatter (no closing '---')")
    return m.group(1), text[m.end():]


def parse_frontmatter(text: str) -> dict:
    fm_raw, _ = split_frontmatter(text)
    try:
        data = yaml.safe_load(fm_raw)
    except yaml.YAMLError as exc:  # pragma: no cover - message varies
        raise SkillAuthorError(f"malformed frontmatter YAML: {exc}")
    if not isinstance(data, dict):
        raise SkillAuthorError("frontmatter is not a mapping")
    return data


def _allowed_tools_str(fm: dict) -> str:
    val = fm.get("allowed-tools", fm.get("allowed_tools"))
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        return ", ".join(str(v) for v in val)
    return str(val)


# A wildcard *grant* is HIGH only when the star is a whole token: a bare `*`
# list entry (delimited by string bounds / comma / whitespace / list brackets /
# quotes), or a Word(*) grant where `*` is the ENTIRE parenthesised argument
# (e.g. Bash(*) or Bash( * )). A scoped glob suffix like Bash(git status:*) —
# the least-privilege form TEMPLATE.md recommends — must NOT match: there the
# star is preceded by ':' (not a token delimiter).
_WILDCARD_TOOLS = re.compile(
    r"""(?:(?<![^\s,\[\"'])\*(?![^\s,\]\"']))|(?:\b\w+\(\s*\*\s*\))"""
)


def _check_over_broad_tools(fm: dict, findings: list, filename: str) -> None:
    tools = _allowed_tools_str(fm).strip()
    if not tools:
        return
    if _WILDCARD_TOOLS.search(tools) or tools == "*" or tools.lower() == "all":
        findings.append(
            _finding(
                "over-broad-allowed-tools", "HIGH", filename, 1,
                tools[:80],
                "allowed-tools grants a wildcard / all-tools scope",
                "over-broad-frontmatter",
            )
        )
        return
    # Bare unrestricted Bash (no parenthesised scope) is a softer smell.
    if re.search(r"(?<![\w-])Bash(?!\s*\()", tools):
        findings.append(
            _finding(
                "unrestricted-bash", "MEDIUM", filename, 1,
                tools[:80],
                "allowed-tools grants unrestricted Bash (no command scope)",
                "over-broad-frontmatter",
            )
        )


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------


def _finding(rule_id, severity, file, line, snippet, description, category=""):
    snippet = (snippet or "").strip().replace("\n", "\\n")
    if len(snippet) > 120:
        snippet = snippet[:117] + "..."
    return {
        "rule_id": rule_id,
        "severity": severity,
        "file": file,
        "line": line,
        "snippet": snippet,
        "description": description,
        "category": category,
    }


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _iter_scan_files(skill_dir: str):
    for dirpath, _dirs, filenames in os.walk(skill_dir):
        for fn in sorted(filenames):
            if fn in CONTROL_FILES:
                continue
            yield os.path.join(dirpath, fn)


def scan_skill_dir(skill_dir: str, denylist_path: str | None = None) -> dict:
    """Layer-1 static analysis. Returns a fail-closed verdict dict."""
    verdict = {
        "tool": "scan_skill",
        "skill_dir": os.path.realpath(skill_dir),
        "denylist_version": None,
        "scanned_files": [],
        "findings": [],
        "counts": {"HIGH": 0, "MEDIUM": 0, "LOW": 0},
        "layer1_pass": False,
        "verdict": "fail",
        "scanned_at": _now_iso(),
    }
    findings = verdict["findings"]
    try:
        version, rules = load_denylist(denylist_path)
        verdict["denylist_version"] = version

        if not os.path.isdir(skill_dir):
            findings.append(
                _finding("missing-skill-dir", "HIGH", skill_dir, 0, "",
                         "skill directory does not exist", "structure")
            )
            raise _StopScan

        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(skill_md):
            findings.append(
                _finding("missing-skill-md", "HIGH", "SKILL.md", 0, "",
                         "no SKILL.md in skill directory", "structure")
            )

        for fpath in _iter_scan_files(skill_dir):
            rel = os.path.relpath(fpath, skill_dir).replace(os.sep, "/")
            verdict["scanned_files"].append(rel)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, ValueError):
                findings.append(
                    _finding("binary-or-nontext", "MEDIUM", rel, 0, "",
                             "companion file is not valid UTF-8 text (opaque payload risk)",
                             "obfuscation")
                )
                continue

            for rule in rules:
                for m in rule.regex.finditer(text):
                    findings.append(
                        _finding(rule.id, rule.severity, rel,
                                 _line_of(text, m.start()), m.group(0),
                                 rule.description, rule.category)
                    )

        # Structural frontmatter checks on SKILL.md.
        if os.path.isfile(skill_md):
            with open(skill_md, "r", encoding="utf-8") as fh:
                md_text = fh.read()
            try:
                fm = parse_frontmatter(md_text)
            except SkillAuthorError as exc:
                findings.append(
                    _finding("malformed-frontmatter", "HIGH", "SKILL.md", 1, str(exc),
                             "SKILL.md frontmatter is missing or unparseable", "structure")
                )
            else:
                _check_over_broad_tools(fm, findings, "SKILL.md")
                if not fm.get("name"):
                    findings.append(
                        _finding("missing-name", "MEDIUM", "SKILL.md", 1, "",
                                 "frontmatter has no 'name'", "structure")
                    )
                if not fm.get("description"):
                    findings.append(
                        _finding("missing-description", "MEDIUM", "SKILL.md", 1, "",
                                 "frontmatter has no 'description'", "structure")
                    )
    except _StopScan:
        pass
    except Exception as exc:  # fail-closed on ANY unexpected error
        findings.append(
            _finding("scanner-exception", "HIGH", skill_dir, 0, str(exc)[:120],
                     "scanner raised an unexpected exception — failing closed",
                     "internal")
        )

    for f in findings:
        verdict["counts"][f["severity"]] = verdict["counts"].get(f["severity"], 0) + 1
    high = verdict["counts"]["HIGH"]
    verdict["layer1_pass"] = high == 0
    verdict["verdict"] = "pass" if high == 0 else "fail"
    return verdict


class _StopScan(Exception):
    pass


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------


def draft(name: str, skill_md: str | None = None, companions=None, from_dir: str | None = None) -> dict:
    _validate_name(name)
    dest = os.path.join(_quarantine_root(), name)

    # HARD ASSERT — the load-bearing guarantee: drafts never land in a scanned root.
    _assert_outside_scanned_roots(dest)

    # Clear any prior draft (only ever inside quarantine — re-assert before rmtree).
    if os.path.exists(dest):
        if not _is_within(dest, _quarantine_root()):
            raise SkillAuthorError(f"refusing to clear {dest!r}: outside quarantine root")
        shutil.rmtree(dest)
    os.makedirs(dest, exist_ok=True)

    if from_dir:
        if not os.path.isdir(from_dir):
            raise SkillAuthorError(f"--from {from_dir!r} is not a directory")
        for item in os.listdir(from_dir):
            if item in CONTROL_FILES:
                continue
            src = os.path.join(from_dir, item)
            dst = os.path.join(dest, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

    if skill_md:
        if not os.path.isfile(skill_md):
            raise SkillAuthorError(f"--skill-md {skill_md!r} not found")
        shutil.copy2(skill_md, os.path.join(dest, "SKILL.md"))

    for comp_name, comp_path in (companions or {}).items():
        if not os.path.isfile(comp_path):
            raise SkillAuthorError(f"companion {comp_path!r} not found")
        base = os.path.basename(comp_name)
        if base != comp_name or base in CONTROL_FILES:
            raise SkillAuthorError(f"illegal companion name {comp_name!r}")
        shutil.copy2(comp_path, os.path.join(dest, base))

    return {
        "drafted": True,
        "name": name,
        "quarantine_dir": os.path.realpath(dest),
        "outside_scanned_roots": True,
    }


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------


def _read_token(path: str, required_decision: str) -> dict:
    if not os.path.isfile(path):
        raise SkillAuthorError(f"missing required token: {os.path.basename(path)}")
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SkillAuthorError(f"malformed token {os.path.basename(path)!r}: {exc}")
    if not isinstance(data, dict) or data.get("decision") != required_decision:
        raise SkillAuthorError(
            f"token {os.path.basename(path)!r} decision != {required_decision!r}"
        )
    return data


def promote(name: str, denylist_path: str | None = None) -> dict:
    """Guarded move quarantine -> ~/.claude/skills/<name>/. Fail-closed."""
    _validate_name(name)
    quarantine_dir = os.path.join(_quarantine_root(), name)
    if not os.path.isdir(quarantine_dir):
        raise SkillAuthorError(f"no quarantined skill {name!r} to promote")

    # 1. Fresh Layer-1 re-scan — catches any tampering since the recorded scan.
    fresh = scan_skill_dir(quarantine_dir, denylist_path)
    if fresh["verdict"] != "pass":
        raise SkillAuthorError(
            f"REFUSED: fresh Layer-1 scan failed "
            f"({fresh['counts']['HIGH']} HIGH finding(s))"
        )

    # 2. Stored passing Layer-1 verdict must exist.
    verdict_path = os.path.join(quarantine_dir, ".scan-verdict.json")
    if not os.path.isfile(verdict_path):
        raise SkillAuthorError("REFUSED: no recorded Layer-1 verdict (.scan-verdict.json)")
    with open(verdict_path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    try:
        stored = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SkillAuthorError(
            f"REFUSED: corrupt recorded Layer-1 verdict (.scan-verdict.json): {exc}"
        )
    if not (stored.get("layer1_pass") is True and stored.get("verdict") == "pass"):
        raise SkillAuthorError("REFUSED: recorded Layer-1 verdict is not a pass")

    # 3. Layer-2 GO token.
    _read_token(os.path.join(quarantine_dir, ".layer2-go"), "GO")

    # 4. HITL approve token.
    _read_token(os.path.join(quarantine_dir, ".hitl-approve"), "APPROVE")

    # Destination inside the skills root.
    dest = os.path.join(_skills_root(), name)
    if not _is_within(dest, _skills_root()):
        raise SkillAuthorError(f"REFUSED: destination {dest!r} escapes skills root")
    if os.path.exists(dest):
        raise SkillAuthorError(f"REFUSED: {dest!r} already exists (won't overwrite)")

    os.makedirs(_skills_root(), exist_ok=True)
    shutil.copytree(
        quarantine_dir, dest,
        ignore=shutil.ignore_patterns(*CONTROL_FILES),
    )
    return {
        "promoted": True,
        "name": name,
        "dest": os.path.realpath(dest),
        "layer1_pass": True,
        "layer2_go": True,
        "hitl_approve": True,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_companions(pairs):
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SkillAuthorError(f"--companion expects NAME=PATH, got {pair!r}")
        k, v = pair.split("=", 1)
        out[k] = v
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scan_skill", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_draft = sub.add_parser("draft", help="stage generated files into quarantine")
    p_draft.add_argument("--name", required=True)
    p_draft.add_argument("--skill-md")
    p_draft.add_argument("--from", dest="from_dir")
    p_draft.add_argument("--companion", action="append", default=[])

    p_scan = sub.add_parser("scan", help="Layer-1 denylist scan of a quarantined skill")
    p_scan.add_argument("--name", required=True)
    p_scan.add_argument("--denylist")

    p_promote = sub.add_parser("promote", help="guarded promote quarantine -> ~/.claude/skills")
    p_promote.add_argument("--name", required=True)
    p_promote.add_argument("--denylist")

    args = parser.parse_args(argv)

    if args.cmd == "draft":
        try:
            result = draft(
                args.name,
                skill_md=args.skill_md,
                companions=_parse_companions(args.companion),
                from_dir=args.from_dir,
            )
        except SkillAuthorError as exc:
            print(json.dumps({"drafted": False, "reason": str(exc)}, indent=2))
            return 3
        print(json.dumps(result, indent=2))
        return 0

    if args.cmd == "scan":
        quarantine_dir = os.path.join(_quarantine_root(), _validate_name(args.name))
        verdict = scan_skill_dir(quarantine_dir, args.denylist)
        # Persist the verdict beside the skill (fail-closed: if the dir vanished,
        # the verdict already reflects that as a HIGH finding).
        try:
            with open(os.path.join(quarantine_dir, ".scan-verdict.json"), "w", encoding="utf-8") as fh:
                json.dump(verdict, fh, indent=2)
        except OSError:
            pass
        print(json.dumps(verdict, indent=2))
        return 0 if verdict["verdict"] == "pass" else 1

    if args.cmd == "promote":
        try:
            result = promote(args.name, args.denylist)
        except Exception as exc:  # fail-closed on ANYTHING
            print(json.dumps({"promoted": False, "reason": str(exc)}, indent=2))
            return 2
        print(json.dumps(result, indent=2))
        return 0

    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())

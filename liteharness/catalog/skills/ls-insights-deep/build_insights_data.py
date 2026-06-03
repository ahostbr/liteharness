#!/usr/bin/env python
"""
Aggregates Claude Code session data from all sources into a single insights JSON.
Sources (in order of richness):
  1. facets/              - deep per-session analysis (outcome, friction, goals)
  2. session-meta/        - lightweight metrics (tokens, tools, languages, commits)
  3. raw JSONLs           - first-prompt + tool counts from ~/.claude/projects/
  4. Archive exports      - archived sessions from optional user-configured paths
                           (set INSIGHTS_ARCHIVE_DIRS env var, colon-separated paths)

Optional: set INSIGHTS_ARCHIVE_DIRS to a colon-separated list of directories
containing archived Claude session JSONL exports to include in the analysis.
Example:
    export INSIGHTS_ARCHIVE_DIRS="~/my-archive/exports:~/backup-sessions"
"""

import json
import os
import glob
import sys
from collections import defaultdict
from datetime import datetime, timezone

USAGE_DIR = os.path.expanduser("~/.claude/usage-data")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

# Optional extra archive dirs — configured via env var, not hardcoded
_archive_env = os.environ.get("INSIGHTS_ARCHIVE_DIRS", "")
EXTRA_EXPORT_DIRS = [
    os.path.expanduser(d.strip())
    for d in _archive_env.split(os.pathsep)
    if d.strip() and os.path.isdir(os.path.expanduser(d.strip()))
]

# ── 1. Load facets ────────────────────────────────────────────────────────────
facets = {}
for path in glob.glob(os.path.join(USAGE_DIR, "facets", "*.json")):
    try:
        d = json.load(open(path, encoding="utf-8"))
        sid = d.get("session_id") or os.path.splitext(os.path.basename(path))[0]
        facets[sid] = d
    except Exception:
        pass

# ── 2. Load session-meta ──────────────────────────────────────────────────────
session_meta = {}
for path in glob.glob(os.path.join(USAGE_DIR, "session-meta", "*.json")):
    try:
        d = json.load(open(path, encoding="utf-8"))
        sid = d.get("session_id") or os.path.splitext(os.path.basename(path))[0]
        session_meta[sid] = d
    except Exception:
        pass

# ── 3. Scan raw JSONLs for remaining sessions ─────────────────────────────────
known_sids = set(facets) | set(session_meta)
raw_sessions = {}

for jsonl_path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
    sid = os.path.splitext(os.path.basename(jsonl_path))[0]
    if sid in known_sids:
        continue
    project_dir = os.path.basename(os.path.dirname(jsonl_path))

    first_prompt = None
    first_ts = None
    last_ts = None
    tool_counts = defaultdict(int)
    user_msgs = 0
    assistant_msgs = 0
    git_branch = None
    cwd = None

    try:
        with open(jsonl_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue

                ts = entry.get("timestamp")
                if ts:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                etype = entry.get("type")
                if etype == "user":
                    user_msgs += 1
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and first_prompt is None:
                        first_prompt = content[:200]
                    elif isinstance(content, list) and first_prompt is None:
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                first_prompt = block.get("text", "")[:200]
                                break
                    if git_branch is None:
                        git_branch = entry.get("gitBranch")
                    if cwd is None:
                        cwd = entry.get("cwd")

                elif etype == "assistant":
                    assistant_msgs += 1
                    msg = entry.get("message", {})
                    for block in msg.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_counts[block.get("name", "unknown")] += 1

    except Exception:
        continue

    if first_ts is None:
        continue  # skip empty/unreadable sessions

    raw_sessions[sid] = {
        "session_id": sid,
        "project_dir": project_dir,
        "cwd": cwd,
        "start_time": first_ts,
        "end_time": last_ts,
        "first_prompt": first_prompt,
        "user_message_count": user_msgs,
        "assistant_message_count": assistant_msgs,
        "tool_counts": dict(tool_counts),
        "git_branch": git_branch,
        "source": "raw_jsonl",
    }

# ── 4. Scan archive export dirs for additional sessions ───────────────────────
archive_sessions = {}
for _export_root in EXTRA_EXPORT_DIRS:
  for jsonl_path in glob.glob(os.path.join(_export_root, "**", "*.jsonl"), recursive=True):
        basename = os.path.basename(jsonl_path)
        # Skip non-session files (transcript_current.jsonl, etc.)
        if basename in ("transcript_current.jsonl",) or not basename.endswith(".jsonl"):
            continue
        # Session ID is embedded in filename as last hex segment: name_HEXID.jsonl
        parts = basename.replace(".jsonl", "").split("_")
        sid = parts[-1] if parts else basename
        if sid in known_sids or sid in raw_sessions:
            continue

        # Extract date from parent dir name
        date_dir = os.path.basename(os.path.dirname(jsonl_path))

        first_prompt = None
        first_ts = None
        tool_counts = defaultdict(int)
        user_msgs = 0
        assistant_msgs = 0

        try:
            with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue

                    ts = entry.get("timestamp")
                    if ts and first_ts is None:
                        first_ts = ts

                    etype = entry.get("type")
                    if etype == "user":
                        user_msgs += 1
                        msg = entry.get("message", {})
                        content = msg.get("content", "")
                        if isinstance(content, str) and first_prompt is None:
                            first_prompt = content[:200]
                        elif isinstance(content, list) and first_prompt is None:
                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    first_prompt = block.get("text", "")[:200]
                                    break
                    elif etype == "assistant":
                        assistant_msgs += 1
                        msg = entry.get("message", {})
                        for block in msg.get("content", []):
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_counts[block.get("name", "unknown")] += 1
        except Exception:
            continue

        if first_ts is None and date_dir:
            first_ts = f"{date_dir}T00:00:00.000Z"

        archive_sessions[sid] = {
            "session_id": sid,
            "project_dir": os.path.basename(_export_root),
            "start_time": first_ts,
            "first_prompt": first_prompt,
            "user_message_count": user_msgs,
            "assistant_message_count": assistant_msgs,
            "tool_counts": dict(tool_counts),
            "source": "archive_export",
        }

# ── 6. Merge everything ───────────────────────────────────────────────────────
all_sessions = {}

# Start with archive exports (oldest, least data)
for sid, d in archive_sessions.items():
    all_sessions[sid] = {"source": "archive_export", **d}

# Layer on raw JSONLs
for sid, d in raw_sessions.items():
    all_sessions[sid] = {"source": "raw_jsonl", **d}

# Layer on session-meta (richer)
for sid, d in session_meta.items():
    if sid in all_sessions:
        all_sessions[sid].update(d)
        all_sessions[sid]["source"] = "session_meta"
    else:
        all_sessions[sid] = {"source": "session_meta", **d}

# Layer on facets (richest)
for sid, d in facets.items():
    if sid in all_sessions:
        all_sessions[sid].update(d)
        all_sessions[sid]["source"] = "facet"
    else:
        all_sessions[sid] = {"source": "facet", **d}

# ── 5. Sort by start_time ─────────────────────────────────────────────────────
sessions_list = sorted(
    all_sessions.values(),
    key=lambda s: s.get("start_time") or s.get("user_message_timestamps", [""])[0] or "",
)

# ── 6. Build summary stats ────────────────────────────────────────────────────
total = len(sessions_list)
facet_count = sum(1 for s in sessions_list if s["source"] == "facet")
meta_count = sum(1 for s in sessions_list if s["source"] == "session_meta")
raw_count = sum(1 for s in sessions_list if s["source"] == "raw_jsonl")

project_counts = defaultdict(int)
for s in sessions_list:
    proj = s.get("project_path") or s.get("project_dir") or s.get("cwd") or "unknown"
    # Normalize path to project name
    proj = os.path.basename(proj.rstrip("/\\"))
    project_counts[proj] += 1

total_commits = sum(s.get("git_commits", 0) for s in sessions_list)
total_input_tokens = sum(s.get("input_tokens", 0) for s in sessions_list)
total_output_tokens = sum(s.get("output_tokens", 0) for s in sessions_list)

tool_totals = defaultdict(int)
for s in sessions_list:
    for tool, count in (s.get("tool_counts") or {}).items():
        tool_totals[tool] += count

date_range = {
    "oldest": sessions_list[0].get("start_time", "?") if sessions_list else "?",
    "newest": sessions_list[-1].get("start_time", "?") if sessions_list else "?",
}

output = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "summary": {
        "total_sessions": total,
        "facet_sessions": facet_count,
        "meta_sessions": meta_count,
        "raw_sessions": raw_count,
        "archive_export_sessions": sum(1 for s in sessions_list if s["source"] == "archive_export"),
        "total_commits": total_commits,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "date_range": date_range,
        "top_projects": sorted(project_counts.items(), key=lambda x: -x[1])[:20],
        "top_tools": sorted(tool_totals.items(), key=lambda x: -x[1])[:20],
    },
    "sessions": sessions_list,
}

out_path = os.path.join(USAGE_DIR, "insights_data.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, default=str)

print(f"OK: {total} sessions written to {out_path}")
print(f"  Facets: {facet_count} | Session-meta: {meta_count} | Raw JSONL: {raw_count}")
print(f"  Date range: {date_range['oldest'][:10]} to {date_range['newest'][:10]}")
print(f"  Commits: {total_commits} | Input tokens: {total_input_tokens:,} | Output: {total_output_tokens:,}")

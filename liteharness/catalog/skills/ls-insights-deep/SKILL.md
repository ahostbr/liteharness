---
name: ls-insights-deep
description: Deep usage insights across ALL Claude Code sessions (full history, not just recent). Scans every JSONL conversation file, merges with facets and session-meta, then generates a beautiful visual HTML report using the mockup skill. Use when you want comprehensive analysis covering months of sessions, not just the last 50-60.
---

# Deep Insights

Full-history Claude Code usage analysis. Covers all sessions back to the beginning, not just recent ones.

## Step 1: Build the Data

Run the aggregation script to scan all sessions:

```bash
python "${CLAUDE_SKILL_DIR}/build_insights_data.py"
```

This outputs `~/.claude/usage-data/insights_data.json` with all sessions merged.

If it takes more than 2 minutes, show a progress note to the user.

## Step 2: Load and Summarize

Read `~/.claude/usage-data/insights_data.json`.

Extract for analysis:

- `summary` block (totals, date range, top projects, top tools)
- All sessions where `source == "facet"` (richest data — include ALL of these)
- A representative sample of `session_meta` sessions (up to 150)
- First-prompt data from raw JSONL sessions to understand topic spread

## Step 3: Parallel Analysis

Spawn 4 subagents in parallel, each focused on a different lens. Give each subagent the full facets array plus the summary stats. Ask each to return structured JSON.

**Agent A — Project & Topic Landscape**

- What projects dominate (by session count, commits, tokens)?
- What are the top 5-8 distinct work areas?
- How has focus shifted over the date range (early vs. recent periods)?
- Which projects got the most debugging effort vs. new feature work?

**Agent B — Productivity & Velocity Patterns**

- Sessions per week/month trend
- Commits per session, lines added/removed trends
- Tool usage patterns (Bash vs. Read vs. Edit ratios, MCP usage, web fetch)
- Session length distribution (short <5 msg vs. marathon >50 msg)
- Peak productivity hours/days from message_hours data

**Agent C — Friction & Quality Analysis**

- Aggregate all friction_counts across all facet sessions
- Top friction categories ranked
- Sessions by outcome (fully_achieved vs partial vs not_achieved)
- Claude helpfulness distribution
- Common tool errors and error categories
- Sessions where user_interruptions were high

**Agent D — Evolution & Growth**

- Compare earliest 25% of sessions vs. most recent 25%
- Has friction decreased over time?
- New tools/capabilities adopted over time
- Goal complexity trend (single_task vs multi_task vs complex)
- Most ambitious sessions (highest tool counts, most files modified)

## Step 4: Synthesize

After all 4 agents complete, synthesize into a single insights structure:

```json
{
  "headline_stats": { ... },
  "project_landscape": { ... },
  "productivity_patterns": { ... },
  "friction_analysis": { ... },
  "evolution": { ... },
  "top_sessions": [ ... ],
  "recommendations": [ ... ]
}
```

## Step 5: Generate the Report

Load the mockup skill and generate a beautiful self-contained HTML report:

```
Skill tool → skill: "mockup"
```

The report should use the **Data Visualization Mode** from the mockup skill.

Save to: `~/.claude/usage-data/insights_deep_report.html`

Then open it:

```bash
start "~/.claude/usage-data/insights_deep_report.html"
```

Report it done with the file path.

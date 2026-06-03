---
name: ls-compile-cli
description: Turn any MCP server or SKILL.md file into a standalone CLI using LiteCLI. Use when the user wants to compile an MCP server, skill file, or directory of skills into runnable CLI commands.
version: 1.0.0
---

# compile-cli

Turn any MCP server or SKILL.md file into a compiled standalone Python Click CLI using LiteCLI. This is the self-replicating multiplier — skills become runnable commands that work without Claude.

## Requirements

- Python 3.10+
- LiteCLI — installed as part of [LiteHarness](https://pypi.org/project/liteharness/): `pip install liteharness`

> **Note:** The `litecli` CLI is bundled inside the `liteharness` package. The `litecli-compiler` package referenced in older docs does not exist on PyPI — install `liteharness` instead.

---

## Pipeline 1: MCP Server to CLI

Register any MCP server as a backend, then auto-generate Click commands from its tool schemas.

### Step 1: Register the MCP server

For a stdio MCP (npx-based):

```bash
litecli admin add --id filesystem --type stdio --cmd npx --args "@modelcontextprotocol/server-filesystem /tmp"
```

For an HTTP MCP:

```bash
litecli admin add --id myserver --type http --url http://localhost:8200/mcp
```

The `--id` value becomes the command prefix. Keep it short and lowercase.

### Step 2: Discover tools

```bash
litecli admin discover
```

Output shows all registered backends and their available tools:

```
Backend: filesystem (stdio)
  Tools:
    - read_file       Read a file from the filesystem
    - write_file      Write content to a file
    - list_directory  List files in a directory

Backend: myserver (http)
  Tools:
    - search_docs     Search documentation
    - run_query       Execute a database query
```

### Step 3: Use tools immediately (dynamic mode)

The `dynamic.py` module auto-converts JSON Schema to Click commands at runtime. Tools are accessible immediately after registration:

```bash
litecli filesystem read-file --path /tmp/notes.txt
litecli filesystem list-directory --path /tmp/
litecli myserver search-docs --query "authentication" --limit 10
```

Tool names use hyphens in CLI form (`read_file` becomes `read-file`). Arguments map directly from the JSON Schema properties.

### Step 4: Freeze to static CLI

Once you're satisfied with the dynamic commands, snapshot them as static Python:

```bash
litecli freeze
```

This generates `compiled/<backend_id>.py` for each registered backend — no runtime schema fetching needed, no MCP connection required to run the CLI.

### Real-world example: Turning a local MCP server into a standalone CLI

```bash
# Register your local MCP gateway (replace the URL with your server's address)
litecli admin add --id myapp --type http --url http://localhost:8200/mcp

# Verify tools discovered
litecli admin discover

# Use a tool
litecli myapp search --query "task management" --limit 5

# Freeze to static
litecli freeze
# → writes compiled/myapp.py
```

---

## Pipeline 2: SKILL.md to CLI

Compile a SKILL.md file into a standalone CLI that runs without LiteCLI or an AI.

**Default target is TypeScript** — compiled skills produce a `.ts` file with a `defineCommand` export compatible with the `just-bash` runtime. Use `--target python` to get the legacy Python Click CLI instead.

### Step 1: Basic compile (TypeScript, default)

```bash
litecli compile path/to/SKILL.md
# equivalent:
litecli compile path/to/SKILL.md --target typescript
```

Generates a `.ts` file in the same directory as the skill. The compiler reads the skill's steps, extracts API endpoints and parameters, and produces a `defineCommand` export from `just-bash`.

Output shape:

```typescript
import { defineCommand } from "just-bash";
export const skill-name = defineCommand("skill-name", async (args, ctx) => {
  // args — parsed from SKILL.md parameter definitions
  // ctx.env — auth keys from the just-bash auth bridge (not process.env)
  // fetch() — network calls to configured endpoints
  // returns { stdout, stderr, exitCode }
});
```

### Step 1 (alt): Compile to Python Click CLI (legacy)

```bash
litecli compile path/to/SKILL.md --target python
```

Generates a `.py` file using Click. Use this when the skill targets environments without the just-bash runtime.

### just-bash integration

TypeScript-compiled skills run inside the `just-bash` runtime, which provides:

- **Auth bridge** — credentials injected via `ctx.env`; the runtime resolves keys from the LiteSuite auth store (never `process.env`)
- **Sandboxed execution** — only `just-bash` is an allowed external import; no arbitrary Node modules
- **IPC** — runtime communicates with LiteSuite via the `lite__shell` MCP tool over the agent bridge

### Step 2: Compile with output directory and auto-register

```bash
litecli compile path/to/SKILL.md --output-dir ./compiled/ --register
# or explicitly:
litecli compile path/to/SKILL.md --target typescript --output-dir ./compiled/ --register
```

- `--target typescript` (default) or `--target python` selects the output format
- `--output-dir` puts the generated file in a specific folder
- `--register` adds the compiled CLI to LiteCLI's backend registry so it appears in `litecli compiled list`

### Step 3: List compiled CLIs

```bash
litecli compiled list
```

Output:

```
Name              Source                        Status
────────────────  ────────────────────────────  ──────
search-web        skills/search-web/SKILL.md    ok
send-email        skills/send-email/SKILL.md    ok
notion-pages      skills/notion/SKILL.md        ok
```

### Step 4: Run a compiled CLI

```bash
litecli compiled run search-web --query "LiteCLI documentation"
litecli compiled run send-email --to user@example.com --subject "Hello" --body "Test"
litecli compiled run notion-pages --action list --database-id abc123
```

### Step 5: Remove a compiled CLI

```bash
litecli compiled remove search-web
```

Deletes the generated file (`.ts` or `.py`) and removes it from the registry.

### Real-world example: Compiling a skill

```bash
# Compile the skill (replace path/to/my-skill with your actual skill path)
litecli compile path/to/my-skill/SKILL.md --output-dir ./compiled/ --register

# List to confirm
litecli compiled list

# Run it
litecli compiled run my-skill --action create-page --title "Meeting Notes" --parent-id abc123
```

---

## Pipeline 3: Batch compile from a directory

Compile an entire directory of SKILL.md files in one pass.

```bash
python scripts/build.py --skills-dir path/to/skills/
```

What it does internally:

1. Runs `categorize.py` — classifies each skill as `COMPILE` or `SKIP`
2. Copies skills into the build staging area
3. Compiles each `COMPILE` skill in sequence
4. Logs results to `build/compile_report.json`

Skills are marked `SKIP` when:

- They have no API endpoints (pure reasoning/writing skills)
- They reference too many unrelated external URLs (ambiguous)
- They are marked with `compile: false` in their YAML frontmatter

### Example: Batch compiling a skills directory

```bash
python scripts/build.py --skills-dir ~/.claude/skills/

# Or target a specific category subfolder
python scripts/build.py --skills-dir ~/.claude/skills/productivity/
```

Output:

```
Categorizing skills...
  COMPILE  notion/SKILL.md
  COMPILE  search-web/SKILL.md
  SKIP     brainstorm/SKILL.md  (no API endpoints)
  SKIP     plan-w-quizmaster/SKILL.md  (no API endpoints)
  COMPILE  send-email/SKILL.md

Compiling 3 skills...
  [ok]  notion → compiled/notion.py
  [ok]  search-web → compiled/search_web.py
  [ok]  send-email → compiled/send_email.py

Report written to build/compile_report.json
```

---

## Auth

Compiled CLIs handle authentication two ways:

**Interactive keyring (first use):**
The first time you run a compiled CLI that needs credentials, it prompts interactively and stores the key in the system keyring. Subsequent runs use the stored key silently.

```
$ litecli compiled run notion --action list-pages
Notion token not found. Enter your NOTION_TOKEN: ••••••••••••
Token saved to keyring. Won't ask again.
```

**Environment variables (CI/scripts):**
Set the relevant env var to skip the prompt entirely:

```bash
export NOTION_TOKEN=secret_abc123
export OPENAI_API_KEY=sk-...
export GITHUB_TOKEN=ghp_...

litecli compiled run notion --action list-pages
```

Standard env var names are detected automatically from the skill's API configuration.

---

## Error handling

All compiled CLIs and pipeline errors output structured JSON:

```json
{"error": "Authentication failed", "code": "AUTH_ERROR", "fix": "Set NOTION_TOKEN env var or run with --reset-auth"}
{"error": "Tool not found: read_flie", "code": "TOOL_NOT_FOUND", "fix": "Run 'litecli admin discover' to see available tools"}
{"error": "Connection refused at http://localhost:8200", "code": "BACKEND_UNREACHABLE", "fix": "Check that the MCP server is running"}
```

**"Compilation failed: too complex"**

This means the skill has too many unrelated API endpoints or external URLs for the compiler to produce a clean CLI. The fix: keep it as a readable agent skill. Not everything needs to be compiled — skills that require judgment, iteration, or multi-step reasoning are better left as AI skills.

**"No tools discovered"**

```bash
# Check the backend is reachable
litecli admin discover --id myserver --verbose

# Re-register with correct args
litecli admin remove --id myserver
litecli admin add --id myserver --type http --url http://localhost:8200/mcp
```

---

## Quick reference

```bash
# MCP pipeline
litecli admin add --id <name> --type stdio --cmd npx --args <package>
litecli admin add --id <name> --type http --url <url>
litecli admin discover
litecli <name> <tool-name> [options]
litecli freeze

# SKILL.md pipeline (TypeScript default)
litecli compile path/to/SKILL.md                          # → .ts (default)
litecli compile path/to/SKILL.md --target typescript      # explicit TS
litecli compile path/to/SKILL.md --target python          # legacy .py
litecli compile path/to/SKILL.md --output-dir ./compiled/ --register
litecli compiled list
litecli compiled run <name> [args]
litecli compiled remove <name>

# Batch
python scripts/build.py --skills-dir path/to/skills/
```

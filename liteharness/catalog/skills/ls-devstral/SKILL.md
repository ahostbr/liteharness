---
name: ls-devstral
description: Spawn a local Devstral coding agent running on LM Studio. Use when asked to '/devstral', 'use devstral', 'ask devstral', 'local agent', 'run this on devstral', 'devstral this'. Routes tasks through the local model_router.py proxy at port 5111 which transparently forwards devstral requests to LM Studio while keeping Anthropic calls working normally.
---

# /devstral — Local Devstral Agent via LM Studio

> **Requirements** — before this skill can run, the following must be available on the user's machine:
>
> - **LM Studio** installed and running with `mistralai/devstral-small-2-2512` loaded (download from https://lmstudio.ai/)
> - **uv** on `PATH` — Python package runner (install: `pip install uv` or https://docs.astral.sh/uv/)
> - **LiteCLI** (optional) — universal CLI for MCP backends; set `LITECLI_DIR` env var to its directory if installed. If missing, Step 3 is skipped gracefully.
> - **model_router.py proxy** — expected at `.claude/plugins/kuro/proxy/model_router.py` relative to your project root. This is bundled with the liteharness plugin; if absent the skill falls back to direct HTTP (Step 5b).

You are executing the `/devstral` skill. This routes the user's coding task to a local Devstral Small 2 (24B) agent running in LM Studio.

**Safety rule:** This skill ONLY activates when explicitly invoked. Never auto-invoke it. Devstral is a Mistral coding agent — optimized for code generation, refactoring, and analysis tasks.

The user's task is: `$ARGUMENTS`

---

## Step 1 — Verify LM Studio is Reachable

Before doing anything else, confirm Devstral is available in LM Studio:

```bash
curl -s --max-time 5 http://localhost:1234/api/v0/models | python -c "
import json, sys
data = json.load(sys.stdin)
models = data.get('data', [])
devstral = [m for m in models if 'devstral' in m.get('id','').lower()]
if devstral:
    for m in devstral:
        print(f\"LOADED: {m['id']} state={m.get('state','?')}\")
else:
    print('NOT LOADED')
    print('Available:', [m['id'] for m in models[:5]])
" 2>/dev/null || echo "LM_STUDIO_UNREACHABLE"
```

If LM Studio is unreachable or Devstral is not loaded:

- Report: "LM Studio is not running or Devstral is not loaded. Please start LM Studio and load `mistralai/devstral-small-2-2512` before running /devstral."
- Stop here.

If Devstral is listed but not in a loaded state, offer to load it:

```bash
"$USERPROFILE/.lmstudio/bin/lms.exe" load "mistralai/devstral-small-2-2512" -y -c 32768 --gpu max --identifier "devstral"
```

---

## Step 2 — Check and Start the Model Router Proxy

The proxy (`model_router.py`) routes Devstral requests to LM Studio and everything else to Anthropic. It must be running before spawning the agent.

### Check if proxy is running

Look for the PID file relative to the project root:

```bash
PROXY_PID_FILE="${CLAUDE_PROJECT_DIR:-.}/.claude/plugins/kuro/proxy/proxy.pid"
if [ -f "$PROXY_PID_FILE" ]; then
    PID=$(cat "$PROXY_PID_FILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "PROXY_RUNNING pid=$PID"
    else
        echo "PROXY_STALE_PID"
        rm -f "$PROXY_PID_FILE"
    fi
else
    echo "PROXY_NOT_RUNNING"
fi
```

### Start proxy if not running

If `PROXY_NOT_RUNNING` or `PROXY_STALE_PID`:

```bash
PROXY_SCRIPT="${CLAUDE_PROJECT_DIR:-.}/.claude/plugins/kuro/proxy/model_router.py"
uv run "$PROXY_SCRIPT" &
echo "Proxy started, waiting for startup..."
sleep 2
```

### Verify proxy health

```bash
curl -s --max-time 5 http://127.0.0.1:5111/health
```

Expected response: `{"status": "ok", "port": 5111}`

If the health check fails after starting, report:

- "Proxy failed to start. Check that uv is installed and `model_router.py` exists at `.claude/plugins/kuro/proxy/model_router.py`."
- Stop here.

---

## Step 3 — Discover Available LiteCLI Tools

LiteCLI is a universal CLI generator that turns MCP server tools into Click commands. Devstral's primary way of interacting with the system is through these generated CLIs.

Run discovery to find available backends and tools:

```bash
cd "${LITECLI_DIR:-<your-litecli-dir>}" && uv run -m litecli --json admin discover
```

This returns JSON with each backend's `id`, `prefix`, `state`, `tool_count`, and `description`. Example output:

```json
[
  {
    "id": "my-mcp-backend",
    "type": "http",
    "prefix": "mcp",
    "state": "connected",
    "enabled": true,
    "tool_count": 25,
    "description": "Your MCP Core"
  }
]
```

For each connected backend, you can list its tools by running:

```bash
cd "${LITECLI_DIR:-<your-litecli-dir>}" && uv run -m litecli --json <prefix> --help
```

**Format a tool catalog** for the Devstral prompt. Example:

```
AVAILABLE LITECLI TOOLS:
Backend: mcp (Your MCP Core) — 25 tools
  Invocation: litecli --json mcp <tool_name> [args]

Backend: tools (Your Tools MCP) — 12 tools
  Invocation: litecli --json tools <tool_name> [args]
```

If LiteCLI is not installed or discovery fails, proceed without the tool catalog — Devstral can still do code analysis and generation tasks without it.

**Important:** The `--json` flag ensures machine-parseable output. Always include it when Devstral runs litecli commands.

---

## Step 4 — ANTHROPIC_BASE_URL Warning

**IMPORTANT:** To route Devstral through the proxy, `ANTHROPIC_BASE_URL` must be set to `http://127.0.0.1:5111` for the Claude Code session. This affects ALL API calls in the session. The proxy handles routing correctly:

- Model contains "devstral" or "mistral" → routed to LM Studio at `http://localhost:1234`
- Everything else → routed to Anthropic at `https://api.anthropic.com`

**Proxy resilience caveat:** If the proxy process dies mid-session, ALL API calls will fail (including your own calls back to Anthropic). The proxy is lightweight and stable, but worth noting.

Check if already set:

```bash
echo "ANTHROPIC_BASE_URL=${ANTHROPIC_BASE_URL:-<not set>}"
```

If `ANTHROPIC_BASE_URL` is not already pointing at the proxy, inform the user:

> "Note: To spawn Devstral via the Agent tool, `ANTHROPIC_BASE_URL` must be set to `http://127.0.0.1:5111`. This requires either restarting the session with that env var set, or setting it in your shell before launching Claude Code."
>
> "Alternatively, I can query Devstral directly via HTTP (no env var needed) — this works for single-turn tasks but not for agent-style multi-turn work."

If the user wants to proceed with direct HTTP (simpler, no env var required), jump to **Step 4b**.
If the user wants full agent spawning, they must restart with `ANTHROPIC_BASE_URL=http://127.0.0.1:5111` set.

---

## Step 5a — Spawn Devstral Agent (if ANTHROPIC_BASE_URL is set)

Build the full agent prompt combining:

1. The user's task
2. The LiteCLI tool catalog from Step 3
3. Available file context (paths, code snippets if mentioned)
4. System instructions tuned for Devstral's coding-agent behavior

Then spawn using the Agent tool:

```
Agent(
  subagent_type="devstral-worker",
  prompt="<assembled prompt — see below>"
)
```

### Prompt assembly template

```
You are Devstral Small 2, a coding agent running locally on LM Studio.

TASK:
<user's task from $ARGUMENTS>

LITECLI TOOLS:
You can interact with MCP servers via LiteCLI. Always use --json for parseable output.
Invocation: litecli --json <prefix> <tool_name> [args]

<insert the tool catalog from Step 3 here, e.g.:>
Backend: mcp (Your MCP Core) — 25 tools
  Example: litecli --json mcp rag_query --query "search term"
  Example: litecli --json mcp read_file --path "src/main.py"

INSTRUCTIONS:
- Focus solely on the task above
- Use litecli --json commands via Bash to interact with MCP tools
- Do not emit raw [TOOL_CALLS] tokens — use Bash + litecli instead
- If writing code, include the full file path at the top as a comment
- Be concise and direct. No preamble.
- If the task requires reading files, use the Read tool or litecli

CONTEXT:
<include any file content, error messages, or code snippets the user referenced>
```

**CRITICAL for Devstral:** If the tool catalog is empty (LiteCLI not available), omit the LITECLI TOOLS section and instruct Devstral to use Read/Glob/Grep/Bash directly instead.

---

## Step 5b — Direct HTTP Query (fallback, no env var required)

When ANTHROPIC_BASE_URL is not set or the user wants a simpler path, query Devstral directly via the LM Studio API. This works for single-turn tasks (write this function, review this code, explain this error).

```python
python -c "
import json, urllib.request, sys

task = '''<USER_TASK_HERE>'''

payload = json.dumps({
    'model': 'devstral',
    'messages': [
        {
            'role': 'system',
            'content': 'You are a precise coding assistant. Answer directly. Do not use any tools or tool calls. Output only code or analysis as requested.'
        },
        {
            'role': 'user',
            'content': task
        }
    ],
    'max_tokens': 4096,
    'temperature': 0.1
}).encode()

req = urllib.request.Request(
    'http://localhost:1234/v1/chat/completions',
    data=payload,
    headers={'Content-Type': 'application/json'}
)
try:
    resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
    print(resp['choices'][0]['message']['content'])
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
"
```

For large code files or content with special characters, write to a temp file first:

```bash
cat > /tmp/devstral_task.py << 'SCRIPT_EOF'
import json, urllib.request, sys

task = open('/tmp/devstral_input.txt').read()

payload = json.dumps({
    'model': 'devstral',
    'messages': [
        {
            'role': 'system',
            'content': 'You are a precise coding assistant. Answer directly. Do not use any tools or tool calls.'
        },
        {'role': 'user', 'content': task}
    ],
    'max_tokens': 4096,
    'temperature': 0.1
}).encode()

req = urllib.request.Request(
    'http://localhost:1234/v1/chat/completions',
    data=payload,
    headers={'Content-Type': 'application/json'}
)
resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
print(resp['choices'][0]['message']['content'])
SCRIPT_EOF

python /tmp/devstral_task.py
```

---

## Step 6 — Present Results

When Devstral returns:

1. Present the response clearly with attribution: "**Devstral (local):**"
2. If code was returned, show it in proper fenced code blocks with language tags
3. If the task involved editing files, apply the changes using the Edit tool
4. Note approximate inference time if remarkable (Devstral 24B @ RTX 5090 is typically 10-30s for most tasks)
5. If Devstral's response included `[TOOL_CALLS]` artifacts, strip them and re-run with a stronger "no tool calls" instruction

---

## Proxy Lifecycle Notes

| Scenario                               | Action                                 |
| -------------------------------------- | -------------------------------------- |
| Proxy already running                  | Skip startup, proceed directly         |
| Proxy PID file exists but process gone | Delete stale PID, restart proxy        |
| Proxy failed to start                  | Report error with uv path, stop        |
| LM Studio unreachable                  | Warn user, stop                        |
| Devstral not loaded                    | Offer to load via `lms.exe`, wait ~12s |
| ANTHROPIC_BASE_URL not set             | Warn, offer direct HTTP fallback       |

**To stop the proxy manually:**

```bash
kill $(cat "${CLAUDE_PROJECT_DIR:-.}/.claude/plugins/kuro/proxy/proxy.pid")
```

**To check proxy logs** (if started in this session):

```bash
# Proxy logs go to stderr — check terminal where Claude Code was launched
```

---

## Quick Reference

| Item                      | Value                                                            |
| ------------------------- | ---------------------------------------------------------------- |
| Proxy port                | `http://127.0.0.1:5111`                                          |
| LM Studio API             | `http://localhost:1234`                                          |
| Devstral model ID         | `mistralai/devstral-small-2-2512`                                |
| Devstral identifier (LMS) | `devstral`                                                       |
| VRAM usage                | ~15.2 GB (Q4_K_M)                                                |
| Context length            | 32768 (recommended), 65536 (max, solo)                           |
| Proxy script              | `.claude/plugins/kuro/proxy/model_router.py`                     |
| PID file                  | `.claude/plugins/kuro/proxy/proxy.pid`                           |
| uv path                   | `uv` (must be on PATH — install from https://docs.astral.sh/uv/) |

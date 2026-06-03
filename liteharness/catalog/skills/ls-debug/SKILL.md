---
name: ls-debug
description: "LiteSuite runtime debugging — set up disk logging, Monitor tail, rebuild loop. Use when debugging Electron webview, preload, bridge, IPC, or any runtime issue where reading code alone won't reveal the problem. Triggers on 'debug litesuite', 'ls-debug', 'debug the panel', 'debug codex', 'debug claude panel', 'why is it not loading', 'webview blank', 'bridge not connecting'."
---

# LiteSuite Runtime Debug Loop

Systematic debugging for LiteSuite's Electron layer. Never guess from code — set up logging, monitor, rebuild, read, fix, repeat.

## When to Use

- Webview not loading / blank screen / spinner stuck
- Bridge not connecting / messages not flowing
- Preload not exposing APIs / contextBridge failures
- IPC handlers not firing
- Process spawn failures (app-server, TTS server, PTY)
- Any "it loads but doesn't work" symptom

## Step 1: Create a Disk Logger

Create a debug log file for the component. Follow the canonical pattern:

**File:** `apps/desktop/src/litesuite/<component>/<component>-debug-log.ts`

```typescript
import { appendFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";

const LOG_DIR = join(homedir(), ".litesuite");
const LOG_FILE = join(LOG_DIR, "<component>-debug.log");

let initialized = false;

export function initDebugLog(): void {
  if (initialized) return;
  try {
    mkdirSync(LOG_DIR, { recursive: true });
    writeFileSync(
      LOG_FILE,
      `=== LiteSuite <Component> Debug Log - ${new Date().toISOString()} ===\n`,
    );
    initialized = true;
  } catch (err) {
    console.error("[<component>-debug] Failed to initialize:", err);
  }
}

export function clog(...args: unknown[]): void {
  const line = `[${new Date().toISOString()}] ${args
    .map((arg) => (typeof arg === "object" ? JSON.stringify(arg) : String(arg)))
    .join(" ")}`;
  console.log("[<component>]", ...args);
  if (!initialized) {
    try {
      mkdirSync(LOG_DIR, { recursive: true });
      appendFileSync(LOG_FILE, line + "\n");
    } catch {
      /* silent */
    }
    return;
  }
  try {
    appendFileSync(LOG_FILE, line + "\n");
  } catch {
    /* silent */
  }
}

export function getLogPath(): string {
  return LOG_FILE;
}
```

**Existing loggers:**

- Voice: `apps/desktop/src/litesuite/voice/debug-log.ts` → `~/.litesuite/voice-debug.log`
- Codex: `apps/desktop/src/litesuite/codex/codex-debug-log.ts` → `~/.litesuite/codex-debug.log`

## Step 2: Add Logging at Every Boundary

Log at EVERY point where data crosses a boundary:

### Main Process (services, managers, handlers)

```typescript
import { clog, initDebugLog } from "../<component>/<component>-debug-log";
initDebugLog();

// Function entry
clog(`[manager] initSession id=${id} path=${path}`);

// IPC receive
clog(`[bridge] webview→bridge: session=${sessionId} type=${msgType}`);

// IPC send
clog(`[bridge] bridge→webview: type=${message?.type}`);

// Process spawn
clog(`[bridge] spawning binary: ${exePath} args=${args.join(" ")}`);

// Error
clog(`[manager] FAIL: ${error.message}`);
```

### Preload (renderer context — no disk access)

```typescript
// Preload can't write to disk directly. Use console.log with a prefix.
// The main process captures these via webContents.on("console-message").
function plog(...args: unknown[]): void {
  console.log("[<component>-preload]", ...args);
}
```

### Forward webview console to disk log

```typescript
view.webContents.on("console-message", (_event, level, message) => {
  clog(`[webview-console] L${level}: ${message}`);
});
```

### Error trapper in wrapper HTML

For webview issues, inject a global error handler BEFORE any scripts:

```typescript
const errorTrapper = `<script>window.addEventListener("error",function(e){
  console.error("[error-trap] "+e.message+" at "+e.filename+":"+e.lineno+":"+e.colno);
  console.error("[error-trap] stack: "+(e.error&&e.error.stack||"none"));
});</script>`;

html = html.replace("<head>", `<head>\n${errorTrapper}`);
```

## Step 3: Arm a Monitor

```bash
# Filtered — only errors and key events
Monitor({ description: "<Component> debug log", persistent: false, timeout_ms: 600000,
  command: "tail -f ~/.litesuite/<component>-debug.log | grep --line-buffered 'error|Error|fatal|timeout|FAIL|initialized|handshake'" })

# Unfiltered — see everything
Monitor({ description: "<Component> debug log (full)", persistent: false, timeout_ms: 300000,
  command: "tail -f ~/.litesuite/<component>-debug.log | grep --line-buffered '.'" })
```

## Step 4: Rebuild and Launch

Run from your LiteSuite repository root (requires LiteSuite source checked out):

```bash
# Replace <litesuite-root> with your LiteSuite repository path
cd <litesuite-root> && taskkill //f //im electron.exe 2>/dev/null
bunx turbo run build --filter=@litesuite/desktop --force 2>&1 | tail -3
bun run start:desktop 2>&1 &
```

> **Note:** This skill targets LiteSuite's Electron codebase. If you are debugging a different Electron app, adapt the build command to your project's package manager and script names.

The user always restarts the app after changes. Never ask if they restarted. If a change isn't reflected, the code is wrong.

## Step 5: Read → Fix → Add More Logging → Repeat

1. User triggers the flow (opens a panel, clicks a button)
2. Read the Monitor output — what happened? Where did it stop?
3. Fix the specific issue the log reveals
4. If the issue isn't clear, add MORE logging at the gap
5. Rebuild and check again
6. Repeat until the flow completes without errors

## Common Patterns Found via This Method

| Symptom           | Log Reveals                                                    | Fix                                                                                 |
| ----------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Blank webview     | `Unable to load preload script: An object could not be cloned` | Proxy/non-cloneable objects can't cross contextBridge                               |
| Spinner stuck     | `Uncaught Error: Invalid semantic version: undefined`          | Missing field in preload shim (e.g., `appVersion`)                                  |
| "Not Found"       | `findExtension: null`                                          | Discovery path wrong or file doesn't exist at expected location                     |
| Handshake timeout | `fatal-error` after 15s, no `initialized` event                | Wrong binary (`.cmd` vs `.exe`), wrong spawn options, or binary needs `shell: true` |
| Bridge silent     | No `webview→bridge` messages                                   | Preload failed to expose the API, or IPC channel name mismatch                      |

## Anti-Patterns

- Reading code and guessing without running it
- Making multiple changes between rebuilds without checking logs
- Using `console.log` alone without disk persistence (lost on crash)
- Asking "did you restart?" (the user always does)
- Adding a Proxy to contextBridge objects (they can't be cloned)

## Cleanup

After debugging is complete:

- Keep the disk logger (it's useful for future debugging)
- Remove verbose per-message logging if it's too noisy for production
- Keep error-level logging permanently
- The debug log file is overwritten on each app launch (the `initDebugLog` call)

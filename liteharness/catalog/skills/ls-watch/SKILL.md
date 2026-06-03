---
name: ls-watch
description: Spawn background bash watchers that monitor for conditions and notify the conversation when triggered. Watch for files to appear, ports to open/close, processes to exit, commands to succeed, regex patterns in log files, HTTP endpoints to respond, or RSS/Atom feeds for new entries. Triggers on '/watch', 'watch for', 'notify me when', 'alert when', 'wait for', 'monitor until', 'tell me when', 'watch this port', 'watch this file', 'watch this process', 'watch this URL', 'watch this feed', 'watch this channel', 'alert me when a new video', 'monitor this site'.
---

# /watch - Background Condition Watchers

Spawn background bash scripts that poll for conditions and notify the conversation via Claude Code's `run_in_background` task notification system. When the condition is met, the watcher script exits successfully, the background task completes, and you receive the notification automatically.

## Syntax

```
/watch <type> <target> [options]
```

## Watcher Types

### 1. `file` - Watch for a file or directory to appear

```
/watch file /tmp/build-done.txt
/watch file /tmp/build-done.txt --timeout 10m --interval 2s
/watch file /tmp/output/ --then "echo build complete"
```

### 2. `port` - Watch for a port to open or close

```
/watch port 3000 open        # wait for port to start listening
/watch port 3000 close       # wait for port to stop listening
/watch port 3000             # defaults to 'open'
```

### 3. `pid` - Watch for a process to exit

```
/watch pid 12345
/watch pid 12345 --timeout 30m
```

### 4. `command` - Re-run a command until it succeeds (exit 0)

```
/watch command "curl -s localhost:3000/health"
/watch command "docker ps | grep myapp" --interval 5s
```

### 5. `pattern` - Tail a file until a regex matches

```
/watch pattern build.log "BUILD SUCCESSFUL"
/watch pattern /var/log/app.log "ERROR.*database"
/watch pattern output.log "Done in \d+\.\d+s" --timeout 15m
```

### 6. `web` - Watch an HTTP endpoint for a condition

```
/watch web https://litesuite.dev --expect 200
/watch web https://api.example.com/health --expect-body "operational"
/watch web https://litesuite.dev --expect-header "x-version: 2.0"
/watch web https://example.com/deploy --expect-body "success" --method POST
```

Options specific to `web`:

| Option                            | Default | Description                                             |
| --------------------------------- | ------- | ------------------------------------------------------- |
| `--expect <status>`               | `200`   | Expected HTTP status code                               |
| `--expect-body <string>`          | none    | String that must appear in response body                |
| `--expect-header <header: value>` | none    | Header+value that must be present                       |
| `--method <GET\|POST\|HEAD>`      | `GET`   | HTTP method                                             |
| `--headers <json>`                | none    | Extra headers as JSON: `'{"Authorization":"Bearer x"}'` |

### 7. `feed` - Watch an RSS/Atom feed for new entries matching keywords

```
/watch feed "https://www.youtube.com/feeds/videos.xml?channel_id=UCX6..." --match "claude|cursor"
/watch feed "https://hnrss.org/newest" --match "anthropic|claude" --interval 2m --timeout 1h
/watch feed "https://blog.example.com/feed.xml" --name "eng-blog"
```

The `feed` watcher is designed for RSS/Atom feeds (YouTube channels, blogs, HN, subreddits). It:

1. On first poll, snapshots all existing `<id>` / `<guid>` entries as "already seen"
2. On subsequent polls, detects entries with IDs not in the snapshot
3. If `--match` is specified, only fires when a new entry's title or description matches the regex
4. If `--match` is omitted, fires on ANY new entry

Options specific to `feed`:

| Option            | Default | Description                                                                      |
| ----------------- | ------- | -------------------------------------------------------------------------------- |
| `--match <regex>` | none    | Only fire when a new entry's title/summary matches this regex (case-insensitive) |

**YouTube shorthand:** If the target looks like a YouTube channel URL (`youtube.com/@handle` or `youtube.com/channel/...`), auto-convert it to the RSS feed URL. If it's just a channel ID, build the feed URL automatically.

Output JSON for `feed`:

```json
{
  "event": "new_feed_entry",
  "target": "https://www.youtube.com/feeds/videos.xml?channel_id=...",
  "title": "Claude Code vs Cursor: The Real Test",
  "url": "https://youtube.com/watch?v=abc123",
  "author": "Some Channel",
  "published": "2026-03-26T14:30:00Z",
  "timestamp": "...",
  "elapsed_seconds": 120
}
```

**Auto-reaction for YouTube feeds:** When a `feed` watcher fires with a YouTube video URL, proactively offer to run `/youtube-transcript` on it. If the user included `--then transcript` as the then-command, invoke `/youtube-transcript` automatically without asking.

## Options

| Option                  | Default | Description                                              |
| ----------------------- | ------- | -------------------------------------------------------- |
| `--interval <duration>` | `1s`    | Poll interval (e.g., `1s`, `5s`, `30s`, `1m`, `5m`)      |
| `--timeout <duration>`  | `5m`    | Max wait time before giving up (e.g., `30s`, `5m`, `1h`) |
| `--then <command>`      | none    | Run this command when condition is met, before exiting   |
| `--name <label>`        | auto    | Human-readable label for this watcher                    |

## Implementation

When the user invokes `/watch`, follow these steps exactly:

### Step 1: Parse the Request

Extract from the user's input:

- **type**: one of `file`, `port`, `pid`, `command`, `pattern`
- **target**: the file path, port number, PID, command string, or file+regex
- **options**: interval, timeout, then-command, name

Apply defaults for anything not specified.

### Step 2: Generate the Watcher Script

Generate a bash script and write it to a temp file. Use this template structure:

```bash
#!/usr/bin/env bash
# Watcher: <name>
# Type: <type> | Target: <target>
# Timeout: <timeout> | Interval: <interval>

INTERVAL=<seconds>
TIMEOUT=<seconds>
ELAPSED=0
EVENT_TYPE="<event_type>"

while [ $ELAPSED -lt $TIMEOUT ]; do
  # --- condition check (varies by type) ---
  if <condition>; then
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    # Run --then command if specified
    THEN_OUTPUT=""
    <then_block>
    # Output JSON payload
    cat <<ENDJSON
{"event":"$EVENT_TYPE","target":"<target>","timestamp":"$TIMESTAMP","elapsed_seconds":$ELAPSED,"details":"<details>","then_output":"$THEN_OUTPUT"}
ENDJSON
    exit 0
  fi
  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))
done

# Timeout reached
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat <<ENDJSON
{"event":"timeout","target":"<target>","timestamp":"$TIMESTAMP","elapsed_seconds":$ELAPSED,"details":"Timed out after ${TIMEOUT}s waiting for $EVENT_TYPE"}
ENDJSON
exit 1
```

#### Condition checks by type:

**file:**

```bash
if [ -e "<path>" ]; then
```

- event: `file_appeared`
- details: file path

**port (open):**

```bash
if (echo >/dev/tcp/localhost/<port>) 2>/dev/null; then
```

- event: `port_opened`
- details: port number

**port (close):**

```bash
if ! (echo >/dev/tcp/localhost/<port>) 2>/dev/null; then
```

- event: `port_closed`
- details: port number

**pid:**

```bash
if ! kill -0 <pid> 2>/dev/null; then
```

- event: `process_exited`
- details: PID

**command:**

```bash
CMD_OUTPUT=$(<command> 2>&1)
if [ $? -eq 0 ]; then
```

- event: `command_succeeded`
- details: first 200 chars of stdout

**pattern:**

```bash
if [ -f "<file>" ]; then
  MATCH=$(grep -m1 -E '<regex>' "<file>" 2>/dev/null)
  if [ -n "$MATCH" ]; then
```

- event: `pattern_matched`
- details: the matched line (first 200 chars)
- Note: For pattern type, on each poll check the ENTIRE file with `grep`, not just new lines. This is simpler and handles log rotation. For very large files, use `tail -n 1000` piped to grep.

**web:**

```bash
RESPONSE=$(curl -s -o /tmp/watch-web-body-$$ -w "%{http_code}" -X <METHOD> <header_flags> "<url>" 2>/dev/null)
# Check status code
if [ "$RESPONSE" = "<expected_status>" ]; then
  # If --expect-body, also check body
  BODY_OK=true
  if [ -n "<expect_body>" ]; then
    if ! grep -q "<expect_body>" /tmp/watch-web-body-$$ 2>/dev/null; then
      BODY_OK=false
    fi
  fi
  # If --expect-header, also check header
  HEADER_OK=true
  # (header check uses curl -D for headers)
  if $BODY_OK && $HEADER_OK; then
```

- event: `web_condition_met`
- details: HTTP status code + first 200 chars of body

**feed:**

The feed watcher is more complex. The generated script:

1. First poll: fetch feed, extract all `<id>` (Atom) or `<guid>` (RSS) values into a "seen" file
2. Subsequent polls: fetch feed, extract IDs, diff against seen file
3. For new entries: extract title, link, author, published date
4. If `--match` is set, check title+summary against the regex
5. Fire on first match

```bash
SEEN_FILE="/tmp/watch-feed-seen-$$"
FEED_URL="<url>"

# First poll — snapshot existing entries
curl -s "$FEED_URL" > /tmp/watch-feed-raw-$$
grep -oP '(?<=<id>)[^<]+|(?<=<guid>)[^<]+|(?<=<guid isPermaLink="false">)[^<]+|(?<=<guid isPermaLink="true">)[^<]+' /tmp/watch-feed-raw-$$ | sort -u > "$SEEN_FILE"
sleep $INTERVAL
ELAPSED=$((ELAPSED + INTERVAL))

# Subsequent polls
while [ $ELAPSED -lt $TIMEOUT ]; do
  curl -s "$FEED_URL" > /tmp/watch-feed-raw-$$
  grep -oP '(?<=<id>)[^<]+|(?<=<guid>)[^<]+|(?<=<guid isPermaLink="false">)[^<]+|(?<=<guid isPermaLink="true">)[^<]+' /tmp/watch-feed-raw-$$ | sort -u > /tmp/watch-feed-current-$$

  # Find new IDs
  NEW_IDS=$(comm -23 /tmp/watch-feed-current-$$ "$SEEN_FILE")

  if [ -n "$NEW_IDS" ]; then
    # Extract details for the first new entry
    FIRST_NEW_ID=$(echo "$NEW_IDS" | head -1)
    # Parse title, link, author from the raw XML around that ID
    # (uses awk/sed to extract the <entry> or <item> block containing the ID)
    TITLE=$(...)  # extracted from XML
    LINK=$(...)   # extracted from XML
    AUTHOR=$(...)
    PUBLISHED=$(...)

    # If --match specified, check against regex
    if [ -n "<match_regex>" ]; then
      if echo "$TITLE $SUMMARY" | grep -iqE "<match_regex>"; then
        # MATCH — fire the event
        <output JSON and exit>
      fi
    else
      # No match filter — any new entry fires
      <output JSON and exit>
    fi

    # Update seen file with new IDs (so we don't re-check them)
    cat /tmp/watch-feed-current-$$ > "$SEEN_FILE"
  fi

  sleep $INTERVAL
  ELAPSED=$((ELAPSED + INTERVAL))
done
```

- event: `new_feed_entry`
- details: title, url, author, published date
- Note: XML parsing in bash is fragile. The script uses grep/sed/awk for extraction — good enough for YouTube RSS and standard Atom/RSS feeds. For complex feeds, the `command` type with a Python one-liner is more robust.

**YouTube channel shorthand:** When the user provides a YouTube channel URL or ID instead of a feed URL, convert it:

- `youtube.com/@handle` → first `curl -s "https://www.youtube.com/@handle"` and extract channel ID from the page, then build `https://www.youtube.com/feeds/videos.xml?channel_id=<ID>`
- `youtube.com/channel/UC...` → `https://www.youtube.com/feeds/videos.xml?channel_id=UC...`
- Raw `UC...` string → `https://www.youtube.com/feeds/videos.xml?channel_id=UC...`

#### --then block:

If `--then` is specified:

```bash
THEN_OUTPUT=$(<then_command> 2>&1 | head -c 500)
```

If not specified, omit the block entirely.

### Step 3: Write and Launch

1. Generate a unique ID: `watch-<type>-<short_hash>` (use `$RANDOM` or timestamp)
2. Write the script to `$TEMP/watch-<id>.sh` using the Write tool
3. Launch it with the Bash tool using `run_in_background: true`:

```
bash "$TEMP/watch-<id>.sh"
```

4. Tell the user the watcher is running:

> Watcher `<name>` started. Monitoring <description>. Timeout: <timeout>.

### Step 4: React on Notification

When the background task completes and you receive the notification:

1. Parse the JSON output
2. If `event` is NOT `timeout`:
   - Report: "Watcher **<name>** triggered: <event description>"
   - If `then_output` is non-empty, show it
3. If `event` is `timeout`:
   - Report: "Watcher **<name>** timed out after <timeout> without detecting <condition>"

## Duration Parsing

Convert human durations to seconds for the script:

- `1s` → 1
- `5s` → 5
- `30s` → 30
- `1m` → 60
- `5m` → 300
- `10m` → 600
- `30m` → 1800
- `1h` → 3600

## Multiple Watchers

Users can launch multiple watchers in parallel. Each gets its own script and background task. Example:

```
/watch port 3000 open --name "api-server"
/watch port 5173 open --name "vite-dev"
/watch file dist/index.html --name "build-output"
```

Launch all three as separate `run_in_background` Bash calls in a single response.

## Safety

- **Max timeout:** 1 hour. Reject anything longer.
- **Min interval:** 1 second. Reject anything shorter.
- **Max interval:** 5 minutes.
- **No infinite loops:** Every watcher MUST have a timeout.
- **Clean exit:** Scripts always produce JSON output, even on timeout.
- **No secrets in scripts:** Never embed API keys or passwords in watcher scripts. If a command needs auth, tell the user to set env vars.

## Examples

### Wait for a dev server to start

```
/watch port 3000 --name "dev-server" --then "curl -s localhost:3000 | head -c 100"
```

### Wait for a build to finish

```
/watch pattern build.log "BUILD SUCCESSFUL" --timeout 10m --name "build"
```

### Wait for a process to die

```
/watch pid 54321 --timeout 30m --name "long-running-job"
```

### Chain: wait for file, then process it

```
/watch file /tmp/export.csv --then "wc -l /tmp/export.csv" --name "export-ready"
```

### Health check loop

```
/watch command "curl -sf http://localhost:8080/healthz" --interval 5s --timeout 2m --name "healthcheck"
```

### Watch a website come back online

```
/watch web https://litesuite.dev --expect 200 --interval 10s --timeout 30m --name "site-up"
```

### Watch an API for a specific response

```
/watch web https://api.example.com/deploy/status --expect-body "complete" --interval 15s --name "deploy"
```

### Watch YouTube channel for new AI coding videos

```
/watch feed "https://www.youtube.com/feeds/videos.xml?channel_id=UCX6..." --match "claude|cursor|ai coding" --interval 60s --timeout 1h --name "ai-videos"
```

### Watch YouTube channel + auto-transcript

```
/watch feed UC... --match "claude code" --interval 60s --timeout 1h --then transcript --name "claude-vids"
```

### Watch Hacker News for mentions

```
/watch feed "https://hnrss.org/newest" --match "anthropic|claude|lite suite" --interval 2m --timeout 1h --name "hn-mentions"
```

## Persistent Monitoring (re-arming watchers)

`/watch` is a one-shot — it fires once and exits. For **persistent monitoring** that re-arms after each trigger, you can manually re-launch the watcher in the background after each notification, or write a wrapper script that loops around the watch script.

> **Note:** A `/loop` skill companion is not currently bundled in this plugin. To achieve persistent monitoring, either:
> - Re-invoke `/watch` after each notification fires, or
> - Write a small bash wrapper that loops indefinitely around the generated watcher script, sleeping for the desired re-arm interval between runs.

Example persistent-monitoring pattern (manual wrapper):

```bash
#!/usr/bin/env bash
# Re-arm loop: run /watch feed every 5 minutes
while true; do
  bash /tmp/watch-feed-<id>.sh  # your generated watcher script
  sleep 300  # 5m re-arm interval
done
```

Use cases this pattern enables:

| Pattern                                        | What it does                             |
| ---------------------------------------------- | ---------------------------------------- |
| Loop + `/watch feed ...`                       | Persistent YouTube/RSS monitor           |
| Loop + `/watch web ... --expect 200`           | Continuous uptime monitor                |
| Loop + `/watch command "git ls-remote ..."`    | Watch for new commits on a remote branch |
| Loop + `/watch pattern app.log "ERROR"`        | Continuous log error alerting            |

**Important:** Set the watcher timeout shorter than the re-arm interval to avoid overlap. Rule of thumb: `timeout = interval - 1m`.

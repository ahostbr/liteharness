---
name: ls-generative-ui
description: "LiteSuite Generative UI — render interactive widgets inline in Frontier Chat / Sentinel Chat. Use prompt_widget when you need the user's input to continue (confirmations, forms, picks). Use render_widget for fire-and-forget displays (charts, stat cards, dashboards). Triggers on 'render widget', 'prompt widget', 'ls-generative-ui', 'show me a chart', 'ask the user', 'confirm before', 'show a form', or whenever a visual or interactive answer is clearer than prose."
---

# Generative UI

Render interactive widgets directly inside the chat conversation. Two tools, three bands.

## Choose the right tool

| Tool            | Behavior                                                                  | Use when                                                            |
| --------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `prompt_widget` | **Blocks** until user interacts. Returns `{action, data}` as tool result. | Confirmations, forms, picks. You need the user's input to continue. |
| `render_widget` | **Fire-and-forget.** Returns immediately. Pure inline display.            | Charts, stat cards, status grids, dashboards. No response needed.   |

## Choose the right band

| Band      | When to use                                                        |
| --------- | ------------------------------------------------------------------ |
| `catalog` | Data fits a named `genui.*` component exactly. **Try this first.** |
| `specs`   | Multi-component layout (grids, tabs, dashboards).                  |
| `html`    | Custom interactivity, charts (Chart.js/D3), unique visuals.        |

## prompt_widget — required fields

When calling `prompt_widget`, you MUST provide:

- `type` — `"catalog"` | `"html"` | `"specs"`
- `requestId` — unique identifier per call. Generate `"widget-{8 random hex chars}"` (e.g. `"widget-7f3a9e2c"`). **Must be unique per call.**
- `agentId` — your agent identifier (session UUID or `"sentinel-chat"` if unknown)
- `timeout` — optional; auto-expire after N milliseconds. Omit to wait indefinitely.

Plus the band-specific fields below.

> **Legacy path:** `render_widget` with `interactive: true` (plus the same `requestId`/`agentId`) behaves identically to `prompt_widget` — same wire format, same handler, same response shape. Prefer `prompt_widget` for clarity. A blocking call shows as "pending" in the thread until the user acts — design for immediate action.

## Response shape

When the user interacts, `prompt_widget` returns:

```json
{
  "requestId": "widget-7f3a9e2c",
  "action": "click" | "submit" | "dismiss",
  "data": { /* depends on the widget */ },
  "timestamp": 1779449619556
}
```

## Catalog component reference

### Interactive (use with prompt_widget)

**`genui.Button`** — single click resolves with `{action:"click", data:{label, buttonId?}}`

```json
{ "label": "Confirm", "variant": "primary" }
```

**`genui.FormField`** — auto-wraps with Submit/Cancel; Submit resolves with `{action:"submit", data:{fields:{[name]:value}}}`

```json
{ "name": "email", "type": "text", "label": "Your email", "placeholder": "you@example.com" }
```

Types: `"text"`, `"textarea"`, `"number"`, `"select"`, `"checkbox"`, `"radio"`. For `select`/`radio` pass `options: [{label, value}, ...]`.

**`genui.Select`** — dropdown; same form-wrapped resolution as FormField

```json
{ "options": [{ "label": "A", "value": "a" }], "value": "a" }
```

### Display (use with render_widget)

| Component                | Props                                                          |
| ------------------------ | -------------------------------------------------------------- |
| `genui.StatCard`         | `{title, value, trend?, icon?}`                                |
| `genui.MetricRow`        | `{metrics: [{label, value}, ...]}`                             |
| `genui.DataTable`        | `{columns: ["A","B"], rows: [["x","y"]]}`                      |
| `genui.ProgressRing`     | `{value, max, label?}`                                         |
| `genui.TLDR`             | `{text}`                                                       |
| `genui.KeyTakeaways`     | `{items: [...]}`                                               |
| `genui.ExecutiveSummary` | `{title, sections: [{heading, body}]}`                         |
| `genui.StepCard`         | `{step, title, description}`                                   |
| `genui.CodeBlock`        | `{code, language}`                                             |
| `genui.CalloutCard`      | `{type:"info"\|"warning"\|"error"\|"success", title, message}` |
| `genui.LinkCard`         | `{url, title, description?}`                                   |
| `genui.ToolCard`         | `{name, description, status?}`                                 |
| `genui.BookCard`         | `{title, author, description?}`                                |
| `genui.StatusIndicator`  | `{status:"online"\|"offline"\|"warning", label}`               |
| `genui.CategoryBadge`    | `{category, color}`                                            |

### Layout (use with specs band)

`genui.Section` (`{title, children}`), `genui.Grid` (`{columns, children}`), `genui.Tabs` (`{tabs:[{label, content}]}`), `genui.Accordion` (`{items:[{title, content}]}`).

## HTML band

Write `content` as a **self-contained snippet** — no `<!DOCTYPE>`, no `<html>`, no `<head>`, no `<body>`. The iframe provides them.

Pre-loaded globals available in every HTML widget:

- `Chart` — Chart.js v4 (`new Chart(canvas, {...})`)
- `d3` — D3 v7

The iframe runs `sandbox="allow-scripts"` with no parent-DOM access, no same-origin.

### Interactive HTML — postMessage protocol

For `prompt_widget` html-band, your script MUST post a response to resolve the tool:

```js
window.parent.postMessage(
  {
    type: "widget-response", // exact string required
    action: "click", // or "submit", "dismiss", anything you choose
    data: {
      /* your data */
    },
  },
  "*",
);
```

The `type: "widget-response"` discriminator is required. Anything else is ignored. Your `action` + `data` flow back as the tool result.

### CDN allowlist

For libraries beyond Chart.js/D3, any `https:` script is allowed (sandbox is the boundary). Recommended sources: `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com`. External API calls (`fetch`/`XHR`) are blocked by CSP — keep widgets self-contained.

## Worked examples

### 1. Confirm action (prompt_widget catalog)

```json
{
  "type": "catalog",
  "component": "genui.Button",
  "props": { "label": "Ship it", "variant": "primary" },
  "title": "Deploy confirmation",
  "requestId": "widget-btn-7f3a9e2c",
  "agentId": "{your-id}"
}
```

### 2. Pick from a list (prompt_widget catalog form)

```json
{
  "type": "catalog",
  "component": "genui.FormField",
  "props": {
    "name": "model",
    "label": "Pick a model",
    "type": "select",
    "options": [
      { "label": "Opus", "value": "opus" },
      { "label": "Sonnet", "value": "sonnet" },
      { "label": "Haiku", "value": "haiku" }
    ]
  },
  "title": "Model selection",
  "requestId": "widget-pick-a1b2c3d4",
  "agentId": "{your-id}"
}
```

### 3. Display a chart (render_widget html)

```json
{
  "type": "html",
  "title": "Weekly Signups",
  "content": "<canvas id='c' style='max-height:380px'></canvas><script>new Chart(document.getElementById('c'),{type:'bar',data:{labels:['Mon','Tue','Wed','Thu','Fri'],datasets:[{label:'Signups',data:[42,58,35,71,90],backgroundColor:'#d4a853'}]},options:{responsive:true,plugins:{legend:{labels:{color:'#e8e0d4'}}},scales:{x:{ticks:{color:'#94a3b8'}},y:{ticks:{color:'#94a3b8'}}}}})</script>"
}
```

### 4. Custom interactive HTML (prompt_widget html)

```json
{
  "type": "html",
  "content": "<button id='b' style='padding:10px 20px;background:#7c3aed;color:white;border:0;border-radius:6px;cursor:pointer'>Click me</button><script>document.getElementById('b').addEventListener('click',function(){window.parent.postMessage({type:'widget-response',action:'click',data:{x:42}},'*');});</script>",
  "title": "Custom HTML widget",
  "requestId": "widget-html-9e2c7f3a",
  "agentId": "{your-id}"
}
```

### 5. Dashboard layout (render_widget specs)

```json
{
  "type": "specs",
  "title": "System Health",
  "specs": [
    {
      "component": "genui.Grid",
      "props": { "columns": 2 },
      "children": [
        {
          "component": "genui.StatCard",
          "props": { "title": "CPU", "value": "34%", "trend": "-2%" }
        },
        {
          "component": "genui.StatCard",
          "props": { "title": "Memory", "value": "6.1 GB", "trend": "+0.4 GB" }
        }
      ]
    },
    {
      "component": "genui.ToolCard",
      "props": { "name": "Redis", "description": "Cache layer", "status": "healthy" }
    }
  ]
}
```

## Appendix — full component JSON shapes

Copy-paste prop shapes for every catalog component (condensed table above is the same surface).

### Data

```json
genui.StatCard      { "title": "Active Users", "value": "12,483", "trend": "+8.2%", "icon": "users" }
genui.MetricRow     { "metrics": [{ "label": "Revenue", "value": "$42K" }, { "label": "Churn", "value": "2.1%" }] }
genui.DataTable     { "columns": ["Name", "Status", "Score"], "rows": [["Alice", "active", 94], ["Bob", "idle", 71]] }
genui.ProgressRing  { "value": 73, "max": 100, "label": "Completion" }
```

### Summary

```json
genui.TLDR              { "text": "Deployment succeeded. 3 services restarted. No errors." }
genui.KeyTakeaways      { "items": ["Latency up 12ms on p99", "Cache hit rate dropped to 68%"] }
genui.ExecutiveSummary  { "title": "Q2 Performance", "sections": [{ "heading": "Revenue", "body": "..." }, { "heading": "Risks", "body": "..." }] }
```

### Instructional

```json
genui.StepCard     { "step": 1, "title": "Install dependencies", "description": "Run `bun install` in the project root." }
genui.CodeBlock    { "code": "const x = 42;", "language": "typescript" }
genui.CalloutCard  { "type": "warning", "title": "Breaking Change", "message": "The `userId` field is now required." }
```

`CalloutCard.type`: `"info"` | `"warning"` | `"error"` | `"success"`

### Resources

```json
genui.LinkCard  { "url": "https://example.com/docs", "title": "API Reference", "description": "Full endpoint documentation" }
genui.ToolCard  { "name": "PostgreSQL", "description": "Primary database", "status": "healthy" }
genui.BookCard  { "title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann", "description": "Replication, sharding, consistency." }
```

### Layout

```json
genui.Section    { "title": "Infrastructure", "children": [ ... ] }
genui.Grid       { "columns": 2, "children": [ ... ] }
genui.Tabs       { "tabs": [{ "label": "Overview", "content": "..." }, { "label": "Details", "content": "..." }] }
genui.Accordion  { "items": [{ "title": "What is X?", "content": "X is..." }] }
```

### Interactive

```json
genui.FormField  { "name": "email", "label": "Email Address", "type": "email" }
genui.Button     { "label": "Confirm", "variant": "primary" }
genui.Select     { "options": ["Option A", "Option B", "Option C"], "value": "Option A" }
```

### Tags

```json
genui.StatusIndicator  { "status": "online", "label": "API Gateway" }
genui.CategoryBadge    { "category": "backend", "color": "blue" }
```

## Codex agents

Codex agents get the same `render_widget` tool via MCP — hand them the companion reference in this skill's directory: `codex-generative-ui.md` (tool schema + band selection + examples in Codex-friendly form).

## Best practices

- **Catalog first.** Catalog components are theme-matched. Reach for `html` only when the design isn't expressible as catalog/specs.
- **Don't over-widget.** A single clear widget beats five cluttered ones. Use `genui.Grid` or `specs` to group.
- **Pair tool to intent.** Use `render_widget` for displays you don't need a response to. Use `prompt_widget` when you'll actually use the user's answer.
- **Always set a `title`** — it gives the widget a header and makes the conversation easier to reference.
- **HTML must be self-contained.** No external API calls. All styles + scripts inline.
- **One round-trip per `prompt_widget`.** The tool resolves on the first user response. For multi-step interaction, chain multiple `prompt_widget` calls.

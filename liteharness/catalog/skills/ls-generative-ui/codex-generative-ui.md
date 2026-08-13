# Generative UI — render_widget (Codex companion)

You have access to a `render_widget` MCP tool that renders interactive widgets inline in the chat conversation. For BLOCKING interactions (confirmations, forms, picks) the sibling tool `prompt_widget` shares the same shape and additionally requires `requestId` + `agentId` — see SKILL.md in this directory.

## Tool Schema

```json
{
  "name": "render_widget",
  "description": "Render an interactive widget inline in the current chat conversation.",
  "inputSchema": {
    "type": "object",
    "required": ["type"],
    "properties": {
      "type": { "type": "string", "enum": ["catalog", "html", "specs"] },
      "component": { "type": "string", "description": "genui.* component name (catalog band)" },
      "props": { "type": "object", "description": "Component props (catalog band)" },
      "content": {
        "type": "string",
        "description": "Self-contained HTML/CSS/JS snippet (html band)"
      },
      "specs": { "type": "array", "description": "Declarative component tree (specs band)" },
      "title": { "type": "string", "description": "Optional widget title" }
    }
  }
}
```

## When to Use

Call `render_widget` when showing data visually would be clearer than prose: metrics, charts, tables, dashboards, step-by-step instructions, status views, or interactive forms.

## Band Selection

- **`catalog`** — Use a named `genui.*` component when the data fits. Fastest, theme-consistent.
- **`specs`** — Combine multiple catalog components into a layout (grid, tabs, sections).
- **`html`** — Custom charts (Chart.js, D3), animations, or bespoke interactivity. Self-contained HTML/CSS/JS only.

## Catalog Components (22)

**Data:** `genui.StatCard` (title, value, trend?, icon?), `genui.MetricRow` (metrics[]), `genui.DataTable` (columns[], rows[]), `genui.ProgressRing` (value, max, label?)

**Summary:** `genui.TLDR` (text), `genui.KeyTakeaways` (items[]), `genui.ExecutiveSummary` (title, sections[])

**Instructional:** `genui.StepCard` (step, title, description), `genui.CodeBlock` (code, language?), `genui.CalloutCard` (type, title, message)

**Resources:** `genui.LinkCard` (url, title, description?), `genui.ToolCard` (name, description, status?), `genui.BookCard` (title, author, description?)

**Layout:** `genui.Section` (title, children), `genui.Grid` (columns?, children), `genui.Tabs` (tabs[{label,content}]), `genui.Accordion` (items[{title,content}])

**Interactive:** `genui.FormField` (name, label, type), `genui.Button` (label, variant?), `genui.Select` (options[], value?)

**Tags:** `genui.StatusIndicator` (status, label?), `genui.CategoryBadge` (category, color?)

## Examples

**Catalog — single stat:**

```json
{
  "type": "catalog",
  "component": "genui.StatCard",
  "props": { "title": "Uptime", "value": "99.97%", "trend": "+0.02%" },
  "title": "Service Health"
}
```

**HTML — Chart.js bar chart.** `Chart` (Chart.js v4) and `d3` (D3 v7) are PRE-LOADED globals in every html widget — no script tag needed. Write `content` as a self-contained snippet: NO `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>` — the iframe provides them.

```json
{
  "type": "html",
  "title": "Weekly Signups",
  "content": "<canvas id=\"c\"></canvas><script>new Chart(document.getElementById('c'),{type:'bar',data:{labels:['Mon','Tue','Wed','Thu','Fri'],datasets:[{label:'Users',data:[42,58,35,71,90]}]}})</script>"
}
```

**Specs — dashboard grid:**

```json
{
  "type": "specs",
  "title": "Health Dashboard",
  "specs": [
    {
      "component": "genui.Grid",
      "props": { "columns": 2 },
      "children": [
        { "component": "genui.StatCard", "props": { "title": "CPU", "value": "34%" } },
        { "component": "genui.StatCard", "props": { "title": "Memory", "value": "6.1 GB" } }
      ]
    }
  ]
}
```

## CDN Allowlist (html band only)

For libraries beyond the pre-loaded Chart.js/D3: `cdn.jsdelivr.net`, `cdnjs.cloudflare.com`, `unpkg.com`, `d3js.org`.

External API calls from html content are blocked by sandbox CSP.

## Rules

- Prefer `catalog` over `html` — catalog is theme-consistent and faster to render.
- Use `specs` for multi-component views, not repeated `catalog` calls.
- Keep `html` content fully self-contained — no external API calls, all data inline.

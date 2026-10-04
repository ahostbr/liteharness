---
name: ls-theater
description: Use to SHOW the human anything visual inside LiteSuite (mockups, page previews, data visualizations, reports, screenshots, prototypes) or to ASK them things a chat question does badly (several questions at once, choice cards, a scale, image or file uploads, an approve/revise/reject verdict). Theater compiles a TSX page, opens it in a new LiteSuite browser pane, and sends every answer back to you as an ANSWER message in your inbox. Triggers on 'theater', 'mockup', 'create a mockup', 'page preview', 'visual prototype', 'visualize this', 'make this pretty', 'visualize this document', 'interactive mockup', 'driven mockup', 'generative UI', 'show me', 'let me answer questions', 'let me upload images'.
---

# Theater

Theater is where an agent shows the human things inside LiteSuite. You write one page as TSX,
LiteSuite compiles it live and opens it in a **new browser pane**, and whatever the human
answers on it comes straight back to **you**, the agent that opened it.

**Quick yes/no or pick-one question? Use `AskUserQuestion` instead.** Theater is for anything
visual, anything with several questions, and anything with uploads.

## The loop

```
you write page.tsx  ->  ask open  ->  a new pane shows it
                                              |
                          the human answers   v
                        session.json (+ uploads/)  ->  an ANSWER message in YOUR inbox
                                              |
you read the answer, edit page.tsx  ->  the open pane reloads itself
```

1. **Write the page** at `<cwd>/.litesuite/theater/<pageId>/page.tsx`, or pass it as `source`.
2. **Open it:** `ask open pageId=<id> title="<what you are asking>"` (MCP tool `ask`,
   or `lst run ask action=open …`). `theater` is a one-release compatibility alias;
   migrate to `ask` before the next release after T1150. One call opens one NEW pane. Calling `open` again for
   the same `pageId` reloads that pane in place and opens no second one.
3. **Answers arrive in your inbox**, one ANSWER message per changed answer, through the inbox
   watcher you already run. **You arm nothing.** The exception is below: if `open` says
   `NO INBOX REACHABLE`, arm the Monitor it printed.
4. **Rewrite the page so the human can see the answer landed.** Editing any file in the page
   folder reloads the open pane; you do not tell them to refresh.

Who gets the answers: the agent whose id LiteSuite finds in the tool's environment, and never
an id you type. A leader's page answers the leader; a worker's page answers the worker. The pane
header shows "Asked by <name> (<tier>)" so the human knows who is waiting. **One opener per
`pageId`:** whoever opens a page first keeps its answers, and a later `open` of the same
`pageId` by anyone else revises the page but does not take them.

**Calling the tool over HTTP MCP (`http://localhost:7423/mcp`)? Pass `projectDir`** (an
absolute path) on `open`, `question` and `status`. That path runs the tool with LiteSuite's
working directory, not yours, so without it the page lands under LiteSuite's.

## Actions

| Action     | Arguments                              | Does                                                                                   |
| ---------- | -------------------------------------- | -------------------------------------------------------------------------------------- |
| `open`     | `pageId`, `title`, `source?`, `projectDir?` | compile and show the page in a new pane (same `pageId` → reload in place)             |
| `question` | `pageId`, `questionId`, `wait?`, `timeout?`, `projectDir?` | the answer so far; `wait=true` blocks until it is answered (default 1800 s)      |
| `media`    | `path` or `url`, `title?`              | show an image, video or audio file                                                     |
| `status`   | `pageId`, `projectDir?`                | title, owner, revision, which questions are answered                                   |
| `help`     |                                        | this table                                                                             |

Use `wait=true` only when you have nothing else to do until the human answers. Otherwise keep
working: the ANSWER message will interrupt you.

## Writing a page

A page is ONE default-exported React component in `page.tsx`. Local files next to it
(`./lib/data.ts`, `./Chart.tsx`) are bundled with it. Local images, videos, and other media go
under the page's `assets/` directory and are referenced as `assets/<path>` (for example,
`<img src="assets/screenshots/hero.png" />` or `<video src="assets/clips/demo.mp4" controls />`).

```tsx
import {
  useTheaterSession, SessionBanner, Recommendation,
  AskChoice, AskText, AskScale, AskFiles, AskReview,
  StatCard, MetricRow,
} from "@theater/kit";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip } from "recharts";

export default function Page() {
  const session = useTheaterSession();
  return (
    <main style={{ maxWidth: 880, margin: "0 auto", padding: "32px 20px", display: "grid", gap: 16 }}>
      <SessionBanner session={session} title="Pick a direction" />

      <Recommendation title="Recommended: B">Because it keeps the canvas free.</Recommendation>

      <div style={{ height: 260 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={[{ d: "Mon", v: 3 }, { d: "Tue", v: 7 }]}>
            <XAxis dataKey="d" /><YAxis /><Tooltip />
            <Line dataKey="v" stroke="var(--accent-color)" dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <AskChoice session={session} promptId="direction" title="1 · Which direction?"
        options={[{ value: "a", label: "A", desc: "why" }, { value: "b", label: "B" }]} />
      <AskText session={session} promptId="direction.note" title="Note on 1" />
      <AskChoice session={session} promptId="sections" multi title="2 · Which sections?"
        options={[{ value: "hero", label: "Hero" }, { value: "proof", label: "Proof" }]} />
      <AskScale session={session} promptId="density" title="3 · How dense?" min={1} max={5}
        minLabel="airy" maxLabel="packed" />
      <AskFiles session={session} promptId="refs" title="4 · Drop reference images" />
      <AskReview session={session} promptId="verdict" title="Verdict on this draft" />
      <AskText session={session} promptId="feedback" title="Anything else?" />
    </main>
  );
}
```

### What a page can import

Only these bare imports resolve; anything else fails to load, and the pane shows the error.

| Import                                                  | What                                                                 |
| ------------------------------------------------------- | -------------------------------------------------------------------- |
| `react`, `react-dom`                                    | React 19                                                             |
| `@theater/kit`                                          | the driven controls, the genui catalog, and the Light Mock-Up extras |
| `recharts`                                              | standard charts (the default for most charts)                        |
| `@nivo/bar` `line` `pie` `radar` `sankey` `treemap` `heatmap` `network` | rich themed charts; sankey, treemap, heatmap, network |
| `@visx/visx`                                            | custom SVG primitives. **One package**: `import { Group, scaleLinear } from "@visx/visx"` |
| `d3`                                                    | scales, layouts and transforms. Render with React, never d3 DOM calls |
| `react-force-graph-2d`, `react-force-graph-3d`          | node networks                                                         |
| `three`, `@react-three/fiber`, `@react-three/drei`      | custom 3D                                                             |
| `motion/react`                                          | animation                                                             |
| `lucide-react`                                          | icons                                                                 |

**No `next/*`** (there is no Next server) and **no Tailwind utility classes in page source**
(they are not compiled in v1). Style with the kit's components, inline styles and the theme's
CSS variables (`var(--accent-color)`, `var(--color-bone)`, `var(--color-stone)`,
`var(--color-panel)`, `var(--color-void)`). The page follows LiteSuite's current theme and
accent.

### The driven controls (`@theater/kit`)

| Control                     | Answer                                   | Use for                                    |
| --------------------------- | ---------------------------------------- | ------------------------------------------ |
| `Recommendation`            | none (display)                           | your recommendation, above the question    |
| `AskChoice`                 | `string`                                 | one decision, as choice cards              |
| `AskChoice multi`           | `string[]`                               | pick any number                            |
| `AskSelect`                 | `string`                                 | one pick from a short list                 |
| `AskText`                   | `string`                                 | a per-question note, feedback, free text   |
| `AskScale`                  | `number`                                 | taste dials: density, boldness, risk       |
| `AskFiles` / `DropImages`   | files on disk                            | screenshots, sketches, any file            |
| `AskReview`                 | `approve\|revise\|reject` + `<id>.note`   | the verdict gate                           |
| `SessionBanner`             | none                                     | the page title, and where answers go       |

Every control takes `session`, `promptId`, `title` and an optional `hint`. There is no submit
button: each answer saves the moment it is given. `AskText` saves when the box loses focus, so
you get a finished thought rather than a keystroke.

**The genui catalog** is exported too, and renders exactly as it does in Frontier chat:
`StatCard`, `MetricRow`, `DataTable`, `ProgressRing`, `TLDR`, `KeyTakeaways`,
`ExecutiveSummary`, `StepCard`, `CodeBlock`, `CalloutCard`, `LinkCard`, `ToolCard`, `BookCard`,
`Section`, `Grid`, `Tabs`, `Accordion`, `FormField`, `Button`, `Select`, `StatusIndicator`,
`CategoryBadge`, `BeforeAfter`, `GaugeBoard`, `Leaderboard`, `Timeline`, `Graph`, `Treemap`,
`DecisionBrief`, `PlanBoard`, `TriageList`, `CapabilitiesForm`, `ChoiceGrid`, `Scale`,
`DropZone`, `ReviewGate`.

**Light Mock-Up extras:** `CountUp`, `ScrollReveal`, `CostBar`, `Screenshot3D`, `PanelGrid`,
`AgentConstellation`, `MockupLibraryCard`.

> 🪤 **`CountUp`'s prop is `end`, NOT `target`.** The wrong name renders **`NaN`** where the
> number should be, serves fine and throws nothing. A text read of the page will not catch it;
> only looking at the pane does. Look at the page before you call it done.

> 🪤 To collect an answer use the Ask* wrapper, never the raw ChoiceGrid/Scale/DropZone/ReviewGate: the raw control renders but never reaches the session, so no ANSWER is sent.

### Pick the visualization the data calls for

| Data story                                    | Best viz                                    |
| --------------------------------------------- | ------------------------------------------- |
| How things flow (budget, users, pipeline)     | Sankey (`@nivo/sankey`)                     |
| How things relate (deps, agents, systems)     | Force graph (`react-force-graph-2d/3d`)     |
| How things compare across dimensions          | Radar (`@nivo/radar`)                       |
| Hierarchical proportions                      | Treemap (`@nivo/treemap` or genui `Treemap`) |
| Correlation / activity over time × category   | Heatmap (`@nivo/heatmap`)                   |
| Trends over time                              | Line chart (`recharts`)                     |
| Ranked comparison                             | Bar chart (`recharts`) or genui `Leaderboard` |
| Multi-metric KPIs                             | genui `MetricRow` / `GaugeBoard` / `StatCard` |

### Every diff uses the claude-trim keeper

**ANY diff shown in Theater uses `BeforeAfter` from `@theater/kit`.** The keeper is
`light-mock-up/src/app/claude-trim/page.tsx`: the user chose it **AS IS, only theming,
NOT layout** (2026-09-10). Do not invent a diff table, raw patch/code block, cards or
an alternate layout. Preserve its aligned side-by-side rows, line gutters, section
rail and one synchronized scroll container. Chrome follows the current **dark**
theme; additions/removals keep the keeper's fixed green/red tints, never theme
`ok`/`danger` colors (Amber Ledger's `ok` is tan).

For a unified patch, create **one `DiffSection` per `@@` hunk**, in file/hunk order:
strip each line's leading patch marker; `before` is context + removed lines and
`after` is context + added lines. Preserve whitespace and empty lines. File headers
(`---`/`+++`), hunk headers and `\ No newline at end of file` are metadata, not text.
Give each section a stable unique id and a title with the path and hunk position;
render `BeforeAfter sections={sections}`. Do not concatenate unrelated hunks or
present the patch itself as either side.

```tsx
import { BeforeAfter } from "@theater/kit";

// @@ -1,2 +1,2 @@: " kept", "-old", "+new"
const sections = [{
  id: "example.ts:1", title: "example.ts @1",
  before: "kept\nold", after: "kept\nnew",
}];
// Inside your page:
<BeforeAfter title="Change review" sections={sections} />;
```

## The ANSWER message

Each answer arrives as an inbox message `from: "Theater"`, `type: ANSWER`:

```
Theater answer — <page title>: <questionId> (reply on the page, not to "Theater")
<the value, cut at 300 characters>
<absolute path of session.json>  <upload paths, if any>
{"pageId": …, "questionId": …, "value": …, "revision": …, "uploads": [...]}
```

- **One message per changed answer.** Re-saving the same value sends nothing. Several clicks
  on a multi-choice within 1.5 s, or a drop of several files, become ONE message.
- **A long answer is cut in the message.** The full value is always in `session.json`; read it
  there.
- **`AskReview`'s why is its own question**, `<promptId>.note`, and sends its own message.
- **Reply on the page, never to "Theater".** No agent is called Theater, so a reply sent there
  is refused. Rewrite the page instead.
- **Uploads are real files at absolute paths.** Open them with the Read tool; images you can see.

### Reading the session yourself

```bash
cat <cwd>/.litesuite/theater/<pageId>/session.json
```

```jsonc
{
  "pageId": "driven-demo",
  "revision": 3, // rises on every write, and never goes back, even after a reset
  "answers": [
    { "promptId": "direction", "value": "technical", "at": "..." },
    { "promptId": "sections", "value": ["hero", "proof"], "at": "..." },
  ],
  "uploads": [
    { "promptId": "refs", "originalName": "hero.jpg", "filePath": "C:\\...\\uploads\\hero.jpg",
      "bytes": 266295, "mimeType": "image/jpeg", "at": "..." },
  ],
  "meta": {},
}
```

`session.json` does not exist until the first answer. A page nobody has answered has none.

## "A page is waiting"

Until a page gets its **first** answer or upload, LiteSuite shows a "A page is waiting" notice
with the page's title. **The first answer takes the notice down, even on a page with five
questions.** That is the v1 definition of "answered": the notice tells the human that something
is waiting for them, not how much of it is left.

## Fallback: no inbox reachable

`open` finds its owner from your seat's environment and a registered presence file
(`~/.liteharness/agents/<id>.json`). A seat with neither, such as a CLI that was never
registered, has no inbox to deliver to. Then `open` prints `NO INBOX REACHABLE` followed by a
Monitor command with the real session path already filled in. **Arm it exactly as printed.** It
is the loop below.

### Only when `open` printed it: arm the answer watcher before you hand over the page

```
Monitor({
  description: "<mockupId> answers",   // be specific — this text appears in every notification
  persistent: true,
  timeout_ms: 3600000,
  command: <the one-liner below>,
})
```

```bash
F=<MOCKUP_PROJECT_DIR>/.mockup-io/<mockupId>/session.json
last=""
while true; do
  cur=$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['revision'])" "$F" 2>/dev/null || echo "-")
  if [ "$cur" != "$last" ]; then
    if [ -z "$last" ] && [ "$cur" != "-" ]; then
      # Armed against a file that ALREADY HAS ANSWERS. Do not seed silently:
      # the session file is created lazily BY the first answer, so a populated
      # file at arm time means answers are already waiting to be read.
      echo "<mockupId> ALREADY ANSWERED at arm time — revision $cur; read the file now, do not wait"
    elif [ -n "$last" ]; then
      echo "<mockupId> answered — revision $last -> $cur"
    fi
    last="$cur"
  fi
  sleep 3
done
```

Behaviour, tested both directions:

| condition                                                            | result                                              |
| -------------------------------------------------------------------- | --------------------------------------------------- |
| session file does not exist yet (created lazily on the first answer) | silent — no spam                                    |
| first tick, file ABSENT                                              | silent — seeds `-`; the first answer then fires normally |
| first tick, file ALREADY HAS ANSWERS                                 | **fires immediately** — never absorbed as baseline   |
| a real answer lands                                                  | fires **once** — `answered — revision 11 -> 12`     |

**Compare `revision`, never mtime.** `revision` rises only on a real write; mtime also moves for
a touch or a temp-file rename.

On each notification: read the session file, act on the **new** answers only, and rewrite the
page so the human can see the answer landed. `TaskStop` the monitor when the page is finished.

## Rules for pages

1. **Ask only what changes what you build next.** An answer that would change nothing is
   decoration; cut the question.
2. **React visibly.** After reading an answer, rewrite the page so the human sees it landed. A
   page that never changes is just a form.
3. **Seed sensible defaults**, so an unanswered page is still a coherent design.
4. **Never re-ask** something already answered. Read `session.json` first.
5. **Look at the page before you call it done.** A page can build, serve and still render `NaN`
   or an empty chart; only the pane shows it.
6. **One page per decision thread.** Revise the same `pageId`; a new `pageId` is a new pane.

## Where things live

`<cwd>/.litesuite/theater/<pageId>/`:

| File           | What                                                           |
| -------------- | -------------------------------------------------------------- |
| `page.tsx`     | your page (plus any local files it imports)                    |
| `page.json`    | title and owner, written by `open`                             |
| `session.json` | the answers; created by the first answer                       |
| `uploads/`     | dropped files, as real files (25 MB each at most)              |
| `sent.json`    | which answers were already sent to your inbox                  |

A reset on the page clears the answers but keeps the uploads, and the revision keeps rising.

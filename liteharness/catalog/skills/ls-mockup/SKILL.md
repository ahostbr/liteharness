---
name: ls-mockup
description: Use when creating visual mockups, screenshot layouts, page previews, prototype arrangements, data visualizations, or any visual design work. Outputs to a Next.js mockup project (user-supplied). Triggers on 'mockup', 'create a mockup', 'layout mockup', 'screenshot preview', 'page preview', 'arrange screenshots', 'visual prototype', 'visualize this', 'make this pretty', 'visualize this markdown', 'visualize this document'.
---

# Mockup

> **Requirements:** A Next.js project that serves as the mockup host. Set `MOCKUP_PROJECT_DIR` to its absolute path, or tell the agent the path at invocation time (e.g. `/mockup --dir /home/user/my-mockup-app`). The project must already have the component library described below installed (Next.js 15+, Framer Motion, Recharts, Nivo, visx, D3, react-force-graph, Three.js/R3F, pnpm).
>
> If you do not have a mockup project yet, create one with:
> ```bash
> pnpm create next-app my-mockup --typescript --tailwind
> cd my-mockup
> pnpm add framer-motion recharts @nivo/bar @nivo/line @nivo/pie @nivo/radar @nivo/sankey @nivo/treemap @nivo/heatmap @nivo/network @visx/group @visx/shape @visx/scale @visx/axis @visx/grid @visx/tooltip @visx/responsive @visx/wordcloud @visx/hierarchy @visx/network @visx/heatmap @visx/gradient @visx/pattern @visx/zoom @visx/brush @visx/annotation d3 react-force-graph-2d react-force-graph-3d @react-three/fiber @react-three/drei three
> ```

All mockups output to the **mockup project** at `<MOCKUP_PROJECT_DIR>` (set by the user). No standalone HTML mode — everything goes through the full project with animations, 3D, scroll effects, and interactive data visualization.

## Step 1: Load Design Principles

**Recommended:** If the `frontend-design` plugin is installed, invoke it before design decisions:

```
Skill tool → skill: "frontend-design:frontend-design"
```

This loads anti-slop design thinking (distinctive typography, bold color, spatial composition). The mockup skill provides _structure_, frontend-design provides _taste_.

If `frontend-design` is not installed, apply these principles directly: prefer distinctive typography, bold color choices, spatial composition, and avoid generic "corporate" aesthetics. Use the dark design system tokens in the CSS Variables section below.

---

# Mockup Project Mode

Project: `<MOCKUP_PROJECT_DIR>` — Next.js 15+, run with `pnpm dev` (default port 3000, or user-configured)

## Architecture

```
src/
├── data/
│   ├── mockups.ts          # MockupMeta registry (id, slug, route, audience, tags)
│   └── agents.ts           # 78 agents across 6 categories (for constellation viz)
├── mockups/
│   └── mockup-pages.ts     # Section composition — maps MockupId → ComponentType[]
├── components/
│   ├── mockups/
│   │   ├── MockupPage.tsx       # Rendering orchestrator (iterates sections)
│   │   └── MockupLibraryCard.tsx # Card for /mockups library browser
│   ├── sections/
│   │   ├── investor/       # 9 sections (Hero, Problem, Numbers, etc.)
│   │   ├── technical/      # 8+ sections (Architecture, Comparison, etc.)
│   │   └── shared/         # AgentsSection (used by multiple mockups)
│   ├── shared/
│   │   ├── Nav.tsx              # Global nav (auto-discovers mockups from registry)
│   │   ├── Screenshot3D.tsx     # Image with 3D tilt on mousemove + lightbox
│   │   └── CostBar.tsx          # Pricing breakdown bar
│   ├── three/
│   │   ├── PanelGridScene.tsx          # 6 floating screenshots in 3D space
│   │   └── AgentConstellationScene.tsx # 78-agent Fibonacci sphere constellation
│   └── animations/
│       ├── ScrollReveal.tsx     # Scroll-triggered fade+slide (up/down/left/right)
│       └── CountUp.tsx          # Scroll-triggered number animation
└── app/
    ├── layout.tsx          # Root layout (Sora + Outfit + JetBrains Mono + Bebas Neue)
    ├── globals.css         # CSS vars + Tailwind v4 theme
    ├── page.tsx            # "/" → investor mockup
    ├── technical/page.tsx  # "/technical" → technical mockup
    └── mockups/page.tsx    # "/mockups" → library browser
```

## Creating a New Mockup

### Step A: Register the mockup

Add to `src/data/mockups.ts`:

```typescript
// Add to MockupId union type
export type MockupId = "investor" | "technical" | "your-new-id";

// Add to MOCKUP_LIBRARY array
{
  id: "your-new-id",
  slug: "your-new-slug",
  route: "/your-route",
  navLabel: "Nav Label",
  title: "Full Title",
  summary: "One-line summary for library card",
  audience: "Who this is for",
  family: "Deck mock-up",
  heroImage: "/screenshots/hero.png",
  tags: ["tag1", "tag2"],
  status: "editable",
  order: 3,
}
```

### Step B: Create section components

Create `src/components/sections/{mockup-id}/` directory. Each section is a `"use client"` component:

```typescript
"use client";
import { motion } from "motion/react";
import { ScrollReveal } from "@/components/animations/ScrollReveal";
import { Screenshot3D } from "@/components/shared/Screenshot3D";

export function YourHero() {
  return (
    <section style={{ position: "relative", minHeight: "100vh", display: "flex", alignItems: "center" }}>
      <motion.h1
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      >
        Headline
      </motion.h1>

      <ScrollReveal direction="up" delay={0.2}>
        <p>Supporting copy</p>
      </ScrollReveal>

      <Screenshot3D src="/screenshots/main.png" alt="Main view" />
    </section>
  );
}
```

### Step C: Register sections

Add to `src/mockups/mockup-pages.ts`:

```typescript
import { YourHero } from "@/components/sections/your-id/YourHero";
import { YourFeatures } from "@/components/sections/your-id/YourFeatures";
// ...

const MOCKUP_SECTIONS: Record<MockupId, ComponentType[]> = {
  // existing entries...
  "your-new-id": [
    YourHero,
    YourFeatures,
    // more sections...
  ],
};
```

### Step D: Create the route

Create `src/app/{route}/page.tsx`:

```typescript
import { MockupPage } from "@/components/mockups/MockupPage";

export default function YourMockupRoute() {
  return <MockupPage mockupId="your-new-id" />;
}
```

Nav auto-discovers the new mockup from the registry. No manual nav edits needed.

## Available Component Library

### Animations

| Component      | Import                                 | Props                                                                              |
| -------------- | -------------------------------------- | ---------------------------------------------------------------------------------- |
| `ScrollReveal` | `@/components/animations/ScrollReveal` | `direction` (up/down/left/right/none), `delay`, `distance`, `duration`, `children` |
| `CountUp`      | `@/components/animations/CountUp`      | `target` (number), `prefix`, `suffix`, `duration`                                  |

### Shared UI

| Component      | Import                             | Purpose                                                                                                |
| -------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `Screenshot3D` | `@/components/shared/Screenshot3D` | Image with 3D tilt on hover + lightbox. Props: `src`, `alt`, `aspectRatio` (16/10 default), `priority` |
| `CostBar`      | `@/components/shared/CostBar`      | Horizontal pricing breakdown bar                                                                       |
| `Nav`          | `@/components/shared/Nav`          | Auto-generated top nav (don't manually render — layout handles it)                                     |

### Three.js 3D Scenes

| Component            | Import                                  | Purpose                                                                                |
| -------------------- | --------------------------------------- | -------------------------------------------------------------------------------------- |
| `PanelGrid`          | `@/components/three/PanelGrid`          | 6 floating screenshots with sin-wave motion. SSR-disabled wrapper.                     |
| `AgentConstellation` | `@/components/three/AgentConstellation` | 78-agent Fibonacci sphere. Click to inspect, hover to highlight. SSR-disabled wrapper. |

Import 3D components with SSR disabled:

```typescript
import dynamic from "next/dynamic";
const PanelGrid = dynamic(
  () => import("@/components/three/PanelGrid").then((m) => ({ default: m.PanelGrid })),
  { ssr: false },
);
```

### Motion (Framer Motion v12)

All sections are `"use client"`. Use `motion/react` for entrance animations:

```typescript
import { motion, useMotionValue, useSpring, useTransform, useInView } from "motion/react";
```

---

## Data Visualization Library (INSTALLED)

Light Mock-Up has a full visualization stack. **Always prefer these over hand-rolled CSS charts.** All are `"use client"` components — wrap in SSR-disabled dynamic imports if they access `window`.

### Recharts — Standard 2D Charts (DEFAULT for most charts)

Best for: line charts, bar charts, area charts, pie/donut, scatter, composed charts, funnel charts. Responsive, animated, tooltip-ready out of the box.

```typescript
"use client";
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, AreaChart, Area,
  PieChart, Pie, Cell, RadarChart, Radar, PolarGrid, PolarAngleAxis,
  ScatterChart, Scatter, ComposedChart, FunnelChart, Funnel,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine,
} from "recharts";

// Always wrap in ResponsiveContainer for responsive sizing
<ResponsiveContainer width="100%" height={400}>
  <LineChart data={data}>
    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
    <XAxis dataKey="name" stroke="var(--text-secondary)" />
    <YAxis stroke="var(--text-secondary)" />
    <Tooltip
      contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }}
      labelStyle={{ color: "var(--text-primary)" }}
    />
    <Line type="monotone" dataKey="value" stroke="var(--gold)" strokeWidth={2} dot={false} />
  </LineChart>
</ResponsiveContainer>
```

**Use Recharts when:** standard charts, dashboards, time series, comparisons, KPI panels. It's the fastest to implement and looks great with our theme tokens.

### Nivo — Rich Themed Charts (for polish + built-in dark theme)

Best for: bar, line, pie, radar, sankey flows, treemaps, heatmaps, network graphs. Has built-in dark theme support, animation, and rich interactivity.

```typescript
"use client";
import { ResponsiveBar } from "@nivo/bar";
import { ResponsiveLine } from "@nivo/line";
import { ResponsivePie } from "@nivo/pie";
import { ResponsiveRadar } from "@nivo/radar";
import { ResponsiveSankey } from "@nivo/sankey";
import { ResponsiveTreeMap } from "@nivo/treemap";
import { ResponsiveHeatMap } from "@nivo/heatmap";
import { ResponsiveNetwork } from "@nivo/network";

// Nivo uses a theme object — match our design system
const nivoTheme = {
  background: "transparent",
  text: { fontSize: 12, fill: "var(--text-secondary)" },
  axis: {
    ticks: { text: { fill: "var(--text-secondary)" } },
    legend: { text: { fill: "var(--text-primary)", fontSize: 13 } },
  },
  grid: { line: { stroke: "var(--border)", strokeWidth: 1 } },
  tooltip: {
    container: { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, color: "var(--text-primary)" },
  },
};

<div style={{ height: 400 }}>
  <ResponsiveBar
    data={data}
    keys={["value"]}
    indexBy="label"
    theme={nivoTheme}
    colors={["var(--gold)"]}
    borderRadius={4}
    animate={true}
    motionConfig="gentle"
  />
</div>
```

**Use Nivo when:** you need sankey diagrams, treemaps, heatmaps, network graphs, or want richer built-in interactivity than Recharts. Also great for radar charts comparing multiple dimensions.

#### Nivo chart picker

| Chart Type          | Import          | Best For                                           |
| ------------------- | --------------- | -------------------------------------------------- |
| `ResponsiveBar`     | `@nivo/bar`     | Comparisons, rankings, category data               |
| `ResponsiveLine`    | `@nivo/line`    | Time series, trends, multi-line                    |
| `ResponsivePie`     | `@nivo/pie`     | Proportions, market share, breakdowns              |
| `ResponsiveRadar`   | `@nivo/radar`   | Multi-dimensional comparisons (features, scores)   |
| `ResponsiveSankey`  | `@nivo/sankey`  | Flow diagrams, user journeys, money flow, pipeline |
| `ResponsiveTreeMap` | `@nivo/treemap` | Hierarchical data, file sizes, budget allocation   |
| `ResponsiveHeatMap` | `@nivo/heatmap` | Correlation matrices, activity grids, schedules    |
| `ResponsiveNetwork` | `@nivo/network` | Relationship graphs, dependency maps               |

### visx — Low-Level SVG Primitives (for custom/unique visuals)

Best for: fully custom chart designs that don't fit standard templates. D3-powered React components. Maximum control over every SVG element.

```typescript
"use client";
import { Group } from "@visx/group";
import { Bar } from "@visx/shape";
import { scaleLinear, scaleBand } from "@visx/scale";
import { AxisBottom, AxisLeft } from "@visx/axis";
import { GridRows } from "@visx/grid";
import { useTooltip, TooltipWithBounds } from "@visx/tooltip";
import { ParentSize } from "@visx/responsive";
import { Wordcloud } from "@visx/wordcloud";
import { Treemap } from "@visx/hierarchy";
import { NetworkGraph } from "@visx/network";
import { HeatmapRect } from "@visx/heatmap";
import { LinePath, AreaClosed } from "@visx/shape";
import { GradientDarkgreenGreen, LinearGradient } from "@visx/gradient";
import { PatternLines } from "@visx/pattern";
import { Zoom } from "@visx/zoom";
import { Brush } from "@visx/brush";
import { Annotation, Label, Connector } from "@visx/annotation";

// Wrap in ParentSize for responsive
<ParentSize>{({ width, height }) => (
  <svg width={width} height={height}>
    <Group top={margin.top} left={margin.left}>
      {/* Custom SVG chart here */}
    </Group>
  </svg>
)}</ParentSize>
```

**Use visx when:** you need word clouds, custom annotations, zoomable/brushable charts, unique gradient fills, pattern fills, geo maps, or any chart shape that doesn't exist in Recharts/Nivo.

#### visx module picker

| Module             | Import                                          | Purpose                      |
| ------------------ | ----------------------------------------------- | ---------------------------- |
| `@visx/shape`      | Bar, Line, Pie, Area, Arc, LinkHorizontal       | Drawing primitives           |
| `@visx/scale`      | scaleLinear, scaleBand, scaleTime, scaleOrdinal | D3 scales                    |
| `@visx/axis`       | AxisBottom, AxisLeft, AxisTop, AxisRight        | Axis rendering               |
| `@visx/grid`       | GridRows, GridColumns, GridRadial               | Background grids             |
| `@visx/tooltip`    | useTooltip, TooltipWithBounds, defaultStyles    | Hover tooltips               |
| `@visx/responsive` | ParentSize, ScaleSVG                            | Responsive containers        |
| `@visx/wordcloud`  | Wordcloud                                       | Tag/word clouds              |
| `@visx/hierarchy`  | Treemap, Tree, Cluster, Pack                    | Tree/hierarchy layouts       |
| `@visx/network`    | Graph, Links, Nodes                             | Network/graph visualizations |
| `@visx/heatmap`    | HeatmapRect, HeatmapCircle                      | Heatmaps                     |
| `@visx/geo`        | Projection, Graticule, CustomProjection         | Geographic maps              |
| `@visx/gradient`   | LinearGradient, RadialGradient, GradientX       | SVG gradients                |
| `@visx/pattern`    | PatternLines, PatternCircles, PatternWaves      | SVG pattern fills            |
| `@visx/zoom`       | Zoom                                            | Pan + zoom interaction       |
| `@visx/brush`      | Brush, BaseBrush                                | Range selection              |
| `@visx/annotation` | Annotation, Label, Connector, CircleSubject     | Chart annotations            |
| `@visx/voronoi`    | voronoi                                         | Nearest-point detection      |
| `@visx/delaunay`   | delaunay                                        | Triangulation                |

### D3 — Direct Access (for transforms + layouts)

Use D3 for data transforms, scales, and layouts — then render with React. Don't use D3 for DOM manipulation (let React handle that).

```typescript
import * as d3 from "d3";

const colorScale = d3.scaleSequential(d3.interpolateViridis).domain([0, maxValue]);
const hierarchy = d3.hierarchy(treeData).sum((d) => d.value);
const treemap = d3.treemap().size([width, height]).padding(2)(hierarchy);
const forceSimulation = d3
  .forceSimulation(nodes)
  .force("charge", d3.forceManyBody().strength(-50))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("link", d3.forceLink(links));
```

**Use D3 directly when:** computing layouts (force, treemap, pack, cluster), color scales (sequential, diverging), geo projections, time formatting, number formatting, statistical functions.

### Force Graphs — Interactive Node Networks (2D + 3D)

Best for: dependency graphs, knowledge graphs, agent networks, social networks, system architecture.

```typescript
"use client";
import dynamic from "next/dynamic";

// 2D force graph (canvas-based, high performance)
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), { ssr: false });

// 3D force graph (Three.js WebGL, fully rotatable)
const ForceGraph3D = dynamic(() => import("react-force-graph-3d"), { ssr: false });

const graphData = {
  nodes: [
    { id: "sentinel", name: "Sentinel", group: "orchestrator", val: 20 },
    { id: "worker-1", name: "Worker", group: "worker", val: 10 },
  ],
  links: [
    { source: "sentinel", target: "worker-1", value: 1 },
  ],
};

// 2D — fast, good for dense graphs
<ForceGraph2D
  graphData={graphData}
  nodeColor={node => groupColors[node.group]}
  nodeLabel="name"
  linkColor={() => "rgba(212,168,83,0.3)"}
  backgroundColor="#06060a"
  width={800}
  height={600}
/>

// 3D — immersive, rotatable, best for showcases
<ForceGraph3D
  graphData={graphData}
  nodeColor={node => groupColors[node.group]}
  nodeLabel="name"
  linkOpacity={0.3}
  backgroundColor="#06060a"
/>
```

**Use force graphs when:** showing relationships between agents, systems, dependencies, or any network topology. 3D for wow factor, 2D for performance with 100+ nodes.

### Three.js + R3F — Custom 3D Visualizations

Already installed. For 3D data viz beyond force graphs — globe projections, 3D bar charts, particle systems, spatial data.

```typescript
"use client";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls, Html, Text, Line } from "@react-three/drei";
import * as THREE from "three";
```

**Use Three.js/R3F when:** 3D scatter plots, globe visualizations, architectural 3D diagrams, particle swarms, floating 3D label clouds.

---

### Choosing the Right Viz Library

```
Need a standard chart fast?           → Recharts
Need sankey/treemap/heatmap/radar?    → Nivo
Need a word cloud or custom SVG?      → visx
Need force-directed network graph?    → react-force-graph-2d/3d
Need 3D scatter/globe/particles?      → Three.js + R3F
Need data transforms/scales only?     → D3 (import functions, render with React)
```

**IMPORTANT:** Always prefer the highest-impact visualization. Don't default to boring bar charts when the data calls for a sankey flow, force graph, treemap, or radar comparison. Match the viz to the story the data tells:

| Data Story                                    | Best Viz                                  |
| --------------------------------------------- | ----------------------------------------- |
| How things flow (budget, users, pipeline)     | Sankey (`@nivo/sankey`)                   |
| How things relate (deps, agents, systems)     | Force graph (`react-force-graph-2d/3d`)   |
| How things compare across dimensions          | Radar (`@nivo/radar` or `recharts`)       |
| Hierarchical proportions (files, budget, org) | Treemap (`@nivo/treemap`)                 |
| Correlation / activity over time × category   | Heatmap (`@nivo/heatmap`)                 |
| Trends over time                              | Line chart (`recharts`)                   |
| Ranked comparison                             | Bar chart (`recharts` or `@nivo/bar`)     |
| Proportional breakdown                        | Pie/donut (`recharts` or `@nivo/pie`)     |
| Tag frequency / importance                    | Word cloud (`@visx/wordcloud`)            |
| Geographic distribution                       | Globe (Three.js) or map (`@visx/geo`)     |
| Multi-metric KPI dashboard                    | Composed chart (`recharts` ComposedChart) |

## Design System

### CSS Variables (`globals.css`)

```css
:root {
  --background: #06060a;
  --surface: #111119;
  --border: #1a1a2e;
  --gold: #d4a853;
  --gold-hover: #e0be6e;
  --text-primary: #ededf0;
  --text-secondary: #7e7e96;
  --accent-cyan: #22d3ee;
  --destructive: #ef4444;
}
```

### Fonts (loaded via Next.js `next/font/google`)

| Font               | Weight  | Role                      |
| ------------------ | ------- | ------------------------- |
| **Sora**           | 400-800 | Display headings          |
| **Outfit**         | 400-700 | Body text                 |
| **JetBrains Mono** | —       | Monospace/code            |
| **Bebas Neue**     | 400     | Large geometric headlines |

### Section Ordering (marketing/pitch)

1. **Hero** — scroll-stopper, the thing nobody else has
2. **Problem** — pain point with cost/time data
3. **Product Showcase** — screenshots with 3D tilt
4. **Social Proof** — agents constellation, partner logos, numbers
5. **Differentiators** — why us vs. them
6. **Opportunity** — market size, growth
7. **Founder** — credibility
8. **CTA** — call to action

## Dev Server

```bash
cd <MOCKUP_PROJECT_DIR> && pnpm dev  # http://localhost:<port> (default 3000)
```

After creating a new mockup, verify it renders at `http://localhost:<port>/{route}` and appears in the library at `http://localhost:<port>/mockups`.

---

## Data Visualization (within Light Mock-Up)

For markdown documents, research reports, structured data — create as a new mockup page.

### Approach

Read the source file. Identify:

- **Hero stats** — 3-5 most impactful numbers → `CountUp` components
- **Sections** — each `##` heading becomes a visual section with `ScrollReveal`
- **Tables** → Recharts bar charts, Nivo heatmaps, or styled comparison grids
- **Lists** → animated card grids with staggered reveals
- **Comparisons** → Nivo radar charts or side-by-side panels
- **Hierarchical data** → Nivo treemaps
- **Flow/pipeline data** → Nivo sankey diagrams
- **Network/relationship data** → react-force-graph-2d/3d

Always use the installed viz libraries (Recharts, Nivo, visx, D3, force graphs) — never hand-roll CSS charts.

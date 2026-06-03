# Tweaks: Real-Time Parameter Tuning for Design Variations

Tweaks is a core capability in this skill — it lets users switch between variations and adjust parameters in real time without touching the code.

**Cross-agent environment compatibility**: Some design-agent native environments (e.g., Claude.ai Artifacts) rely on the host's postMessage to write tweak values back to source code for persistence. This skill uses a **pure frontend localStorage approach** — the effect is identical (state is preserved on refresh), but persistence happens in the browser's localStorage rather than in source files. This approach works in any agent environment (Claude Code / Codex / Cursor / Trae / etc.).

## When to Add Tweaks

- The user explicitly asks for "adjustable parameters" / "multiple version switching"
- The design has multiple variations that need to be compared
- The user has not said so explicitly, but you subjectively judge that **adding a few insightful tweaks would help the user see the possibility space**

Default recommendation: **add 2-3 tweaks to every design** (color theme / font size / layout variant) even when the user has not asked — letting users see the possibility space is part of the design service.

## Implementation (Pure Frontend Version)

### Basic Structure

```jsx
const TWEAK_DEFAULTS = {
  primaryColor: "#D97757",
  fontSize: 16,
  density: "comfortable",
  dark: false,
};

function useTweaks() {
  const [tweaks, setTweaks] = React.useState(() => {
    try {
      const stored = localStorage.getItem("design-tweaks");
      return stored ? { ...TWEAK_DEFAULTS, ...JSON.parse(stored) } : TWEAK_DEFAULTS;
    } catch {
      return TWEAK_DEFAULTS;
    }
  });

  const update = (patch) => {
    const next = { ...tweaks, ...patch };
    setTweaks(next);
    try {
      localStorage.setItem("design-tweaks", JSON.stringify(next));
    } catch {}
  };

  const reset = () => {
    setTweaks(TWEAK_DEFAULTS);
    try {
      localStorage.removeItem("design-tweaks");
    } catch {}
  };

  return { tweaks, update, reset };
}
```

### Tweaks Panel UI

Floating panel in the bottom-right corner. Collapsible:

```jsx
function TweaksPanel() {
  const { tweaks, update, reset } = useTweaks();
  const [open, setOpen] = React.useState(false);

  return (
    <div
      style={{
        position: "fixed",
        bottom: 20,
        right: 20,
        zIndex: 9999,
      }}
    >
      {open ? (
        <div
          style={{
            background: "white",
            border: "1px solid #e5e5e5",
            borderRadius: 12,
            padding: 20,
            boxShadow: "0 10px 40px rgba(0,0,0,0.12)",
            width: 280,
            fontFamily: "system-ui",
            fontSize: 13,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 16,
            }}
          >
            <strong>Tweaks</strong>
            <button
              onClick={() => setOpen(false)}
              style={{
                border: "none",
                background: "none",
                cursor: "pointer",
                fontSize: 16,
              }}
            >
              x
            </button>
          </div>

          {/* Color */}
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ marginBottom: 4, color: "#666" }}>Primary Color</div>
            <input
              type="color"
              value={tweaks.primaryColor}
              onChange={(e) => update({ primaryColor: e.target.value })}
              style={{ width: "100%", height: 32 }}
            />
          </label>

          {/* Font size slider */}
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ marginBottom: 4, color: "#666" }}>Font Size ({tweaks.fontSize}px)</div>
            <input
              type="range"
              min={12}
              max={24}
              step={1}
              value={tweaks.fontSize}
              onChange={(e) => update({ fontSize: +e.target.value })}
              style={{ width: "100%" }}
            />
          </label>

          {/* Density options */}
          <label style={{ display: "block", marginBottom: 12 }}>
            <div style={{ marginBottom: 4, color: "#666" }}>Density</div>
            <select
              value={tweaks.density}
              onChange={(e) => update({ density: e.target.value })}
              style={{ width: "100%", padding: 6 }}
            >
              <option value="compact">Compact</option>
              <option value="comfortable">Comfortable</option>
              <option value="spacious">Spacious</option>
            </select>
          </label>

          {/* Dark mode toggle */}
          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
            }}
          >
            <input
              type="checkbox"
              checked={tweaks.dark}
              onChange={(e) => update({ dark: e.target.checked })}
            />
            <span>Dark Mode</span>
          </label>

          <button
            onClick={reset}
            style={{
              width: "100%",
              padding: "8px 12px",
              background: "#f5f5f5",
              border: "none",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            Reset
          </button>
        </div>
      ) : (
        <button
          onClick={() => setOpen(true)}
          style={{
            background: "#1A1A1A",
            color: "white",
            border: "none",
            borderRadius: 999,
            padding: "10px 16px",
            fontSize: 12,
            cursor: "pointer",
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
          }}
        >
          Settings Tweaks
        </button>
      )}
    </div>
  );
}
```

### Applying Tweaks

Using Tweaks in the main component:

```jsx
function App() {
  const { tweaks } = useTweaks();

  return (
    <div
      style={{
        "--primary": tweaks.primaryColor,
        "--font-size": `${tweaks.fontSize}px`,
        background: tweaks.dark ? "#0A0A0A" : "#FAFAFA",
        color: tweaks.dark ? "#FAFAFA" : "#1A1A1A",
      }}
    >
      {/* Your content */}
      <TweaksPanel />
    </div>
  );
}
```

Using variables in CSS:

```css
button.cta {
  background: var(--primary);
  color: white;
  font-size: var(--font-size);
}
```

## Typical Tweak Options

What tweaks to add for different design types:

### General

- Primary color (color picker)
- Font size (slider 12-24px)
- Typeface (select: display font vs body font)
- Dark mode (toggle)

### Slide Decks

- Theme (light / dark / brand)
- Background style (solid / gradient / image)
- Font contrast (more decorative vs more restrained)
- Information density (minimal / standard / dense)

### Product Prototypes

- Layout variant (layout A / B / C)
- Interaction speed (animation speed 0.5x-2x)
- Data volume (number of mock data rows: 5 / 20 / 100)
- State (empty / loading / success / error)

### Animations

- Speed (0.5x-2x)
- Loop (once / loop / ping-pong)
- Easing (linear / easeOut / spring)

### Landing Pages

- Hero style (image / gradient / pattern / solid)
- CTA copy (several variants)
- Structure (single column / two column / sidebar)

## Tweaks Design Principles

### 1. Meaningful Options, Not Busywork

Every tweak must expose **genuine design choices**. Don't add tweaks that nobody would actually switch (e.g., a border-radius 0-50px slider — users will find that all intermediate values look bad).

Good tweaks expose **discrete, considered variations**:

- "Corner style": No rounding / Subtle rounding / Large rounding (three options)
- Not: "Corner radius": 0-50px slider

### 2. Less Is More

A Tweaks panel for any one design should have **at most 5-6 options**. More than that turns it into a "configuration page" and defeats the purpose of quickly exploring variations.

### 3. Default Values Are the Finished Design

Tweaks are **the cherry on top**. The default values must themselves constitute a complete, shippable design. What the user sees when they close the Tweaks panel is the deliverable.

### 4. Sensible Grouping

When there are many options, group them for display:

```
---- Visual ----
Primary Color | Font Size | Dark Mode

---- Layout ----
Density | Sidebar Position

---- Content ----
Data Volume | State
```

## Forward-Compatible Source-Level Persistence Host

If you later want to upload the design to an environment that supports source-level tweaks (e.g., Claude.ai Artifacts), keep the **EDITMODE marker blocks**:

```jsx
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/ {
  primaryColor: "#D97757",
  fontSize: 16,
  density: "comfortable",
  dark: false,
}; /*EDITMODE-END*/
```

The marker blocks have **no effect** in the localStorage approach (they are just ordinary comments), but in hosts that support source write-back they will be read and enable source-level persistence. Adding them causes no harm in the current environment while maintaining forward compatibility.

## Frequently Asked Questions

**The Tweaks panel covers the design content**
-> Make it collapsible. Closed by default, showing a small button that expands when clicked.

**Users have to re-apply tweaks after switching**
-> localStorage is already being used. If state does not persist after a refresh, check whether localStorage is available (it fails in private/incognito mode — make sure to catch the error).

**I want to share tweaks across multiple HTML pages**
-> Add the project name to the localStorage key: `design-tweaks-[projectName]`.

**I want tweaks to have dependencies on each other**
-> Add logic inside `update`:

```jsx
const update = (patch) => {
  let next = { ...tweaks, ...patch };
  // Linked behavior: automatically switch text color when dark mode is selected
  if (patch.dark === true && !patch.textColor) {
    next.textColor = '#F0EEE6';
  }
  setTweaks(next);
  localStorage.setItem(...);
};
```

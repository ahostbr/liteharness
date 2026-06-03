# Design Context: Starting from Existing Context

**This is the most important one thing about this skill.**

Good hi-fi design always grows from existing design context. **Doing hi-fi from scratch is a last resort and will always produce generic work**. So at the start of every design task, ask first: is there anything we can reference?

## What Is Design Context

In order of priority from highest to lowest:

### 1. The User's Design System / UI Kit

The component library, color tokens, typography specs, and icon system from the user's own product. **The ideal scenario**.

### 2. The User's Codebase

If the user has given you a codebase, it contains living component implementations. Read those component files:

- `theme.ts` / `colors.ts` / `tokens.css` / `_variables.scss`
- Specific components (Button.tsx, Card.tsx)
- Layout scaffold (App.tsx, MainLayout.tsx)
- Global stylesheets

**Read the code and copy exact values**: hex codes, spacing scale, font stack, border radius. Don't redraw from memory.

### 3. The User's Published Product

If the user has a live product but hasn't shared the code, use Playwright or ask the user to provide screenshots.

```bash
# Screenshot a public URL with Playwright
npx playwright screenshot https://example.com screenshot.png --viewport-size=1920,1080
```

This lets you see the real visual vocabulary.

### 4. Brand Guidelines / Logo / Existing Assets

The user may have: logo files, brand color specs, marketing materials, slide templates. All of these are context.

### 5. Competitor References

The user says "like XX website" — ask them to provide a URL or screenshot. **Don't** work from the blurry memory in your training data.

### 6. Known Design Systems (fallback)

If none of the above exist, use an established design system as a base:

- Apple HIG
- Material Design 3
- Radix Colors (color palettes)
- shadcn/ui (components)
- Tailwind default palette

Tell the user explicitly what you're using so they know this is a starting point, not a final draft.

## The Process for Getting Context

### Step 1: Ask the User

The required checklist at the start of a task (from `workflow.md`):

```markdown
1. Do you have an existing design system / UI kit / component library? Where is it?
2. Do you have brand guidelines, color / typography specs?
3. Can you give me screenshots or a URL of your existing product?
4. Do you have a codebase I can read?
```

### Step 2: When the User Says "No", Help Them Find It

Don't give up immediately. Try:

```markdown
Let me look for clues:

- Do any of your previous projects have related designs?
- What colors / fonts does your company's marketing website use?
- What style is your product's logo? Can you share a copy?
- Is there a product you admire that could serve as a reference?
```

### Step 3: Read All the Context You Can Find

If the user has given you a codebase path, read:

1. **List the file structure first**: find style/theme/component-related files
2. **Read the theme/token files**: extract specific hex/px values
3. **Read 2-3 representative components**: observe the visual vocabulary (hover states, shadows, borders, padding patterns)
4. **Read the global stylesheet**: base resets, font loading
5. **If there's a Figma link/screenshot**: look at the image, but **trust the code more**

**Important**: **Don't** glance at something and work from impression. You haven't truly lifted the context until you've read down to 30+ specific values.

### Step 4: Vocalize the System You're Going to Use

After reviewing the context, tell the user the system you plan to use:

```markdown
Based on your codebase and product screenshots, here is the design system I've distilled:

**Colors**

- Primary: #C27558 (from tokens.css)
- Background: #FDF9F0
- Text: #1A1A1A
- Muted: #6B6B6B

**Typography**

- Display: Instrument Serif (from @font-face in global.css)
- Body: Geist Sans
- Mono: JetBrains Mono

**Spacing** (from your scale system)

- 4, 8, 12, 16, 24, 32, 48, 64

**Shadow pattern**

- `0 1px 2px rgba(0,0,0,0.04)` (subtle card)
- `0 10px 40px rgba(0,0,0,0.1)` (elevated modal)

**Border-radius**

- Small components 4px, cards 12px, buttons 8px

**Component vocabulary**

- Button: filled primary, outlined secondary, ghost tertiary — all 8px border-radius
- Card: white background, subtle shadow, no border

I'll start working with this system. Does this look right to you?
```

Wait for user confirmation before proceeding.

## Designing Without Context (fallback when there is no context)

**Strong warning**: The quality of output in this situation will be significantly lower. Tell the user clearly.

```markdown
You have no design context, so I can only work from general instinct.
The output will be "looks okay but lacks distinctiveness."
Do you want to continue, or would you like to provide some reference materials first?
```

If the user insists, make decisions in this order:

### 1. Choose an Aesthetic Direction

Don't produce generic results. Pick a clear direction:

- brutally minimal
- editorial/magazine
- brutalist/raw
- organic/natural
- luxury/refined
- playful/toy
- retro-futuristic
- soft/pastel

Tell the user which direction you chose.

### 2. Choose a Known Design System as the Skeleton

- Use Radix Colors for color palettes (https://www.radix-ui.com/colors)
- Use shadcn/ui for component vocabulary (https://ui.shadcn.com)
- Use Tailwind spacing scale (multiples of 4)

### 3. Choose a Distinctive Font Pairing

Avoid Inter/Roboto. Suggested combinations (free from Google Fonts):

- Instrument Serif + Geist Sans
- Cormorant Garamond + Inter Tight
- Bricolage Grotesque + Söhne (paid)
- Fraunces + Work Sans (note: Fraunces is already being overused by AI)
- JetBrains Mono + Geist Sans (technical feel)

### 4. Every Key Decision Has Reasoning

Don't choose silently. Write comments in the HTML:

```html
<!--
Design decisions:
- Primary color: warm terracotta (oklch 0.65 0.18 25) — fits the "editorial" direction  
- Display: Instrument Serif for humanist, literary feel
- Body: Geist Sans for cleanness contrast
- No gradients — committed to minimal, no AI slop
- Spacing: 8px base, golden ratio friendly (8/13/21/34)
-->
```

## Import Strategy (When the User Has Given You a Codebase)

If the user says "import this codebase as reference":

### Small (<50 files)

Read everything and internalize the context.

### Medium (50-500 files)

Focus on:

- `src/components/` or `components/`
- All style/token/theme-related files
- 2-3 representative full-page components (Home.tsx, Dashboard.tsx)

### Large (>500 files)

Ask the user to specify the focus:

- "I'm building a settings page" → read the existing settings-related code
- "I'm adding a new feature" → read the overall shell + the closest reference
- Don't try to cover everything; be targeted and precise

## Working with Figma / Design Files

If the user has provided a Figma link:

- **Don't** expect to "convert Figma to HTML" directly — that requires additional tooling
- Figma links are generally not publicly accessible
- Ask the user to: export as **screenshots** and send them to you + tell you the specific color/spacing values

If you've only been given a Figma screenshot, tell the user:

- I can see the visuals, but I can't extract exact values
- Please share the key numbers (hex, px), or export as code (Figma supports this)

## Final Reminder

**The quality ceiling of a project's design is determined by the quality of the context you receive.**

Spending 10 minutes collecting context is more valuable than spending 1 hour doing hi-fi work from scratch.

**When you have no context, prioritize asking the user for it — don't just push through.**

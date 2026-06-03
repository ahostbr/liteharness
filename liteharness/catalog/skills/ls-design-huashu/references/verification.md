# Verification: Output Validation Workflow

Some design-agent native environments (e.g., Claude.ai Artifacts) have a built-in `fork_verifier_agent` that spawns a subagent to check using iframe screenshots. Most agent environments (Claude Code / Codex / Cursor / Trae / etc.) don't have this built-in capability — doing it manually with Playwright covers the same validation scenarios.

## Verification Checklist

After producing HTML, run through this checklist once:

### 1. Browser Render Check (Required)

The most basic check: **can the HTML be opened?** On macOS:

```bash
open -a "Google Chrome" "/path/to/your/design.html"
```

Or use a Playwright screenshot (see next section).

### 2. Console Error Check

The most common problem in HTML files is JS errors causing a white screen. Run through it with Playwright:

```bash
python ${CLAUDE_SKILL_DIR}/scripts/verify.py path/to/design.html
```

This script will:

1. Open the HTML in headless Chromium
2. Save a screenshot to the project directory
3. Capture console errors
4. Report status

See `scripts/verify.py` for details.

### 3. Multi-Viewport Check

For responsive designs, capture multiple viewports:

```bash
python verify.py design.html --viewports 1920x1080,1440x900,768x1024,375x667
```

### 4. Interaction Check

Tweaks, animations, button toggling — you can't see these in a static screenshot. **Recommend having the user open a browser and click through it themselves**, or record with Playwright:

```python
page.video.record('interaction.mp4')
```

### 5. Slide-by-Slide Check

For deck-style HTML, capture each slide individually:

```bash
python verify.py deck.html --slides 10  # capture first 10 slides
```

Generates `deck-slide-01.png`, `deck-slide-02.png`... for quick visual review.

## Playwright Setup

First-time setup:

```bash
# If not yet installed
npm install -g playwright
npx playwright install chromium

# Or the Python version
pip install playwright
playwright install chromium
```

If the user already has Playwright installed globally, just use it directly.

## Screenshot Best Practices

### Full-page screenshot

```python
page.screenshot(path='full.png', full_page=True)
```

### Viewport screenshot

```python
page.screenshot(path='viewport.png')  # captures visible area only by default
```

### Screenshot of a specific element

```python
element = page.query_selector('.hero-section')
element.screenshot(path='hero.png')
```

### High-DPI screenshot

```python
page = browser.new_page(device_scale_factor=2)  # retina
```

### Wait for animations to settle before capturing

```python
page.wait_for_timeout(2000)  # wait 2 seconds for animations to settle
page.screenshot(...)
```

## Sharing Screenshots with Users

### Open a local screenshot directly

```bash
open screenshot.png
```

The user will view it in their own Preview / Figma / VSCode / browser.

### Upload to an image host and share the link

If you need to share with remote collaborators (e.g., via Slack / Feishu / WeChat), have the user upload with their own image hosting tool or via MCP:

```bash
python ~/Documents/writing/tools/upload_image.py screenshot.png
```

Returns a permanent ImgBB link that can be pasted anywhere.

## When Validation Fails

### White screen

There will always be a console error. Check first:

1. Whether the React+Babel script tag integrity hash is correct (see `react-setup.md`)
2. Whether there is a `const styles = {...}` naming conflict
3. Whether cross-file components are exported to `window`
4. JSX syntax errors (babel.min.js doesn't report errors — switch to the unminified babel.js)

### Laggy animations

- Record a session in Chrome DevTools Performance tab
- Look for layout thrashing (frequent reflows)
- Prioritize `transform` and `opacity` for animations (GPU-accelerated)

### Wrong fonts

- Check whether the `@font-face` URL is accessible
- Check fallback fonts
- Chinese fonts load slowly: display the fallback first, then switch once loaded

### Layout misalignment

- Check whether `box-sizing: border-box` is applied globally
- Check the `* { margin: 0; padding: 0 }` reset
- Open gridlines in Chrome DevTools to see the actual layout

## Validation = The Designer's Second Set of Eyes

**Always run through it yourself.** AI-written code frequently has issues like:

- Looks correct but the interaction has a bug
- Static screenshot looks fine but shifts on scroll
- Looks great at wide screen but breaks at narrow widths
- Dark mode was never tested
- Some components don't respond after switching Tweaks

**One minute of validation at the end can save an hour of rework.**

## Common Validation Script Commands

```bash
# Basic: open + screenshot + capture errors
python verify.py design.html

# Multiple viewports
python verify.py design.html --viewports 1920x1080,375x667

# Multiple slides
python verify.py deck.html --slides 10

# Output to a specific directory
python verify.py design.html --output ./screenshots/

# headless=false, opens a real browser for you to see
python verify.py design.html --show
```

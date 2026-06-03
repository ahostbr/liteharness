---
name: ls-gen-image-or-video
description: Use when generating AI images or videos — marketing visuals, hero banners, social media assets, product mockups, ad creative, animated backgrounds, exploding views, 3D renders, scroll-synced video assets. Triggers on 'generate an image', 'create an image', 'gen-image', 'make me an image', 'image gen', 'marketing image', 'product mockup', 'hero image', 'generate a video', 'create a video', 'gen-video', 'make me a video', 'video gen', 'animated background', 'exploding view', 'veo', 'video asset', 'scroll animation video', 'chatgpt image', 'gpt image', 'chatgpt flag'.
---

# AI Image & Video Generation

## Image Generation

Six tiers: **1) Gemini API** (default, Python script) → **2) Codex Desktop** (gpt-image-2 via bridge, free, no API key) → **3) ChatGPT Desktop CDP** (cli-anything-chatgpt, best quality, one command) → **4) ChatGPT Browser** (Claude-in-Chrome fallback) → **5) LiteImage** (local SD) → **6) Local Gateway** (SSE, optional).

## Video Generation

Google Veo via Gemini API: **1) Text-to-video** → **2) Image-to-video** → **3) Video extension**.

---

## Step 1: Detect Media Type

Read the user's request. Determine if they need an **image** or a **video**:

- **Video signals:** "animated", "video", "scroll animation", "exploding view", "rotating", "3D animation", "hero background video", "motion", "cinematic", "panning shot", "Veo", "mp4"
- **Image signals:** "image", "banner", "mockup", "screenshot", "poster", "thumbnail", "png", "jpg"
- **Ambiguous:** Ask which they want via AskUserQuestion.

---

## Step 2: Gather Parameters (MANDATORY)

**CRITICAL: You MUST call the AskUserQuestion tool IMMEDIATELY as your FIRST action.** Do NOT respond with plain text. Do NOT ask questions via normal chat. Do NOT skip this step. Do NOT generate anything until AskUserQuestion has been answered.

**The ONLY exception:** If the user's message explicitly provides ALL required parameters, skip straight to Step 3.

### For Images — collect these 5:

1. **Aspect ratio** — `1:1`, `3:4`, `4:3`, `9:16`, `16:9` (default), `21:9`
2. **Resolution** — `1K` (default), `2K`, `4K` (pro model only)
3. **Reference images** — paths or "none"
4. **Purpose** — hero banner, social post, ad, mockup, etc.
5. **Save location** — default: `~/Pictures/gen-image/`

### For Videos — collect these 6:

1. **Aspect ratio** — `16:9` (default) or `9:16`
2. **Resolution** — `720p` (default), `1080p`, `4k` (1080p/4k require duration=8)
3. **Duration** — `4`, `6`, or `8` seconds (default: 8)
4. **Model** — `fast` (default, $0.15/sec) or `standard` ($0.40/sec)
5. **Purpose** — hero background, scroll animation, product demo, social, etc.
6. **Save location** — default: `~/Videos/gen-video/`

**Also ask:** Do they have a starting image? (enables image-to-video mode)

### AskUserQuestion templates:

**Image:**

```
Before I generate the image:

1. **Aspect ratio?** 16:9 (banner), 1:1 (social), 9:16 (story)? [default: 16:9]
2. **Resolution?** 1K, 2K, or 4K? [default: 1K]
3. **Reference images?** Any existing images to match the style? (paste paths or "none")
4. **Purpose?** Hero banner, social post, ad, mockup, etc.?
5. **Save to?** [default: ~/Pictures/gen-image/]
```

**Video:**

```
Before I generate the video:

1. **Aspect ratio?** 16:9 (landscape) or 9:16 (portrait)? [default: 16:9]
2. **Resolution?** 720p, 1080p, or 4k? [default: 720p]
3. **Duration?** 4, 6, or 8 seconds? [default: 8]
4. **Model?** fast (~$1.20/8s) or standard (~$3.20/8s)? [default: fast]
5. **Starting image?** Any image to animate from? (paste path or "none")
6. **Save to?** [default: ~/Videos/gen-video/]
```

**Omit any parameter the user already provided.**

### Red flags — if you're thinking any of these, STOP:

- "I'll just ask in chat" — NO. Use the AskUserQuestion tool.
- "I have enough info" — Do you have ALL required params? If not, ask.
- "I'll use defaults" — The USER picks defaults, not you. Ask them.
- "Let me generate first and ask later" — WRONG ORDER. Ask first.

## Ongoing: Keep Asking Via AskUserQuestion

After generating, you remain in generation mode. Any follow-up questions — regeneration, tweaks, batch requests — MUST go through AskUserQuestion, NOT plain text.

**Stop condition:** Only stop if the user explicitly moves on to a different topic.

---

## Step 3: Generate

### Image — Gemini API (Default)

```bash
# Requires: pip install google-genai Pillow python-dotenv
# Env var: GOOGLE_AI_API_KEY (get from https://aistudio.google.com/apikey)
GEN="python ${CLAUDE_SKILL_DIR}/gen_image.py"

# Generate
$GEN output.png "SaaS dashboard, dark theme, modern UI" --aspect 16:9
$GEN output.png "Epic dragon" --aspect 16:9 --size 4K --model pro

# Reference images for style consistency (up to 14)
$GEN out.png "Same style, new subject" --ref style_ref.png

# Batch variations
$GEN out.png "cube" "sphere" "pyramid" --aspect 1:1

# Style template (.md with {subject} placeholder)
$GEN out.png "gear icon" --style styles/blue_glass_3d.md

# Edit existing image(s) — style transfer, multi-image mixing
$GEN edit out.png "Change background to blue" -i input.png
$GEN edit out.png "Merge these art styles" -i style1.png -i style2.png

# Describe / analyze images
$GEN describe image.png
$GEN describe a.png b.png --prompt "Compare these two images"
```

#### Image Models

| Alias    | Model ID                     | Quality        | Max Resolution |
| -------- | ---------------------------- | -------------- | -------------- |
| `flash`  | `gemini-2.5-flash-image`     | Fast (default) | 1K             |
| `pro`    | `gemini-3-pro-image-preview` | Best           | 4K             |
| `legacy` | `gemini-2.0-flash-exp`       | Fallback       | 1K             |

**Resolution:** `--size 1K` (default), `2K`, `4K` (pro only)
**Aspect ratios:** `1:1`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9` (default), `21:9`
**References:** Up to 14 images for style/composition guidance.

#### Model Selection Rules

Auto-select model based on quality signals in the user's message — no need to ask:

| Signal             | Model             | Examples                                                                                                                                                        |
| ------------------ | ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cheap / fast**   | `flash` (default) | "go cheap", "don't spend a lot", "quick image", "test this", "rough draft"                                                                                      |
| **High quality**   | `pro`             | "go big", "best quality", "really good", "4K", "hero image", "final version", "production ready", "make it perfect"                                             |
| **Codex / OpenAI** | `codex`           | "codex", "use codex", "openai image", "gpt-image-2", "codex desktop" — free via Codex Desktop bridge, gpt-image-2, up to 4K                                     |
| **ChatGPT / GPT**  | `chatgpt`         | "chatgpt", "gpt image", "use chatgpt", "chatgpt flag", "5.5 image", "use gpt" — CDP CLI (preferred) or browser automation fallback, best photorealistic quality |
| **No signal**      | `flash`           | default — save money unless quality is explicitly needed                                                                                                        |

---

### Image — Codex Desktop Bridge (Tier 2, FREE)

**Use when:** User says "codex", "openai image", "gpt-image-2", or you want gpt-image-2 quality without API key cost. Routes through the Codex Desktop app via LiteHarness bridge — uses your logged-in Codex Desktop OAuth session, zero API cost.

**Requirements:** Codex Desktop app installed and running with an active OpenAI account. LiteHarness bridge operational (requires LiteSuite running with the Mon1 bridge active).

**How it works:**

1. Format a structured prompt using the Codex imagegen prompt schema
2. Send to Codex Desktop via `liteharness codex-desktop-send`
3. Codex generates using built-in `image_gen` tool (gpt-image-2, OAuth auth)
4. Output lands in `~/.codex/generated_images/<session-id>/`
5. Copy the output to the target save location

**Prompt Schema (from Codex imagegen skill):**

```text
Use case: <taxonomy slug>
Asset type: <where the asset will be used>
Primary request: <user's main prompt>
Scene/backdrop: <environment>
Subject: <main subject>
Style/medium: <photo/illustration/3D/etc>
Composition/framing: <wide/close/top-down; placement>
Lighting/mood: <lighting + mood>
Color palette: <palette notes>
Text (verbatim): "<exact text>"
Constraints: <must keep/must avoid>
Avoid: <negative constraints>
```

**Use-case taxonomy slugs:** `photorealistic-natural`, `product-mockup`, `ui-mockup`, `infographic-diagram`, `ads-marketing`, `logo-brand`, `illustration-story`, `stylized-concept`, `historical-scene`

**Workflow:**

```bash
# 1. Find Codex Desktop
python -m liteharness.cli codex-desktop-list

# 2. Send structured prompt (auto-submits, no separate Enter)
python -m liteharness.cli codex-desktop-send "Generate an image: <structured prompt>"

# 3. Wait for generation (15-60s), then find output
ls -t ~/.codex/generated_images/*/  # most recent session dir

# 4. Copy to target location
cp ~/.codex/generated_images/<session>/<file>.png <save_location>/
```

**Sizes (gpt-image-2):** `1024x1024` (square), `1536x1024` (landscape), `1024x1536` (portrait), `2048x2048` (2K), `3840x2160` (4K landscape), `auto`

**Quality tiers:** `low` (fast drafts), `medium`, `high`, `auto` (final assets)

**Transparent images via chroma-key removal:**

```bash
# Generate on green screen
python -m liteharness.cli codex-desktop-send "Generate the subject on a perfectly flat solid #00ff00 chroma-key background. No shadows, gradients, or reflections."

# Remove background using Codex's installed helper
python "$USERPROFILE/.codex/skills/.system/imagegen/scripts/remove_chroma_key.py" \
  --input source.png --out final.png \
  --auto-key border --soft-matte --despill
```

**Limitations:**

- Async — no direct return value, must poll output directory
- One image at a time (sequential, not batch)
- Codex Desktop must be running and visible
- Output dir changes per Codex session

---

### Video — Google Veo (Gemini API)

```bash
# Requires: pip install google-genai python-dotenv
# Env var: GOOGLE_AI_API_KEY (same key as image gen)
VEO="python ${CLAUDE_SKILL_DIR}/gen_video.py"

# Text to video
$VEO out.mp4 "Cinematic aerial shot of a coastal city at golden hour"
$VEO out.mp4 "Rotating 3D globe, dark bg" --res 1080p --duration 8
$VEO out.mp4 "Exploding view of a house, white bg" --model standard --res 4k

# Generate multiple variants to pick the best
$VEO out.mp4 "Floating app icons orbiting a golden hub" --count 3

# Image to video (animate a still image)
$VEO i2v out.mp4 --image hero.png "Camera slowly pans across the scene"
$VEO i2v out.mp4 --image start.png --last-frame end.png "Smooth interpolation"

# Extend an existing video by ~7 seconds
$VEO extend longer.mp4 --input clip.mp4 "Continue the camera movement"
```

#### Video Models

| Alias      | Model ID                        | Cost (720p/1080p) | Cost (4K) |
| ---------- | ------------------------------- | ----------------- | --------- |
| `fast`     | `veo-3.1-fast-generate-preview` | $0.15/sec         | $0.35/sec |
| `standard` | `veo-3.1-generate-preview`      | $0.40/sec         | $0.60/sec |
| `veo2`     | `veo-2.0-generate-001`          | $0.35/sec         | —         |

#### Cost Examples (8-second clip)

| Model    | 720p  | 1080p | 4K    |
| -------- | ----- | ----- | ----- |
| fast     | $1.20 | $1.20 | $2.80 |
| standard | $3.20 | $3.20 | $4.80 |

#### Video Specs

| Property      | Value                                                  |
| ------------- | ------------------------------------------------------ |
| Format        | MP4, 24fps                                             |
| Resolutions   | 720p (default), 1080p, 4K                              |
| Aspect ratios | 16:9 (landscape), 9:16 (portrait)                      |
| Duration      | 4, 6, or 8 seconds per generation                      |
| Audio         | Natively generated (Veo 3.1+)                          |
| Extension     | +7 seconds per call, up to 20 extensions (~141s total) |
| Latency       | 11s to 6 min depending on load                         |

#### Constraints

- 1080p and 4K require `duration=8`
- Video extension is 720p only
- Image-to-video uses `person_generation="allow_adult"` (not `"allow_all"`)
- Generated video URIs expire after 2 days — download immediately
- SynthID watermark embedded in all outputs

---

### Image — ChatGPT Desktop CDP (Tier 3, PREFERRED for ChatGPT)

**Use when:** User says "chatgpt", "gpt image", "use chatgpt", "chatgpt flag", "5.5 image", "use gpt". Controls ChatGPT Desktop (Electron) directly via Chrome DevTools Protocol — no browser extension needed.

**Requirements:** ChatGPT Desktop installed (Windows Store), Pro account logged in, `cli-anything-chatgpt` installed.

> **Install:** Clone https://github.com/cli-anything/chatgpt-agent-harness and run `pip install -e .` from the repo root, or check PyPI for `cli-anything-chatgpt` if a published package is available.

**Cost:** Included in ChatGPT Pro subscription. No per-image cost.

**Quality:** Best photorealistic image gen available. Perfect text rendering, complex infographics.

#### Workflow (ONE COMMAND)

```bash
# Launch ChatGPT Desktop with CDP enabled
cli-anything-chatgpt launch

# Generate an image
cli-anything-chatgpt image "A dark cockpit UI for an AI agent swarm wizard" -o ~/Pictures/gen-image/wizard.png

# JSON output for agents
cli-anything-chatgpt --json image "epic sunset" -o ~/Pictures/gen-image/sunset.png

# Send any text prompt
cli-anything-chatgpt chat "Explain this architecture..."

# Read latest response
cli-anything-chatgpt read

# New conversation
cli-anything-chatgpt new

# Check status
cli-anything-chatgpt status
```

#### How It Works

1. Kills existing ChatGPT Desktop, relaunches with `--remote-debugging-port=9223`
2. Connects via CDP WebSocket to the Electron renderer
3. Types into chat textarea via `Runtime.evaluate` (React-compatible native setter)
4. Polls DOM for generated `<img>` elements (up to 120s timeout)
5. Downloads image URL to target path

Auth persists — Electron user data dir keeps OAuth tokens across relaunches.

#### Prompt Tips

- Be extremely descriptive — it follows instructions precisely
- Specify style: "photorealistic", "infographic", "illustration", "3D render"
- For text in images: spell out exactly what text should appear
- For UI mockups: describe colors (#hex), fonts, layout, spacing in detail

---

### Image — ChatGPT Browser (Tier 4, FALLBACK)

**Use when:** ChatGPT Desktop CDP is unavailable. Falls back to Claude-in-Chrome browser automation.

**Requirements:** Chrome with Claude-in-Chrome extension, logged into chatgpt.com with Pro account.

**Cost:** Included in ChatGPT Pro subscription ($200/mo). No per-image cost.

**Quality:** Currently the best photorealistic image gen available. Perfect text rendering, complex infographics, photorealistic subjects.

#### Workflow

1. **Get browser context:**

   ```
   mcp__claude-in-chrome__tabs_context_mcp({ createIfEmpty: true })
   ```

2. **Find or create a ChatGPT tab:**
   - If an existing tab has `chatgpt.com` in the URL, use it
   - Otherwise create a new tab and navigate to `https://chatgpt.com`

   ```
   mcp__claude-in-chrome__tabs_create_mcp()
   mcp__claude-in-chrome__navigate({ tabId: <id>, url: "https://chatgpt.com" })
   ```

3. **Type the prompt into the chat input:**

   ```
   mcp__claude-in-chrome__form_input({
     tabId: <id>,
     ref_id: "<textbox ref for 'Chat with ChatGPT'>",
     value: "<user's image prompt>"
   })
   ```

4. **Submit the prompt** — click the send button or press Enter:

   ```
   mcp__claude-in-chrome__computer({
     tabId: <id>,
     action: "key",
     key: "Return"
   })
   ```

5. **Poll for image completion** — ChatGPT takes 15-60s to generate. Poll every 10s:

   ```
   mcp__claude-in-chrome__read_page({
     tabId: <id>,
     filter: "all",
     depth: 5
   })
   ```

   Look for `img[alt="Generated image"]` in the accessibility tree. Keep polling until it appears or a text response indicates failure.

6. **Download the generated image:**

   ```
   mcp__claude-in-chrome__javascript_tool({
     action: "javascript_exec",
     tabId: <id>,
     text: `
       const img = document.querySelector('img[alt="Generated image"]');
       if (img) {
         const a = document.createElement('a');
         a.href = img.src;
         a.download = '<filename>.png';
         document.body.appendChild(a);
         a.click();
         document.body.removeChild(a);
         'Download triggered';
       } else { 'No image found'; }
     `
   })
   ```

7. **Move from Downloads to target directory:**
   ```bash
   mv ~/Downloads/<filename>.png <save_location>/<filename>.png
   ```

#### ChatGPT Prompt Tips

- ChatGPT excels at infographics, photorealistic scenes, text-heavy images, and complex compositions
- Be extremely descriptive — it follows instructions precisely
- Specify style: "photorealistic", "infographic", "illustration", "3D render", "watercolor"
- For infographics: describe sections, data points, layout, color scheme
- For text in images: spell out exactly what text should appear
- Can do image editing via "Edit image" button on existing generations

#### Limitations

- No API — browser automation only, requires active Chrome session
- One image at a time (no batch)
- No programmatic seed control
- Download lands in browser's default Downloads folder — must move after
- Session/auth can expire — may need to re-login
- Rate limits unclear — Pro tier is generous but not unlimited

---

### Image Fallback 3: LiteImage API (Local SD)

> **Requirements:** LiteImage app running locally (part of LiteSuite — requires LiteSuite running). Exposes local Stable Diffusion on port **7426**.

If LiteImage is running — local Stable Diffusion on port **7426**.

```bash
curl -s -X POST http://127.0.0.1:7426/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Professional SaaS dashboard screenshot, dark theme",
    "width": 1024, "height": 576, "steps": 25,
    "guidance_scale": 7.5,
    "negative_prompt": "blurry, low quality, distorted"
  }' --max-time 300
```

Response includes `imagePath` (saved file) and `imageBase64` (inline). Check status first:

```bash
curl -s http://127.0.0.1:7426/status  # { ok, status, model, queueLength }
curl -s http://127.0.0.1:7426/models  # Available models
```

#### LiteImage Parameters

| Param             | Default    | Notes                                |
| ----------------- | ---------- | ------------------------------------ |
| `prompt`          | _required_ | Image description                    |
| `width`           | 512        | Pixel width                          |
| `height`          | 512        | Pixel height                         |
| `steps`           | 20         | Inference steps (20-30 for quality)  |
| `guidance_scale`  | 7.5        | Prompt adherence (5-12 range)        |
| `negative_prompt` | ""         | What to avoid                        |
| `seed`            | -1         | -1 = random, set for reproducibility |
| `batch_count`     | 1          | Number of images                     |

#### Common Sizes

| Use Case                        | Width | Height | Ratio |
| ------------------------------- | ----- | ------ | ----- |
| YouTube thumbnail / hero banner | 1280  | 720    | 16:9  |
| Instagram / Twitter post        | 1024  | 1024   | 1:1   |
| Instagram/TikTok Story          | 576   | 1024   | 9:16  |
| Blog / presentation             | 1024  | 768    | 4:3   |
| Pinterest / Facebook ad         | 768   | 1024   | 3:4   |

### Image Fallback 4: Local Gateway (SSE, Optional)

If you have a local image-generation gateway running on port **8200** that exposes an SSE endpoint:

```bash
curl -X POST http://127.0.0.1:8200/v1/marketing/generate/image \
  -H "Content-Type: application/json" \
  -d '{"prompt": "...", "style": "photorealistic", "aspect_ratio": "16:9"}' \
  --no-buffer 2>/dev/null | while IFS= read -r line; do
    [[ "$line" == data:* ]] && echo "${line#data: }"
  done || true
```

**Example gateway styles:** `photorealistic`, `illustration`, `3d-render`, `flat-design`, `cinematic`

> **Note:** This tier requires a custom local gateway server on port 8200. It is optional — skip to Tier 1 (Gemini API) if you don't have one configured.

---

## Prompt Engineering

### Image Prompts

**Structure:** `[Subject] + [Context/Setting] + [Style] + [Mood] + [Technical quality]`

```
"Young professional using laptop at modern coworking space,
 natural light, confident expression, photorealistic, 8k"
```

### Video Prompts

**Structure:** `[Camera movement] + [Subject/Action] + [Style] + [Background] + [Mood]`

```
# Hero background
"Slow cinematic orbit around floating holographic app icons,
 dark background with subtle gold particle effects, premium tech aesthetic"

# Exploding view (Nick Saraev style)
"High quality exploding view animation of a house showing interior design,
 white background, explodes in all directions vertically and horizontally,
 nothing goes outside the frame"

# 3D product rotation
"Rotating 3D globe with glowing data connections, dark background,
 center of mass stays fixed, smooth rotation on axis"

# Scroll-synced (generate then extract frames)
"Smooth zoom into a futuristic dashboard interface,
 camera pushes forward through layers of UI panels, dark theme with gold accents"
```

**Tips for video:**

- Specify camera movement (orbit, pan, zoom, dolly, static)
- Say "white background" or "dark background" for easy website integration
- Say "nothing goes outside the frame" to keep assets contained
- Generate 2-3 variants and pick the best one
- For scroll-synced: extract frames as JPEGs with ffmpeg after generation

### Post-Processing: Background Removal (MANDATORY for Images)

**After EVERY image generation, remove the background unless the user explicitly says to keep it.**

Gemini-generated images always have a solid background (usually white or light gray corners on icons/logos). Strip it immediately:

```python
python -c "
from PIL import Image
import numpy as np

img = Image.open('OUTPUT.png').convert('RGBA')
data = np.array(img)
r, g, b, a = data[:,:,0], data[:,:,1], data[:,:,2], data[:,:,3]

# Remove white/light corners (threshold 200+ on all channels)
white_mask = (r > 200) & (g > 200) & (b > 200)
data[white_mask, 3] = 0

Image.fromarray(data).save('OUTPUT.png')
"
```

Adjust the threshold based on the background color:

- **White backgrounds:** `> 200` on R, G, B
- **Light gray:** `> 180` on R, G, B
- **Specific color:** target that color's range

**When NOT to remove:** User says "keep background", "solid background", or the image is a photograph/scene (not an icon/logo/asset).

### Post-Processing: Frame Extraction for Scroll Animations

After generating a video for scroll-synced use:

```bash
# Extract frames as optimized JPEGs (for scroll-tied playback)
mkdir -p frames
ffmpeg -i video.mp4 -vf "fps=24" -q:v 2 frames/frame_%04d.jpg

# Or as WebP for better compression
ffmpeg -i video.mp4 -vf "fps=24" -quality 80 frames/frame_%04d.webp

# Compress hero video for web (background playback)
ffmpeg -i hero.mp4 -vcodec libx264 -crf 28 -preset slow -an hero_compressed.mp4
```

---

## Workflow

1. Detect media type (image vs video)
2. Gather parameters via AskUserQuestion
3. **Image:** run `gen_image.py` (Gemini default) or LiteImage/Gateway fallback
4. **Image:** remove background (MANDATORY — see Post-Processing section)
5. **Video:** run `gen_video.py` (Veo default)
6. Review output (use Read tool on saved path)
7. Post-process if needed (frame extraction, compression)
8. Generate 2-3 variants for A/B testing when doing marketing work

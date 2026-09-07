---
name: ls-gen-image-or-video
description: Use when generating AI images or videos — marketing visuals, hero banners, social media assets, product mockups, ad creative, animated backgrounds, exploding views, 3D renders, scroll-synced video assets. Triggers on 'generate an image', 'create an image', 'gen-image', 'make me an image', 'image gen', 'imagegen', 'codex image', 'marketing image', 'product mockup', 'hero image', 'generate a video', 'create a video', 'gen-video', 'make me a video', 'video gen', 'animated background', 'exploding view', 'veo', 'video asset', 'scroll animation video', 'chatgpt image', 'gpt image'.
---

# AI Image & Video Generation

## Image Generation

Three tiers: **1) Codex ImageGen** (default — `codex_image.py`, your ChatGPT/Codex login, no API key, no per-image cost) → **2) LiteImage** (local Stable Diffusion) → **3) Local Gateway** (SSE, optional).

Every image that leaves this machine for a cloud model goes through tier 1. There is no Gemini image route, no Codex Desktop bridge, no ChatGPT Desktop CDP driver and no browser automation in this skill any more: the same ChatGPT credit those routes spent is reached directly, with one stdlib script and a real return value.

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

### For Images — collect these 4:

1. **Aspect ratio** — `1:1`, `3:4`, `4:3`, `9:16`, `16:9` (default), `21:9`. On this route the ratio is WRITTEN INTO THE PROMPT ("wide 16:9 banner", "square", "tall 9:16 story"); size and quality are `auto` on the wire.
2. **Reference images** — paths or "none" (a reference turns the call into an EDIT: the model sees the picture)
3. **Purpose** — hero banner, social post, ad, mockup, icon set, etc.
4. **Save location** — default: `~/Pictures/gen-image/`

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
2. **Reference images?** Any existing images to match or edit? (paste paths or "none")
3. **Purpose?** Hero banner, social post, ad, mockup, icon set, etc.?
4. **Save to?** [default: ~/Pictures/gen-image/]
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

### Image — Codex ImageGen (Tier 1, DEFAULT)

Generates or edits PNGs through the user's Codex/ChatGPT login: the script presents itself as the `codex_cli_rs` client to `https://chatgpt.com/backend-api/codex`, so the images are paid for by the ChatGPT subscription Codex already uses. **Stdlib-only Python, no `OPENAI_API_KEY`, no desktop app, no browser.** Origin: the LiteTUI seat's `skills/codex-imagegen` (LiteTUI 7a273be, measured 2026-09-05); the copy that ships is the one beside this file.

**Requirements:** Codex CLI has been logged in once on this machine (`codex login`), so `~/.codex/auth.json` holds `tokens.{access_token, refresh_token, account_id}`. Nothing else.

```bash
GEN="python ${CLAUDE_SKILL_DIR}/codex_image.py"

# Generate (default backend `responses`, model gpt-5.5 with its native image_generation tool)
$GEN "SaaS dashboard, dark theme, modern UI, wide 16:9 banner" --out ~/Pictures/gen-image/dashboard.png

# Typed endpoint (OpenAI-style JSON, model gpt-image-2)
$GEN "Epic dragon over a medieval city, cinematic, 16:9" --backend images --out dragon.png
$GEN "..." --backend images --model gpt-image-2

# EDIT with reference image(s) — repeatable, png/jpg/gif/webp; the model genuinely sees them
$GEN "restyle this fox in watercolor, keep the pose" --image fox.png --out fox-watercolor.png
$GEN "merge these two art styles into one poster" --image a.png --image b.png --out merged.png

# Token only: refresh the OAuth token and write it back to ~/.codex/auth.json
$GEN --refresh-only
```

`--image` chooses the edit shape automatically: `responses` sends inline `input_image` parts in the user message; `images` posts to `/images/edits`. `--out` defaults to `codex-image-<timestamp>.png` in the cwd; `--timeout` defaults to 300 s. The script prints the output path on stdout and its log lines on stderr (`[codex_image] ...`), and exits 1 on any failure.

#### Backends

| `--backend`           | Endpoint             | Model (default) | Use it for                                                                          |
| --------------------- | -------------------- | --------------- | ----------------------------------------------------------------------------------- |
| `responses` (default) | `/responses`         | `gpt-5.5`       | everything — the model reads the prompt like a chat turn and calls image generation |
| `images`              | `/images/generations`| `gpt-image-2`   | the typed OpenAI shape; the retry when `responses` answers in text instead          |

Both backends were measured on 2026-09-05: real PNGs in ~20–30 s each; both edit paths kept an exact pose and composition from the reference. **Model gotcha:** `gpt-5.4` returns HTTP 400 on this account — use `gpt-5.5` (responses) or `gpt-image-2` (images). The account's model list is in `~/.codex/models_cache.json`.

**Size and quality are `auto` on the wire** (`build_images_body` sends `background/quality/size: auto`). Put the ratio and the resolution wish in the prompt: "wide 16:9", "square 1:1", "tall 9:16 story", "high detail, 4K-grade". A `--size` passthrough for the `images` backend (gpt-image-2 accepts `1024x1024`, `1536x1024`, `1024x1536`, `2048x2048`, `3840x2160`) is not exposed yet.

#### Route Selection Rules

The words that used to pick between clouds now all land on this one route — no need to ask:

| Signal                                                                | Route                          |
| --------------------------------------------------------------------- | ------------------------------ |
| "codex", "codex image", "imagegen", "gpt image", "chatgpt image", "use gpt", "openai image", "gpt-image-2", "go big", "best quality", "hero image", "final version", "quick image", "rough draft", no signal | `codex_image.py` (`responses`) |
| "gpt-image-2", "typed", or `responses` answered in text               | `codex_image.py --backend images` |
| "local", "offline", "stable diffusion", "SD", or the machine is offline | LiteImage (tier 2)             |

#### How auth works (measured 2026-09-05)

- Tokens live in `~/.codex/auth.json` → `tokens.{access_token, refresh_token, account_id}`.
- Access tokens are short-lived (~15 min). The script checks the JWT `exp`; on a 401 it force-refreshes via `POST https://auth.openai.com/oauth/token` (`grant_type=refresh_token`, the `codex_cli_rs` client id) and retries once.
- The refresh token ROTATES — the script writes both back, so the Codex CLI stays logged in too.

#### Prompt Tips

- Be extremely descriptive — it follows instructions precisely.
- Specify style: "photorealistic", "infographic", "illustration", "3D render", "watercolor".
- For text in images: spell out exactly what text should appear, in quotes.
- For UI mockups: describe colors (#hex), fonts, layout, spacing in detail.
- A structured prompt still helps on the `responses` backend:

```text
Use case: <photorealistic-natural | product-mockup | ui-mockup | infographic-diagram | ads-marketing | logo-brand | illustration-story | stylized-concept>
Asset type: <where the asset will be used>
Primary request: <the main prompt>
Subject / Scene / Style / Composition / Lighting / Palette
Text (verbatim): "<exact text>"
Constraints: <must keep / must avoid>
```

#### Transparent images via chroma-key removal

```bash
# Generate on green screen
$GEN "<subject>, isolated on a perfectly flat solid #00ff00 chroma-key background, no shadows, no gradients, no reflections, no text" --out raw.png

# Remove the background with Codex's installed helper (present wherever the Codex CLI is)
python "$USERPROFILE/.codex/skills/.system/imagegen/scripts/remove_chroma_key.py" \
  --input raw.png --out final.png --auto-key border --soft-matte --despill --force
```

Without `--force` it refuses to overwrite. Keep the green-screen raw as a fallback; commit only the transparent final. If the helper is absent, the PIL snippet under Post-Processing does the flat-colour case.

#### Batch generation & reliability (hard-won on a 75-asset game UI run, then moved to this route)

One request per asset, in a loop — the API route has no conversation, so the old thread-context drift is gone, but three of the old traps survive because they live in the account, not the app:

- **Sprite-sheet drift** still happens on icon prompts. Keep the hardened tail: "a SINGLE emblem, ONE icon centered, NOT a sprite sheet, NOT a grid, NOT a set, no second icon."
- **The image cap is ACCOUNT-WIDE (~50 gens/burst).** Onset is silent — a call returns text instead of an image, or `stream ended without a completed image_generation_call` — then the API refuses outright. Resubmits do not help; only a cooldown clears it (observed 30–120+ min). **During onset gens can come back wrong and look "successful"**, so the worst corruption clusters on whichever batch ran as the cap hit. Probe with one cheap gen every ~10 min to detect the lift.
- **ALWAYS eyeball a contact sheet after a batch — exit codes lie.** Montage the finals, audit every cell, and regen only the multi-icon / wrong-subject ones. A contact-sheet script to copy: `C:\Projects\private\TypeOrDie\scripts\genui\contact_sheet.py`.

#### Troubleshooting

- **HTTP 401 twice / refresh failed** — no usable `refresh_token` in `auth.json`: re-login with `codex login`, then retry.
- **HTTP 400 on a model name** — wrong model for the account; check `~/.codex/models_cache.json`.
- **"stream ended without a completed image_generation_call"** — the model answered in text instead of calling the tool: retry once, or force `--backend images`.
- **No `~/.codex/auth.json`** — Codex CLI was never logged in on this machine; there is no key-based fallback in this skill by design.

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

### Image — LiteImage API (Tier 2, local Stable Diffusion)

> **Requirements:** LiteImage app running locally (part of LiteSuite — requires LiteSuite running). Exposes local Stable Diffusion on port **7426**.

Use it when the user says "local", "offline" or "stable diffusion", or when tier 1 is unreachable.

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

### Image — Local Gateway (Tier 3, SSE, optional)

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

> **Note:** This tier requires a custom local gateway server on port 8200. It is optional — skip to Tier 1 (Codex ImageGen) if you don't have one configured.

---

## Prompt Engineering

### Image Prompts

**Structure:** `[Subject] + [Context/Setting] + [Style] + [Mood] + [Technical quality] + [Frame]`

```
"Young professional using laptop at modern coworking space,
 natural light, confident expression, photorealistic, 8k, wide 16:9"
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

Generated icons and logos come on a solid background (usually white or light grey corners). For a clean cut, generate on green and use the chroma-key helper above; for a flat white/grey background, strip it immediately:

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

## Protocol

1. Detect media type (image vs video)
2. Gather parameters via AskUserQuestion
3. **Image:** run `codex_image.py` (Codex ImageGen, default) or LiteImage/Gateway fallback
4. **Image:** remove background (MANDATORY — see Post-Processing section)
5. **Video:** run `gen_video.py` (Veo default)
6. Review output (use Read tool on saved path)
7. Post-process if needed (frame extraction, compression)
8. Generate 2-3 variants for A/B testing when doing marketing work

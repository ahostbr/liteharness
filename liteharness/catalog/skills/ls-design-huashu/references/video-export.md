# Video Export: Exporting HTML Animations to MP4/GIF

Once an animated HTML is complete, users often ask "can I export this as a video?" This guide provides the complete workflow.

## When to Export

**Export timing**:

- The animation runs completely and has been visually verified (Playwright screenshots confirm correct state at each time point)
- The user has viewed it in a browser at least once and confirmed the effect is OK
- **Do not** export while there are still animation bugs — fixing things after export to video is more costly

**Trigger phrases users might say**:

- "Can I export this as a video?"
- "Convert to MP4"
- "Make it a GIF"
- "60fps"

## Output Specifications

Default: deliver three formats at once and let the user choose:

| Format    | Spec                                                          | Best for                                                | Typical size (30s) |
| --------- | ------------------------------------------------------------- | ------------------------------------------------------- | ------------------ |
| MP4 25fps | 1920x1080 · H.264 · CRF 18                                    | WeChat Official Accounts embed, Video Channels, YouTube | 1-2 MB             |
| MP4 60fps | 1920x1080 · minterpolate frame interpolation · H.264 · CRF 18 | High-framerate showcasing, Bilibili, portfolio          | 1.5-3 MB           |
| GIF       | 960x540 · 15fps · palette optimized                           | Twitter/X, README, Slack previews                       | 2-4 MB             |

## Toolchain

Two scripts in `scripts/`:

### 1. `render-video.js` — HTML to MP4

Records a base 25fps MP4. Requires global playwright.

```bash
NODE_PATH=$(npm root -g) node ${CLAUDE_SKILL_DIR}/scripts/render-video.js <html-file>
```

Optional parameters:

- `--duration=30` animation duration (seconds)
- `--width=1920 --height=1080` resolution
- `--trim=2.2` seconds to trim from the start of the video (removes reload + font loading time)
- `--fontwait=1.5` font loading wait time (seconds), increase for many fonts

Output: same directory as the HTML file, same name with `.mp4` extension.

### 2. `add-music.sh` — MP4 + BGM -> MP4

Mixes background music into a silent MP4, selecting from the built-in BGM library by scene (mood), or bring your own audio. Automatically matches duration and adds fade in/out.

```bash
bash add-music.sh <input.mp4> [--mood=<name>] [--music=<path>] [--out=<path>]
```

**Built-in BGM library** (in `assets/bgm-<mood>.mp3`):

| `--mood=`         | Style                                                      | Best for                                                   |
| ----------------- | ---------------------------------------------------------- | ---------------------------------------------------------- |
| `tech` (default)  | Apple Silicon / Apple Keynote style, minimal synth + piano | Product launches, AI tools, skill promotions               |
| `ad`              | Upbeat modern electronic with build + drop                 | Social media ads, product teasers, promotional videos      |
| `educational`     | Warm and bright, light guitar/electric piano, inviting     | Science explainers, tutorial intros, course previews       |
| `educational-alt` | Same category, different track                             | Same as above                                              |
| `tutorial`        | Lo-fi ambient, nearly invisible                            | Software demos, programming tutorials, long demonstrations |
| `tutorial-alt`    | Same category, different track                             | Same as above                                              |

**Behavior**:

- Music is trimmed to the video's duration
- 0.3s fade-in + 1s fade-out (avoids hard cuts)
- Video stream uses `-c:v copy` with no re-encoding; audio is AAC 192k
- `--music=<path>` takes priority over `--mood` — you can specify any external audio directly
- Passing an incorrect mood name will list all available options, no silent failure

**Typical pipeline** (animation export trio + music):

```bash
node render-video.js animation.html                        # record
bash convert-formats.sh animation.mp4                      # derive 60fps + GIF
bash add-music.sh animation-60fps.mp4                      # add default tech BGM
# Or for different scenarios:
bash add-music.sh tutorial-demo.mp4 --mood=tutorial
bash add-music.sh product-promo.mp4 --mood=ad --out=promo-final.mp4
```

### 3. `convert-formats.sh` — MP4 -> 60fps MP4 + GIF

Generates a 60fps version and a GIF from an existing MP4.

```bash
bash ${CLAUDE_SKILL_DIR}/scripts/convert-formats.sh <input.mp4> [gif_width] [--minterpolate]
```

Output (same directory as input):

- `<name>-60fps.mp4` — defaults to `fps=60` frame duplication (broad compatibility); add `--minterpolate` to enable high-quality frame interpolation
- `<name>.gif` — palette-optimized GIF (960 wide by default, configurable)

**60fps mode selection**:

| Mode                        | Command                                    | Compatibility                              | When to use                                                                                                      |
| --------------------------- | ------------------------------------------ | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------- |
| Frame duplication (default) | `convert-formats.sh in.mp4`                | QuickTime / Safari / Chrome / VLC all work | General delivery, platform uploads, social media                                                                 |
| minterpolate interpolation  | `convert-formats.sh in.mp4 --minterpolate` | macOS QuickTime/Safari may refuse to open  | Bilibili and similar where true interpolation is needed — **must test in target player locally before delivery** |

Why default changed to frame duplication? The H.264 elementary stream output by minterpolate has a known compat bug — the previous minterpolate default caused "macOS QuickTime won't open" issues multiple times. See `animation-pitfalls.md` section 14.

`gif_width` parameter:

- 960 (default) — general-purpose for social platforms
- 1280 — sharper but larger file
- 600 — priority loading on Twitter/X

## Complete Workflow (Standard Recommendation)

After the user says "export video":

```bash
cd <project-directory>

# Assume $SKILL points to this skill's root directory (replace with your install path)

# 1. Record base 25fps MP4
NODE_PATH=$(npm root -g) node "$SKILL/scripts/render-video.js" my-animation.html

# 2. Derive 60fps MP4 and GIF
bash "$SKILL/scripts/convert-formats.sh" my-animation.mp4

# Output:
# my-animation.mp4         (25fps · 1-2 MB)
# my-animation-60fps.mp4   (60fps · 1.5-3 MB)
# my-animation.gif         (15fps · 2-4 MB)
```

## Technical Details (for troubleshooting)

### Playwright recordVideo Gotchas

- Frame rate is fixed at 25fps — cannot record 60fps directly (Chromium headless compositor limit)
- Recording starts from context creation — must use `trim` to cut the leading load time
- Default format is webm — needs ffmpeg conversion to H.264 MP4 for universal playback

`render-video.js` handles all of the above.

### ffmpeg minterpolate Parameters

Current config: `minterpolate=fps=60:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1`

- `mi_mode=mci` — motion compensation interpolation
- `mc_mode=aobmc` — adaptive overlapped block motion compensation
- `me_mode=bidir` — bidirectional motion estimation
- `vsbmc=1` — variable-size block motion compensation

Works well for CSS **transform animations** (translate / scale / rotate).

May produce slight ghosting on **pure fades** — if the user objects, fall back to simple frame duplication:

```bash
ffmpeg -i input.mp4 -r 60 -c:v libx264 ... output.mp4
```

### Why GIF Palette Needs Two Passes

GIF is limited to 256 colors. A single-pass GIF compresses all colors across the entire animation into a 256-color universal palette, which produces a blurry result for delicate palettes like cream backgrounds with orange accents.

Two passes:

1. `palettegen=stats_mode=diff` — scans the entire clip first, generating an **optimal palette for this specific animation**
2. `paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle` — encodes using that palette; rectangle diff only updates changed regions, significantly reducing file size

`dither=bayer` produces smoother fade transitions than `none`, but files are slightly larger.

## Pre-flight Check (Before Export)

30-second self-check before exporting:

- [ ] HTML has been run completely in the browser with no console errors
- [ ] Frame 0 of the animation is a complete initial state (not a blank loading screen)
- [ ] The last frame of the animation is a stable end state (not cut off mid-way)
- [ ] All fonts / images / emojis render correctly (refer to `animation-pitfalls.md`)
- [ ] The duration parameter matches the actual animation duration in the HTML
- [ ] The HTML's Stage detection has `window.__recording` forcing loop=false (must check for hand-written Stage; `assets/animations.jsx` includes this automatically)
- [ ] The ending Sprite has `fadeOut={0}` (last frame of video does not fade out)
- [ ] Includes "Created by Huashu-Design" watermark (required for animation scenes only; for third-party branded work add "Unofficial · " prefix. See SKILL.md section "Skill Promotion Watermark")

## Delivery Notes to Include

Standard format for notes to give the user after export:

```
**Complete Delivery**

| File | Format | Spec | Size |
|---|---|---|---|
| foo.mp4 | MP4 | 1920x1080 · 25fps · H.264 | X MB |
| foo-60fps.mp4 | MP4 | 1920x1080 · 60fps (motion interpolation) · H.264 | X MB |
| foo.gif | GIF | 960x540 · 15fps · palette optimized | X MB |

**Notes**
- 60fps uses minterpolate motion estimation interpolation — great for transform animations
- GIF uses palette optimization — 30s animation can be compressed to around 3MB

Let me know if you need a different size or frame rate.
```

## Common Follow-Up Requests

| User says                     | Response                                                                                     |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| "Too large"                   | MP4: increase CRF to 23-28; GIF: reduce resolution to 600 or fps to 10                       |
| "GIF is too blurry"           | Increase `gif_width` to 1280; or suggest using MP4 instead (WeChat Moments also supports it) |
| "Need portrait 9:16"          | Change HTML source with `--width=1080 --height=1920`, re-record                              |
| "Add watermark"               | Add `-vf "drawtext=..."` or `overlay=` a PNG in ffmpeg                                       |
| "Need transparent background" | MP4 doesn't support alpha; use WebM VP9 + alpha or APNG                                      |
| "Need lossless"               | Set CRF to 0 + preset veryslow (file will be 10x larger)                                     |

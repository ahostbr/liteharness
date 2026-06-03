---
name: ls-comfy-to-liteimage
description: "Translate ComfyUI workflows into LiteImage API calls. Use when the user provides a ComfyUI workflow JSON, mentions ComfyUI, asks about node graphs, wants to convert or replicate a ComfyUI pipeline, or references ComfyUI templates. Also use when the user asks how to do something 'like in ComfyUI' or wants to run a workflow locally. Triggers on 'comfy', 'ComfyUI', 'comfyui', 'workflow JSON', 'node graph', 'convert this workflow', 'translate this workflow', 'run this workflow', 'I have a workflow', 'KSampler', 'LoraLoader', 'exported workflow', 'comfy template', 'what this comfy workflow does', 'equivalent in LiteImage', 'how do I do that here', 'make it work here'."
---

# ComfyUI → LiteImage Workflow Translator

Translate ComfyUI workflow JSON into LiteImage REST API call sequences. ComfyUI uses a node graph where each node is a processing step. LiteImage collapses these into parameterized API calls — no nodes, no wiring, just endpoints.

> **Requirements:** This skill requires [LiteImage](https://litesuite.dev) to be installed and running. All API calls target `http://127.0.0.1:7426` (LiteImage's default port). Start LiteImage before running any generated commands.

## How to Use This Skill

1. User provides a ComfyUI workflow (JSON file, pasted JSON, or describes a ComfyUI pipeline)
2. Parse the workflow to identify the intent (what nodes are present, how they connect)
3. Map each node/group to the equivalent LiteImage API call(s)
4. Output a ready-to-run sequence of `curl` commands (or describe the MCP tool calls)

## Node → API Mapping Reference

### Model Loading

| ComfyUI Node                          | LiteImage Equivalent                                                                                 |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `CheckpointLoaderSimple`              | `POST /load_model {"file_path": "..."}`                                                              |
| `UNETLoader` / `DiffusionModelLoader` | `POST /load_model {"file_path": "..."}` (LiteImage auto-detects family)                              |
| `LoraLoader` / `LoraLoaderModelOnly`  | Add `<lora:name>` tag to prompt + set `lora_dir` on `/generate`                                      |
| `VAELoader`                           | Not needed — LiteImage auto-selects VAE per model family. If custom: set `externalVaePath` in config |
| `CLIPLoader` / `DualCLIPLoader`       | Not needed — LiteImage bundles and auto-selects CLIP/T5/LLM encoders per family                      |

**Key insight:** ComfyUI makes you wire model + VAE + CLIP + LoRA as separate nodes. LiteImage handles all of this automatically when you `POST /load_model`. The only thing you need to extract from loader nodes is the **model filename** and any **LoRA names**.

### Text-to-Image Generation

| ComfyUI Node                | LiteImage Equivalent                                                                                    |
| --------------------------- | ------------------------------------------------------------------------------------------------------- |
| `CLIPTextEncode` (positive) | `prompt` parameter on `/generate`                                                                       |
| `CLIPTextEncode` (negative) | `negative_prompt` parameter on `/generate`                                                              |
| `EmptyLatentImage`          | `width` and `height` parameters on `/generate`                                                          |
| `KSampler`                  | `/generate` with `steps`, `guidance_scale` (cfg), `seed`, `sampling_method` (sampler_name), `scheduler` |
| `KSamplerAdvanced`          | Same as KSampler — map `start_at_step`/`end_at_step` to `steps`                                         |
| `VAEDecode`                 | Automatic — LiteImage always decodes to PNG                                                             |
| `SaveImage`                 | `output_path` or `save_to_gallery: true` on `/generate`                                                 |
| `PreviewImage`              | Not needed — `/generate` returns `imageBase64` inline                                                   |

**Sampler name mapping:**
| ComfyUI | LiteImage |
|---------|-----------|
| `euler` | `euler` |
| `euler_ancestral` | `euler_a` |
| `heun` | `heun` |
| `dpm_2` | `dpm2` |
| `dpm_2_ancestral` | `dpm2` |
| `dpmpp_2s_ancestral` | `dpm++2s_a` |
| `dpmpp_2m` | `dpm++2m` |
| `dpmpp_2m_sde` | `dpm++2mv2` |
| `ipndm` | `ipndm` |
| `ipndm_v` | `ipndm_v` |
| `lcm` | `lcm` |

**Scheduler mapping:**
| ComfyUI | LiteImage |
|---------|-----------|
| `normal` | `default` |
| `karras` | `karras` |
| `exponential` | `exponential` |
| `sgm_uniform` | `discrete` |
| `simple` | `default` |
| `ddim_uniform` | `default` |
| `beta` | `default` |
| `ays` / `align_your_steps` | `ays` |

### Image-to-Image

| ComfyUI Node                                 | LiteImage Equivalent                                               |
| -------------------------------------------- | ------------------------------------------------------------------ |
| `LoadImage` → KSampler with `denoise < 1.0`  | `/generate` with `init_image_path` + `strength` (= denoise value)  |
| `VAEEncode` (on an image) → KSampler         | Same — LiteImage handles encoding internally                       |
| `ImageScale` / `ImageResize` before KSampler | Set `width`/`height` on `/generate` — LiteImage resizes internally |

**Rule:** If you see a `LoadImage` feeding into a `KSampler` (via VAEEncode or directly), it's img2img. The `denoise` parameter on KSampler = LiteImage's `strength`.

### Inpainting

| ComfyUI Node                             | LiteImage Equivalent                                   |
| ---------------------------------------- | ------------------------------------------------------ |
| `LoadImage` (image) + `LoadImage` (mask) | `init_image_path` + `mask_path` on `/generate`         |
| `SetLatentNoiseMask`                     | Implicit — providing `mask_path` enables inpaint mode  |
| `InpaintModelConditioning`               | Not needed — LiteImage handles conditioning internally |
| `VAEEncodeForInpaint`                    | Not needed                                             |

**Rule:** If you see a mask being loaded or created, it's inpaint. Extract the mask image path and pass as `mask_path`.

### ControlNet

| ComfyUI Node                                  | LiteImage Equivalent                                     |
| --------------------------------------------- | -------------------------------------------------------- |
| `ControlNetLoader`                            | `control_net_model` parameter (path to .safetensors)     |
| `ControlNetApplyAdvanced` / `ControlNetApply` | `control_image_path` + `control_strength` on `/generate` |
| `CannyEdgePreprocessor`                       | `use_canny: true` on `/generate`                         |
| `DepthAnythingPreprocessor` / `MiDaS`         | Provide depth map as `control_image_path`                |
| `DWPosePreprocessor` / `OpenPose`             | Provide pose image as `control_image_path`               |

**Rule:** ControlNet in ComfyUI is a separate loader + apply node. In LiteImage, just add `control_net_model`, `control_image_path`, and `control_strength` to your `/generate` call. Note: ControlNet only works with SD1.5 and SDXL in LiteImage.

### Upscaling

| ComfyUI Node                                   | LiteImage Equivalent                                                              |
| ---------------------------------------------- | --------------------------------------------------------------------------------- |
| `UpscaleModelLoader` + `ImageUpscaleWithModel` | `POST /upscale {"image_path": "...", "upscale_model": "..."}`                     |
| `ImageScaleBy` / `LatentUpscale`               | Not directly supported — use `/upscale` with ESRGAN model                         |
| `UltimateSDUpscale`                            | Use `/upscale` (simpler, no tiling logic exposed but `tile_size` param available) |

### Video Generation (Wan 2.1 / LTX)

| ComfyUI Node                                      | LiteImage Equivalent                                                   |
| ------------------------------------------------- | ---------------------------------------------------------------------- |
| `WanVideoModelLoader` / `DownloadAndLoadWanVideo` | Config: set `videoModelPath` to Wan GGUF                               |
| `WanVideoTextEncode`                              | `prompt` on `POST /video/generate`                                     |
| `WanVideoSampler`                                 | `steps`, `guidance_scale`, `seed` on `/video/generate`                 |
| `WanVideoImageEncode` (I2V)                       | `init_image_path` on `/video/generate`                                 |
| `VHS_VideoCombine` / `SaveAnimatedWEBP`           | Automatic — LiteImage outputs MP4 + GIF via ffmpeg                     |
| LTX Video nodes                                   | Same mapping — LiteImage uses Wan2.1 but the API params are equivalent |

**Video is async:** `POST /video/generate` returns a `job_id`. Poll `GET /video/status/{id}` for progress.

### Face Swap

| ComfyUI Node                          | LiteImage Equivalent                                            |
| ------------------------------------- | --------------------------------------------------------------- |
| `ReActorFaceSwap` / `IPAdapterFaceID` | `POST /faceswap {"source_image": "...", "target_image": "..."}` |
| `FaceDetailer` / `GFPGAN`             | Set `enhance: true, enhancer: "gfpgan"` on `/faceswap`          |

### Multi-Step Pipelines

| ComfyUI Pattern                      | LiteImage Equivalent                                                             |
| ------------------------------------ | -------------------------------------------------------------------------------- |
| KSampler → Upscale → Save            | `POST /pipeline {"steps": [{"op":"upscale"}, {"op":"save"}]}`                    |
| KSampler → FaceSwap → Upscale → Save | `POST /pipeline {"steps": [{"op":"faceswap"}, {"op":"upscale"}, {"op":"save"}]}` |
| Multiple prompts with same seed      | `POST /pipeline/batch {"axes": {"prompts": [...], "seeds": [42]}}`               |
| Same prompt across models            | `POST /pipeline/batch {"axes": {"prompts": ["..."], "models": ["/a", "/b"]}}`    |

### Nodes With No LiteImage Equivalent

These ComfyUI nodes have no direct mapping — note them to the user:

| Node                              | Why                                      | Workaround                                           |
| --------------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `IPAdapter` / `IPAdapterAdvanced` | Style/composition transfer not in sd-cli | Use img2img with high strength as approximation      |
| `CLIPVision` (for style)          | IP-Adapter specific                      | Not supported                                        |
| `FreeU` / `PerturbedAttention`    | Attention modification not exposed       | Skip — minimal quality impact                        |
| `ModelMerge*`                     | Runtime model merging                    | Merge models offline, load the merged result         |
| `Flux Guidance` node              | Flux-specific CFG routing                | LiteImage handles this automatically for Flux family |
| Custom Python nodes               | Arbitrary code execution                 | Not possible — file a feature request                |
| `AnimateDiff`                     | Different video architecture             | Use Wan2.1 via `/video/generate` instead             |
| Live portrait / audio-reactive    | Specialized pipelines                    | Use LiteImage's TalkingHead feature (separate)       |

## Translation Process

When given a ComfyUI workflow JSON (API format), follow this process:

### Step 1: Identify the workflow type

Read the nodes and classify:

- **txt2img:** Has `EmptyLatentImage` + `KSampler` + `CLIPTextEncode`, no `LoadImage`
- **img2img:** Has `LoadImage` feeding into `KSampler` (denoise < 1.0)
- **inpaint:** Has `LoadImage` for mask OR `SetLatentNoiseMask`
- **controlnet:** Has `ControlNetLoader` + `ControlNetApply`
- **upscale:** Has `UpscaleModelLoader` + `ImageUpscaleWithModel`
- **video:** Has Wan/LTX video nodes
- **multi-step:** Multiple KSamplers chained, or post-processing after generation

### Step 2: Extract parameters

From the node inputs, extract:

- **Model:** checkpoint filename from `CheckpointLoaderSimple.ckpt_name`
- **LoRAs:** names from `LoraLoader.lora_name` — convert to `<lora:name>` prompt tags
- **Prompt:** text from `CLIPTextEncode` connected to positive conditioning
- **Negative:** text from `CLIPTextEncode` connected to negative conditioning
- **Dimensions:** `EmptyLatentImage.width` and `.height` (multiply by 8 if "latent" dimensions)
- **Steps:** `KSampler.steps`
- **CFG:** `KSampler.cfg` → `guidance_scale`
- **Seed:** `KSampler.seed` (or `noise_seed`)
- **Sampler:** `KSampler.sampler_name` → map using table above
- **Scheduler:** `KSampler.scheduler` → map using table above
- **Denoise:** `KSampler.denoise` → `strength` (for img2img)

### Step 3: Generate API calls

Output a numbered sequence of curl commands. Example for a txt2img + upscale workflow:

```bash
# 1. Load the model
curl -X POST http://127.0.0.1:7426/load_model \
  -H "Content-Type: application/json" \
  -d '{"file_path": "C:/Models/dreamshaper_8.safetensors"}'

# 2. Generate the image
RESULT=$(curl -s -X POST http://127.0.0.1:7426/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a majestic castle on a hill, fantasy art",
    "negative_prompt": "ugly, blurry",
    "width": 512, "height": 768,
    "steps": 25, "guidance_scale": 7.0,
    "seed": 42, "sampling_method": "dpm++2m", "scheduler": "karras"
  }')
IMAGE=$(echo $RESULT | jq -r '.imagePath')

# 3. Upscale the result
curl -X POST http://127.0.0.1:7426/upscale \
  -H "Content-Type: application/json" \
  -d "{\"image_path\": \"$IMAGE\", \"upscale_model\": \"C:/Models/RealESRGAN_x4plus.pth\"}"
```

### Step 4: Handle unknowns

If the workflow contains nodes you can't map:

1. List the unmapped nodes and explain why
2. Suggest the closest LiteImage approximation
3. Note any quality differences the user should expect

## Example Translations

### ComfyUI: Simple txt2img with LoRA

```json
{
  "3": {
    "class_type": "KSampler",
    "inputs": {
      "seed": 42,
      "steps": 20,
      "cfg": 7.5,
      "sampler_name": "euler_ancestral",
      "scheduler": "karras",
      "denoise": 1.0
    }
  },
  "4": {
    "class_type": "CheckpointLoaderSimple",
    "inputs": { "ckpt_name": "dreamshaper_8.safetensors" }
  },
  "5": {
    "class_type": "LoraLoader",
    "inputs": { "lora_name": "add_detail.safetensors", "strength_model": 0.8 }
  },
  "6": { "class_type": "CLIPTextEncode", "inputs": { "text": "a beautiful forest" } },
  "7": {
    "class_type": "EmptyLatentImage",
    "inputs": { "width": 512, "height": 768, "batch_size": 1 }
  }
}
```

**LiteImage translation:**

```bash
curl -X POST http://127.0.0.1:7426/load_model \
  -d '{"file_path": "C:/Models/dreamshaper_8.safetensors"}'

curl -X POST http://127.0.0.1:7426/generate \
  -d '{
    "prompt": "a beautiful forest <lora:add_detail:0.8>",
    "width": 512, "height": 768,
    "steps": 20, "guidance_scale": 7.5,
    "seed": 42, "sampling_method": "euler_a", "scheduler": "karras"
  }'
```

**What changed:** LoRA became a prompt tag `<lora:name:weight>`. VAE/CLIP nodes disappeared (automatic). KSampler params became `/generate` params.

### ComfyUI: Image-to-Video (Wan 2.1)

```json
{
  "1": { "class_type": "LoadImage", "inputs": { "image": "input.png" } },
  "2": { "class_type": "WanVideoModelLoader", "inputs": { "model": "wan2.1-i2v-14b" } },
  "3": { "class_type": "WanVideoSampler", "inputs": { "steps": 25, "cfg": 5.0, "seed": 42 } },
  "4": { "class_type": "VHS_VideoCombine", "inputs": { "fps": 15, "frame_count": 60 } }
}
```

**LiteImage translation:**

```bash
# Video generation is async — returns job_id
JOB=$(curl -s -X POST http://127.0.0.1:7426/video/generate \
  -d '{
    "prompt": "cinematic motion",
    "init_image_path": "C:/images/input.png",
    "steps": 25, "guidance_scale": 5.0, "seed": 42,
    "frames": 60, "fps": 15
  }' | jq -r '.job_id')

# Poll until done
while true; do
  STATUS=$(curl -s http://127.0.0.1:7426/video/status/$JOB)
  [ "$(echo $STATUS | jq -r '.status')" = "complete" ] && break
  sleep 5
done
echo "MP4: $(echo $STATUS | jq -r '.result.mp4Path')"
```

## Model Path Resolution

ComfyUI uses short names like `dreamshaper_8.safetensors`. LiteImage needs full paths. To resolve:

1. Call `GET /models` to list all available models
2. Match the ComfyUI checkpoint name against `displayName` or filename in the path
3. If no match, call `GET /loras` for LoRA names
4. If still no match, tell the user: "Model 'X' not found. Available models: ..." and list them

```bash
# Find the model
MODELS=$(curl -s http://127.0.0.1:7426/models)
# Search for match
echo $MODELS | jq '.models[] | select(.displayName | test("dreamshaper"; "i"))'
```

## ComfyUI Workflow Template Library

The official ComfyUI template library at **`Comfy-Org/workflow_templates`** on GitHub contains 250+ categorized workflow templates. Use this as a reference when the user describes what they want semantically (e.g., "I want to animate this image" or "make it look like a product ad").

### How to search the library

Fetch the index to find matching templates by intent:

```bash
# Fetch the template index (cached for 15 min)
gh api repos/Comfy-Org/workflow_templates/contents/templates/index.json --jq '.content' | base64 -d > /tmp/comfy-templates.json
```

Then search by keyword against template names, titles, descriptions, and tags. For example, if the user says "I want to turn my photo into a video":

- Search for: `image to video`, `i2v`, `animate`
- Matches: `video_wan2_2_14B_i2v`, `image_to_video_wan`, `ltxv_image_to_video`, etc.

### Fetch a specific workflow

```bash
# Download the actual workflow JSON
gh api repos/Comfy-Org/workflow_templates/contents/templates/<name>.json --jq '.content' | base64 -d > /tmp/workflow.json
```

Then parse it using the translation process below to generate LiteImage API calls.

### Template categories

| Category     | Count | Examples                                                                 |
| ------------ | ----- | ------------------------------------------------------------------------ |
| Use Cases    | 76    | Multi-angle character gen, product ads, poster recreation, sprite sheets |
| Image        | 45    | Z-Image, Flux, SDXL, Chroma, Klein, Qwen, ControlNet, inpainting         |
| Video        | 40    | Wan 2.1/2.2 (T2V, I2V, animate, camera control), LTX, HunyuanVideo       |
| Utility      | 20    | Upscale (GAN, AI), background removal, depth maps, pose extraction       |
| 3D           | 8     | Hunyuan3D (image/text/multiview to model)                                |
| Audio        | 8     | Text-to-speech, voice cloning, music generation                          |
| API Partners | 100+  | Kling, Runway, Luma, Veo, Sora, DALL-E (cloud API nodes)                 |

### What's directly translatable to LiteImage

Templates in these categories map cleanly to LiteImage API calls:

- **Image txt2img/img2img** — All Flux, SDXL, SD1.5, SD3, Z-Image, Klein, Chroma templates
- **Video** — Wan 2.1/2.2 templates (T2V, I2V) via `/video/generate`
- **Upscale** — GAN upscaler, utility upscale templates via `/upscale`
- **Inpaint** — Flux Fill, inpaint templates via `/generate` with `mask_path`
- **ControlNet** — Canny, depth, pose templates via `/generate` with `control_net_model`

Templates that use **API partner nodes** (Kling, Runway, DALL-E, etc.) are cloud-only and can't run locally in LiteImage — note this to the user.

## Notes

- LiteImage uses `sd-cli.exe` (stable-diffusion.cpp) — not ComfyUI's Python backend. Some nodes have no equivalent.
- ComfyUI's "API format" JSON (exported via Graph → Export as API) is what this skill reads. The "regular" format with node positions is also parseable but the API format is cleaner.
- LoRA weight syntax: `<lora:name:weight>` in the prompt string. Weight defaults to 1.0 if omitted.
- Video model must be configured separately (it's not the same as the loaded checkpoint). Use `/video/readiness` to check.
- Pipeline steps in LiteImage are fixed types: `upscale`, `faceswap`, `inpaint`, `controlnet`, `save`. For anything else, chain individual API calls.

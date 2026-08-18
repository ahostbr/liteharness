#!/usr/bin/env python3
"""
LMS Switch — Model & Profile Manager for Local Lens

Manages LM Studio model loading, unloading, and preset application.
Designed to be called by Claude Code's Local Lens skill.

Usage:
    python lms_switch.py status                  # Show loaded models + VRAM
    python lms_switch.py load <profile>          # Load a named profile
    python lms_switch.py unload [id|--all]       # Unload model(s)
    python lms_switch.py ensure <model-key>      # Load only if not already loaded
    python lms_switch.py preset <preset-name>    # Create/apply a preset
    python lms_switch.py presets                  # List available presets

Profiles:
    default       — Qwen 0.8B @ 8k (general use)
    litespeak     — Qwen 0.8B @ 4k (minimal dictation cleanup)
    duo           — 0.8B @ 8k + Devstral @ 32k
    reasoning     — 0.8B @ 8k + Opus 9B @ 16k
    maxpower      — Devstral solo @ 65k
    coding        — Devstral solo @ 32k
"""

import subprocess
import json
import sys
import os
import urllib.request
import time

LMS_CLI = os.path.join(os.environ["USERPROFILE"], ".lmstudio", "bin", "lms.exe")
LMS_API = "http://localhost:1234"
PRESETS_DIR = os.path.join(os.environ["USERPROFILE"], ".lmstudio", "config-presets")
NVIDIA_SMI = "nvidia-smi"

# Model registry: key -> (default_context, identifier, approx_vram_gb)
# Context lengths right-sized per model class:
#   0.8B: 8k  (dictation/summarization — input is short sentences)
#   4B:   16k (reasoning/extraction — moderate context)
#   9B:   16k (reasoning — moderate context)
#   24B:  32k (coding/RAG — needs full file context)
#   12B:  16k (general — moderate context)
MODELS = {
    "qwen3.5-0.8b":                                  (8192,  "qwen-0.8b",     1.8),
    "huihui-qwen3.5-0.8b-abliterated":               (8192,  "qwen-0.8b-abl", 1.7),
    "qwen3.5-0.8b-uncensored-opus-distill":           (8192,  "qwen-0.8b-opus", 0.5),
    "gemma-3-4b-it":                                  (8192,  "gemma-4b",      2.4),
    "qwen3.5-4b-claude-4.6-opus-reasoning-distilled": (16384, "opus-4b",       4.5),
    "qwen/qwen3.5-9b":                               (16384, "qwen-9b",      10.5),
    "qwen3.5-9b-claude-4.6-opus-reasoning-distilled": (16384, "opus-9b",       9.5),
    "mistralai/devstral-small-2-2512":                (32768, "devstral",     15.2),
    "gemma-3-12b-it-uncensored":                      (16384, "gemma-12b",    13.4),
    # ── Qwen3.8 27B (Alibaba, Apache 2.0, released 2026-08-14) ──
    # Artificial Analysis: Intelligence 52 (ties GPT-5.6 Luna), Agentic 51 (6th of 29),
    # #1/135 in the open-weights 4B-40B class. Vision-capable. Sizes below INCLUDE the
    # bundled mmproj (measured: 9.01 + 0.93 = 9.94).
    # VRAM at load ~= weights + 0.0696 MiB/token of context + 2818 MiB compute buffer.
    # Context ceilings below are MEASURED RUNNABLE limits on a 32 GB 5090, not load limits:
    # Q3_K_M *loads* at 262k and then runs at 42 tok/s vs IQ2's 1310. Budget to ~90%.
    "qwen/qwen3.8-27b@iq2_xxs":                       (262144, "qwen38-iq2",   9.9),
    "qwen/qwen3.8-27b@q3_k_m":                        (196608, "qwen38-q3",   14.8),
    "qwen/qwen3.8-27b@q4_k_m":                        (153600, "qwen38-q4",   19.4),
}

VRAM_BUDGET = 31.5  # Default: RTX 5090 — adjust to match your GPU's VRAM (run nvidia-smi to check)

# Load profiles: list of (model_key, context_override_or_None) tuples
# None = use default from MODELS registry
PROFILES = {
    "default":   [("qwen3.5-0.8b", None)],
    "litespeak": [("qwen3.5-0.8b", 4096)],  # minimal context for dictation cleanup
    "duo":       [("qwen3.5-0.8b", None), ("mistralai/devstral-small-2-2512", None)],
    "reasoning": [("qwen3.5-0.8b", None), ("qwen3.5-9b-claude-4.6-opus-reasoning-distilled", None)],
    "maxpower":  [("mistralai/devstral-small-2-2512", 65536)],
    "coding":    [("mistralai/devstral-small-2-2512", 32768)],

    # ── Qwen3.8 27B context ladder (Ryan, 2026-08-17) ────────────────────────
    # One model, three quants, trading precision for context as a conversation grows.
    # Swap AT a compaction boundary, never mid-turn: a reload destroys the KV cache and
    # the whole context must be re-prefilled through the new quant.
    #
    #   rung-a  ->  short context, most precision      (start here, and after every compact)
    #   rung-b  ->  mid context
    #   rung-c  ->  max context, and the rung that PERFORMS the compaction
    #
    # Compaction is a summarisation task: it needs capacity to READ 260k, not precision to
    # WRITE a summary — which is why the 2-bit rung is the right one to compact on. After
    # the compact, drop straight back to rung-a.
    #
    # 🔴 UNPROVEN PREMISE: the ladder only pays if rung-a is measurably better than rung-c
    # at short context. The 2026-08-17 five-arm eval found QUALITY FLAT across quants and
    # its cost conclusions were retracted (Reasoning Effort confound + a self-inflicted
    # answer leak). If the rerun confirms flat quality, this ladder collapses to rung-c
    # alone and should be deleted rather than kept. Do not treat these rungs as validated.
    "qwen38-rung-a": [("qwen/qwen3.8-27b@q4_k_m", 150000)],
    "qwen38-rung-b": [("qwen/qwen3.8-27b@q3_k_m", 196608)],
    "qwen38-rung-c": [("qwen/qwen3.8-27b@iq2_xxs", 262144)],
    # Scout rig — what Ryan actually had loaded 2026-08-17: small context, 4 parallel
    # slots, for fan-out lookups rather than one long conversation. NOTE: `parallel` is
    # not expressible in this tuple format; set it in LM Studio or via `lms load --parallel 4`.
    "qwen38-scout":  [("qwen/qwen3.8-27b@iq2_xxs", 50176)],
}

# Preset templates for different roles
ROLE_PRESETS = {
    # ── Empirically optimized from optimize_08b.py test battery ──
    "summarizer": {
        "identifier": "@local:lens-summarizer",
        "name": "Lens Summarizer",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.05},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 400}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "Summarize in bullet points. Include: who, what, numbers, dates, decisions. "
                    "No intro sentence. Start with bullets immediately. Be exact."
                )},
                {"key": "llm.prediction.stopStrings", "value": []},
            ]
        },
        "load": {"fields": []},
    },
    "extractor": {
        "identifier": "@local:lens-extractor",
        "name": "Lens Extractor",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.05},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 300}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a data extraction tool. Output requested fields as key: value pairs. "
                    "Nothing else. If missing, output: MISSING."
                )},
                {"key": "llm.prediction.stopStrings", "value": []},
            ]
        },
        "load": {"fields": []},
    },
    "extractor-json": {
        "identifier": "@local:lens-extractor-json",
        "name": "Lens Extractor JSON",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.05},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 500}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a JSON data extraction tool. Output ONLY valid JSON with the requested fields. "
                    "If a field is not present in the source, set its value to null. "
                    "Do not output anything except the JSON object."
                )},
                {"key": "llm.prediction.stopStrings", "value": []},
                {"key": "llm.prediction.structured", "value": {"type": "json"}},
            ]
        },
        "load": {"fields": []},
    },
    "rag-strict": {
        "identifier": "@local:lens-rag-strict",
        "name": "Lens RAG Strict",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.05},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 500}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a precise analyst. Answer ONLY from the provided content. "
                    "If something is not in the content, say so explicitly - do not guess. "
                    "Answer directly. Do not use any tools. Do not attempt tool calls. "
                    "Quote relevant passages when possible."
                )},
                {"key": "llm.prediction.stopStrings", "value": ["[TOOL_CALLS]"]},
            ]
        },
        "load": {"fields": []},
    },
    "tts": {
        "identifier": "@local:lens-tts",
        "name": "Lens TTS",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.3},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 150}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a friendly, conversational assistant. Respond naturally as if speaking aloud. "
                    "No markdown formatting, no bullet points, no headers. Use plain sentences. "
                    "Keep responses concise - 2-3 sentences unless asked for more. "
                    "Use contractions and casual language."
                )},
                {"key": "llm.prediction.stopStrings", "value": ["**", "##", "- ", "* "]},
            ]
        },
        "load": {"fields": []},
    },
    "coder": {
        "identifier": "@local:lens-coder",
        "name": "Lens Coder",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.1},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 800}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a precise code analyst. Answer directly with code when appropriate. "
                    "Do not use any tools. Do not attempt tool calls. "
                    "Be concise. Explain only when asked. Prefer working code over commentary."
                )},
                {"key": "llm.prediction.stopStrings", "value": ["[TOOL_CALLS]"]},
            ]
        },
        "load": {"fields": []},
    },
    "classifier": {
        "identifier": "@local:lens-classifier",
        "name": "Lens Classifier",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.01},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.5}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.0}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 50}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a classifier. Output ONLY the category label, nothing else. "
                    "No explanation, no reasoning, no punctuation. Just the label."
                )},
                {"key": "llm.prediction.stopStrings", "value": ["\n\n"]},
            ]
        },
        "load": {"fields": []},
    },
    "rewriter": {
        "identifier": "@local:lens-rewriter",
        "name": "Lens Rewriter",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.4},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.92}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.15}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 500}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "Rewrite the given text to be clearer and more concise. "
                    "Preserve all facts and meaning. Fix grammar and awkward phrasing. "
                    "Output only the rewritten text, nothing else."
                )},
                {"key": "llm.prediction.stopStrings", "value": []},
            ]
        },
        "load": {"fields": []},
    },
    "translator": {
        "identifier": "@local:lens-translator",
        "name": "Lens Translator",
        "changed": True,
        "operation": {
            "fields": [
                {"key": "llm.prediction.temperature", "value": 0.15},
                {"key": "llm.prediction.topPSampling", "value": {"checked": True, "value": 0.9}},
                {"key": "llm.prediction.repeatPenalty", "value": {"checked": True, "value": 1.1}},
                {"key": "llm.prediction.maxPredictedTokens", "value": {"checked": True, "value": 800}},
                {"key": "llm.prediction.systemPrompt", "value": (
                    "You are a translator. Translate the given text to the requested language. "
                    "Output ONLY the translation, nothing else. Preserve formatting."
                )},
                {"key": "llm.prediction.stopStrings", "value": []},
            ]
        },
        "load": {"fields": []},
    },
}


def get_gpu_vram():
    """Get real GPU VRAM usage from nvidia-smi. Returns (total_mb, used_mb, free_mb)."""
    try:
        result = subprocess.run(
            [NVIDIA_SMI, "--query-gpu=memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            return int(parts[0].strip()), int(parts[1].strip()), int(parts[2].strip())
    except Exception:
        pass
    return 0, 0, 0


def get_system_ram():
    """Get system RAM usage. Returns (total_gb, used_gb, available_gb, percent)."""
    try:
        import psutil
        m = psutil.virtual_memory()
        return m.total / 1024**3, m.used / 1024**3, m.available / 1024**3, m.percent
    except ImportError:
        pass
    # Fallback: wmic
    try:
        result = subprocess.run(
            ["wmic", "OS", "get", "TotalVisibleMemorySize,FreePhysicalMemory",
             "/format:csv"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            line = [l for l in result.stdout.strip().splitlines() if l.strip() and "," in l][-1]
            parts = line.split(",")
            free_kb = int(parts[1].strip())
            total_kb = int(parts[2].strip())
            used_kb = total_kb - free_kb
            return total_kb/1024**2, used_kb/1024**2, free_kb/1024**2, used_kb/total_kb*100
    except Exception:
        pass
    return 0, 0, 0, 0


def run_lms(*args):
    """Run an lms CLI command and return stdout."""
    result = subprocess.run(
        [LMS_CLI, *args],
        capture_output=True, timeout=120, encoding="utf-8", errors="replace"
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    return stdout, stderr, result.returncode


def api_get(path):
    """GET from LM Studio API."""
    try:
        req = urllib.request.Request(f"{LMS_API}{path}")
        resp = urllib.request.urlopen(req, timeout=5)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_loaded_models():
    """Get detailed info about loaded models from v0 API."""
    data = api_get("/api/v0/models")
    if "error" in data:
        return []
    return [m for m in data.get("data", []) if m.get("state") == "loaded"]


def get_loaded_vram():
    """Estimate total VRAM used by loaded models."""
    loaded = get_loaded_models()
    total = 0.0
    for m in loaded:
        model_key = m["id"]
        if model_key in MODELS:
            total += MODELS[model_key][2]
        else:
            total += 2.0  # conservative estimate for unknown models
    return total, loaded


def cmd_status():
    """Show current model status with real GPU VRAM and system RAM."""
    total_mb, used_mb, free_mb = get_gpu_vram()
    ram_total, ram_used, ram_avail, ram_pct = get_system_ram()
    loaded = get_loaded_models()

    # GPU VRAM
    if total_mb > 0:
        total_gb = total_mb / 1024
        used_gb = used_mb / 1024
        free_gb = free_mb / 1024
        pct = used_mb / total_mb * 100
        print(f"GPU VRAM: {used_gb:.1f} GB used / {total_gb:.1f} GB total ({pct:.0f}%) -- {free_gb:.1f} GB free")
    else:
        vram_est, _ = get_loaded_vram()
        print(f"GPU VRAM: nvidia-smi unavailable (estimated ~{vram_est:.1f} GB from model sizes)")

    # System RAM
    if ram_total > 0:
        print(f"Sys  RAM: {ram_used:.1f} GB used / {ram_total:.1f} GB total ({ram_pct:.0f}%) -- {ram_avail:.1f} GB free")

    print()
    if not loaded:
        print("No models loaded.")
        return
    print(f"{'ID':<45} {'Quant':<8} {'Context':<10} {'State'}")
    print("-" * 80)
    for m in loaded:
        ctx = m.get("loaded_context_length", m.get("max_context_length", "?"))
        print(f"{m['id']:<45} {m.get('quantization', '?'):<8} {ctx:<10} {m['state']}")


def cmd_load_profile(profile_name):
    """Load a named profile, safely unloading first."""
    if profile_name not in PROFILES:
        print(f"Unknown profile: {profile_name}")
        print(f"Available: {', '.join(PROFILES.keys())}")
        sys.exit(1)

    entries = PROFILES[profile_name]

    # Calculate total VRAM needed
    total_needed = sum(MODELS[k][2] for k, _ in entries if k in MODELS)
    print(f"Profile '{profile_name}': {len(entries)} model(s), ~{total_needed:.1f} GB VRAM")

    if total_needed > VRAM_BUDGET:
        print(f"WARNING: Profile needs {total_needed:.1f} GB but budget is {VRAM_BUDGET} GB!")
        sys.exit(1)

    # Check what's already loaded
    loaded = get_loaded_models()
    loaded_ids = {m["id"] for m in loaded}
    needed_ids = {k for k, _ in entries}

    # If everything we need is already loaded, skip
    if needed_ids.issubset(loaded_ids):
        print("All models already loaded. Nothing to do.")
        return

    # Unload everything first for safety
    if loaded:
        print("Unloading all models first...")
        stdout, stderr, rc = run_lms("unload", "--all")
        if rc != 0:
            print(f"Unload failed: {stderr}")
            sys.exit(1)
        time.sleep(1)

    # Load each model
    for model_key, ctx_override in entries:
        if model_key not in MODELS:
            print(f"Unknown model: {model_key}")
            continue

        default_ctx, identifier, vram = MODELS[model_key]
        ctx = ctx_override if ctx_override is not None else default_ctx

        print(f"Loading {model_key} (id={identifier}, ctx={ctx}, ~{vram:.1f}GB)...")
        stdout, stderr, rc = run_lms(
            "load", model_key, "-y",
            "-c", str(ctx),
            "--gpu", "max",
            "--identifier", identifier
        )
        if rc != 0:
            print(f"Load failed: {stderr}")
            sys.exit(1)
        print(f"  Loaded: {identifier}")

    print(f"\nProfile '{profile_name}' active.")
    cmd_status()


def cmd_ensure(model_key):
    """Load a model only if it's not already loaded. Safely manages VRAM."""
    loaded = get_loaded_models()
    loaded_ids = {m["id"] for m in loaded}

    if model_key in loaded_ids:
        print(f"Already loaded: {model_key}")
        return

    if model_key not in MODELS:
        print(f"Unknown model: {model_key}")
        print(f"Available: {', '.join(MODELS.keys())}")
        sys.exit(1)

    ctx, identifier, vram_needed = MODELS[model_key]

    # Use real GPU VRAM if available, fall back to estimates
    total_mb, used_mb, free_mb = get_gpu_vram()
    if total_mb > 0:
        free_gb = free_mb / 1024
        vram_needed_mb = int(vram_needed * 1024)
        fits = free_mb > vram_needed_mb + 1024  # 1GB safety margin
    else:
        current_vram, _ = get_loaded_vram()
        fits = current_vram + vram_needed <= VRAM_BUDGET

    if not fits:
        if total_mb > 0:
            print(f"Won't fit: ~{vram_needed:.1f} GB needed, {free_gb:.1f} GB free")
        else:
            print(f"Won't fit: {vram_needed:.1f} GB needed (estimated)")
        print("Unloading all models first...")
        run_lms("unload", "--all")
        time.sleep(1)

    print(f"Loading {model_key} (id={identifier}, ctx={ctx}, ~{vram_needed:.1f}GB)...")
    stdout, stderr, rc = run_lms(
        "load", model_key, "-y",
        "-c", str(ctx),
        "--gpu", "max",
        "--identifier", identifier
    )
    if rc != 0:
        print(f"Load failed: {stderr}")
        sys.exit(1)
    print(f"Loaded: {identifier}")


def cmd_unload(target):
    """Unload a specific model or all models."""
    if target == "--all":
        stdout, stderr, rc = run_lms("unload", "--all")
    else:
        stdout, stderr, rc = run_lms("unload", target)

    if rc != 0:
        print(f"Unload failed: {stderr}")
        sys.exit(1)
    print(f"Unloaded: {target}")


def cmd_preset(preset_name):
    """Create/overwrite a role preset in LM Studio's config-presets directory."""
    if preset_name not in ROLE_PRESETS:
        print(f"Unknown preset: {preset_name}")
        print(f"Available: {', '.join(ROLE_PRESETS.keys())}")
        sys.exit(1)

    preset = ROLE_PRESETS[preset_name]
    filename = f"Lens {preset_name}.preset.json"
    filepath = os.path.join(PRESETS_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(preset, f, indent=2, ensure_ascii=False)

    print(f"Preset written: {filepath}")
    print(f"  Name: {preset['name']}")
    sys_field = next((f for f in preset["operation"]["fields"] if f["key"] == "llm.prediction.systemPrompt"), None)
    if sys_field:
        print(f"  System prompt: {sys_field['value'][:80]}...")
    print(f"\nApply in LM Studio UI or use via API system prompt.")


def cmd_presets():
    """List available role presets."""
    print("Built-in role presets:")
    print(f"{'Name':<15} {'Description'}")
    print("-" * 60)
    descriptions = {
        "summarizer": "temp=0.05 | Concise bullet summaries, no intro, exact facts",
        "extractor": "temp=0.05 | Key:value extraction, says MISSING if absent",
        "extractor-json": "temp=0.05 | Pure JSON output, null for missing fields",
        "rag-strict": "temp=0.05 | RAG discipline, blocks tool calls, quotes sources",
        "tts": "temp=0.3  | Natural speech, no markdown, stop on formatting chars",
        "coder": "temp=0.1  | Code analysis, blocks tool calls, concise",
        "classifier": "temp=0.01 | Single-label output, ultra-deterministic",
        "rewriter": "temp=0.4  | Clarity rewrites, preserves facts, higher creativity",
        "translator": "temp=0.15 | Translation only, preserves formatting",
    }
    for name in ROLE_PRESETS:
        print(f"  {name:<15} {descriptions.get(name, '')}")

    print(f"\nInstalled presets in {PRESETS_DIR}:")
    for f in sorted(os.listdir(PRESETS_DIR)):
        if f.endswith(".preset.json"):
            print(f"  {f}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        cmd_status()
    elif cmd == "load":
        if len(sys.argv) < 3:
            print("Usage: lms_switch.py load <profile>")
            print(f"Profiles: {', '.join(PROFILES.keys())}")
            sys.exit(1)
        cmd_load_profile(sys.argv[2])
    elif cmd == "ensure":
        if len(sys.argv) < 3:
            print("Usage: lms_switch.py ensure <model-key>")
            sys.exit(1)
        cmd_ensure(sys.argv[2])
    elif cmd == "unload":
        target = sys.argv[2] if len(sys.argv) > 2 else "--all"
        cmd_unload(target)
    elif cmd == "preset":
        if len(sys.argv) < 3:
            print("Usage: lms_switch.py preset <preset-name>")
            cmd_presets()
            sys.exit(1)
        cmd_preset(sys.argv[2])
    elif cmd == "presets":
        cmd_presets()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

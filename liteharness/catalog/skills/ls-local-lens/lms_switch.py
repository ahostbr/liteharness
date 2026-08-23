#!/usr/bin/env python3
"""
LMS Switch — LM Studio model manager for Local Lens.

🔴 THIS SCRIPT SHIPS NO MODEL LINEUP. It asks LM Studio what exists, every run.

Ruling, Ryan 2026-08-23: "just ask me what model and context i want at the time
... dont hardcode models."

The version this replaces carried a `MODELS` registry (keys, default contexts and
hand-estimated VRAM) written when one 24B coding model was the strongest thing on
the box. Months later that was false, but the table still read as current fact —
because a constant in source carries no expiry date. An agent following it was
about to unload a user's live, in-use seat to load something worse.

So the discovery path here is modelled on LiteTUI's, which had already solved it:
read `/api/v0/models` and believe the server, not a constant.

Two distinctions LiteTUI learned the hard way and this script keeps:

  A CEILING IS NOT A WINDOW.  `max_context_length` is what the model SUPPORTS.
  `loaded_context_length` is the window it is ACTUALLY serving. Reporting the
  ceiling as if it were the window is how you plan a 262,144-token request
  against a seat that is really serving 8,192.

  `lms ps` SIZE IS WEIGHTS, NOT RESIDENCY.  True footprint is weights + KV cache,
  which scales with context x parallel slots. A 27B reporting SIZE 17.74 GB was
  really holding ~29 GB of 31.5 at 120k x 4. Headroom comes from nvidia-smi.

Usage:
    python lms_switch.py models                  # what LM Studio actually has
    python lms_switch.py status                  # loaded seats + real VRAM
    python lms_switch.py ensure <key> [-c N]     # load only if not already loaded
    python lms_switch.py load <key|profile> [-c N]
    python lms_switch.py unload <identifier|--all>
    python lms_switch.py presets                 # list role presets
    python lms_switch.py preset <name>           # write a role preset

Profiles are USER DATA in lms_profiles.json, not built-ins. No file = no
profiles, which is a fully working state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LMS_CLI = os.path.join(os.environ.get("USERPROFILE", ""), ".lmstudio", "bin", "lms.exe")
LMS_HOST = os.environ.get("LMS_HOST", "http://localhost:1234").rstrip("/")
PRESETS_DIR = os.path.join(os.environ.get("USERPROFILE", ""), ".lmstudio", "config-presets")
PROFILES_PATH = os.path.join(HERE, "lms_profiles.json")

try:
    from lens_presets import ROLE_PRESETS
except ImportError:  # running from elsewhere
    sys.path.insert(0, HERE)
    from lens_presets import ROLE_PRESETS


# ── discovery ───────────────────────────────────────────────────────────────


class Unreachable(RuntimeError):
    """LM Studio is not answering. Distinct from 'answered, has no models'."""


def api_models() -> list[dict]:
    """Every model LM Studio knows about. Raises Unreachable if it is not up.

    Tolerant of the three response shapes seen in the wild (bare list, {data},
    {models}) — the same defensive parse LiteTUI uses.

    An EMPTY list is a real answer meaning "nothing installed"; it must never be
    conflated with "could not ask", which is why that case raises instead.
    """
    url = f"{LMS_HOST}/api/v0/models"
    req = urllib.request.Request(url, headers={"User-Agent": "lms_switch"})
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.load(r)
    except Exception as e:
        raise Unreachable(f"could not reach LM Studio at {LMS_HOST} — {e}") from e
    if isinstance(data, list):
        return data
    return data.get("data") or data.get("models") or []


def describe(m: dict) -> dict:
    """Normalise one API row. Keeps ceiling and window SEPARATE, on purpose."""
    window = m.get("loaded_context_length")
    return {
        "id": m.get("id") or "",
        "loaded": bool(window),
        "window": int(window) if window else None,
        "ceiling": int(m.get("max_context_length") or 0) or None,
        "type": m.get("type") or "?",
        "quant": m.get("quantization") or "",
    }


def catalogue() -> list[dict]:
    return [d for d in (describe(m) for m in api_models()) if d["id"]]


def resolve(key: str, rows: list[dict]) -> dict | None:
    """Exact match, else a unique case-insensitive substring match.

    Deliberately refuses an AMBIGUOUS partial rather than picking one: loading
    the wrong multi-GB model is expensive and silent.
    """
    for r in rows:
        if r["id"] == key:
            return r
    near = [r for r in rows if key.lower() in r["id"].lower()]
    return near[0] if len(near) == 1 else None


def suggest(key: str, rows: list[dict]) -> None:
    print(f"  ! no model matching {key!r} is installed in LM Studio.")
    near = [r["id"] for r in rows if key.split("@")[0].lower()[:8] in r["id"].lower()]
    if near:
        print("    did you mean:")
        for n in near[:8]:
            print(f"      {n}")
    else:
        print(f"    run `{os.path.basename(__file__)} models` to see what is installed.")


# ── hardware ────────────────────────────────────────────────────────────────


def gpu_vram() -> tuple[float, float] | None:
    """(used_gb, total_gb) from nvidia-smi — the only trustworthy headroom."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return None
        used, total = out.stdout.strip().splitlines()[0].split(",")
        return int(used) / 1024, int(total) / 1024
    except Exception:
        return None


def run_lms(*args: str) -> int:
    if not os.path.exists(LMS_CLI):
        print(f"  ! lms CLI not found at {LMS_CLI}")
        return 1
    return subprocess.run([LMS_CLI, *args]).returncode


# ── profiles (user data) ────────────────────────────────────────────────────


def load_profiles() -> dict:
    if not os.path.exists(PROFILES_PATH):
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("profiles") or {}
    except Exception as e:
        print(f"  ! {os.path.basename(PROFILES_PATH)} unreadable — ignoring it ({e})")
        return {}


# ── commands ────────────────────────────────────────────────────────────────


def cmd_models(args) -> int:
    rows = catalogue()
    if not rows:
        print("LM Studio is up but reports NO models installed.")
        return 0
    rows.sort(key=lambda r: (not r["loaded"], r["id"]))
    print(f"{'':2}{'MODEL':<52}{'TYPE':<6}{'WINDOW':>10}{'CEILING':>10}  QUANT")
    print("-" * 92)
    for r in rows:
        mark = "*" if r["loaded"] else " "
        win = f"{r['window']:,}" if r["window"] else "-"
        ceil_ = f"{r['ceiling']:,}" if r["ceiling"] else "?"
        print(f"{mark} {r['id']:<52}{r['type']:<6}{win:>10}{ceil_:>10}  {r['quant']}")
    print("\n* = loaded now.  WINDOW = what it is actually serving; "
          "CEILING = what it supports.\n  They are different numbers and only "
          "WINDOW constrains your request.")
    return 0


def cmd_status(args) -> int:
    try:
        rows = catalogue()
    except Unreachable as e:
        print(f"  ! {e}")
        return 2
    live = [r for r in rows if r["loaded"]]
    if live:
        print("LOADED:")
        for r in live:
            print(f"  {r['id']}  window={r['window']:,}  type={r['type']}  {r['quant']}")
    else:
        print("LOADED: nothing.")
    print(f"\nINSTALLED: {len(rows)} model(s). `models` to list.")

    v = gpu_vram()
    if v:
        used, total = v
        print(f"\nVRAM: {used:.1f} / {total:.1f} GB used  ({total - used:.1f} GB free)")
        print("  NOTE: `lms ps` SIZE is WEIGHTS ONLY. True residency includes the KV")
        print("        cache (context x parallel). Trust this line, not that one.")
    else:
        print("\nVRAM: nvidia-smi unavailable — headroom UNKNOWN, do not assume it fits.")
    return 0


def _load_one(key: str, context: int | None, rows: list[dict]) -> int:
    hit = resolve(key, rows)
    if not hit:
        suggest(key, rows)
        return 1
    argv = ["load", hit["id"], "-y", "--gpu", "max"]
    if context:
        argv += ["-c", str(context)]
    print(f"  loading {hit['id']}" + (f" @ {context:,}" if context else " (LM Studio default context)"))
    return run_lms(*argv)


def cmd_load(args) -> int:
    try:
        rows = catalogue()
    except Unreachable as e:
        print(f"  ! {e}")
        return 2

    profiles = load_profiles()
    if args.target in profiles:
        entries = profiles[args.target]
        print(f"profile {args.target!r}: {len(entries)} model(s) — from "
              f"{os.path.basename(PROFILES_PATH)}, user data, not a recommendation.")
        rc = 0
        for e in entries:
            rc |= _load_one(e["model"], args.context or e.get("context"), rows)
        return rc

    return _load_one(args.target, args.context, rows)


def cmd_ensure(args) -> int:
    try:
        rows = catalogue()
    except Unreachable as e:
        print(f"  ! {e}")
        return 2
    hit = resolve(args.key, rows)
    if not hit:
        suggest(args.key, rows)
        return 1
    if hit["loaded"]:
        print(f"  already loaded: {hit['id']} @ window {hit['window']:,} — leaving it alone.")
        return 0
    return _load_one(args.key, args.context, rows)


def cmd_unload(args) -> int:
    if args.target == "--all":
        return run_lms("unload", "--all")
    return run_lms("unload", args.target)


def cmd_presets(args) -> int:
    print("Role presets (model-agnostic — sampling + system prompt only):")
    for name in sorted(ROLE_PRESETS):
        p = ROLE_PRESETS[name]
        temp = next(
            (f["value"] for f in p["operation"]["fields"]
             if f["key"] == "llm.prediction.temperature"), "?",
        )
        print(f"  {name:<16} temp={temp}")
    if os.path.isdir(PRESETS_DIR):
        installed = [f for f in sorted(os.listdir(PRESETS_DIR)) if f.endswith(".preset.json")]
        print(f"\nInstalled in {PRESETS_DIR}:")
        for f in installed or ["  (none)"]:
            print(f"  {f}")
    return 0


def cmd_preset(args) -> int:
    if args.name not in ROLE_PRESETS:
        print(f"Unknown preset: {args.name}")
        print(f"Available: {', '.join(sorted(ROLE_PRESETS))}")
        return 1
    preset = ROLE_PRESETS[args.name]
    os.makedirs(PRESETS_DIR, exist_ok=True)
    path = os.path.join(PRESETS_DIR, f"Lens {args.name}.preset.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(preset, f, indent=2, ensure_ascii=False)
    print(f"Preset written: {path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="LM Studio model manager. Discovers models at runtime; "
                    "hardcodes none.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("models", help="list every model LM Studio has").set_defaults(fn=cmd_models)
    sub.add_parser("status", help="loaded seats + real VRAM").set_defaults(fn=cmd_status)

    p = sub.add_parser("load", help="load a model key or a user profile")
    p.add_argument("target")
    p.add_argument("-c", "--context", type=int, default=None)
    p.set_defaults(fn=cmd_load)

    p = sub.add_parser("ensure", help="load only if not already loaded")
    p.add_argument("key")
    p.add_argument("-c", "--context", type=int, default=None)
    p.set_defaults(fn=cmd_ensure)

    p = sub.add_parser("unload", help="unload an identifier, or --all")
    p.add_argument("target")
    p.set_defaults(fn=cmd_unload)

    sub.add_parser("presets", help="list role presets").set_defaults(fn=cmd_presets)
    p = sub.add_parser("preset", help="write a role preset")
    p.add_argument("name")
    p.set_defaults(fn=cmd_preset)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except Unreachable as e:
        print(f"  ! {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())

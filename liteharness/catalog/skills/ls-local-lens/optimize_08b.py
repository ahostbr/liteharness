#!/usr/bin/env python3
"""
Optimize 0.8B — Test battery for finding optimal settings per role.

Runs the same prompts at different temperatures, system prompts, and
sampling configs. Outputs a comparison table for each role.
"""

import json
import urllib.request
import time
import sys
import io

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

API = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen-0.8b"

# ── Test content for summarization ──────────────────────────────────────────
LONG_TEXT = """
SpaceX successfully launched its Starship rocket on its seventh test flight on March 4, 2026,
achieving several new milestones. The Super Heavy booster performed a controlled descent and
was caught by the mechanical arms at the launch tower for the third consecutive time. Starship's
upper stage reached orbit and completed two full orbits before a controlled deorbit burn over
the Indian Ocean. CEO Elon Musk announced that the next flight will attempt to carry a
10-ton payload simulator. The FAA granted a modified launch license allowing up to 12 flights
per year from Boca Chica. SpaceX competitor Blue Origin responded by announcing their New Glenn
rocket achieved orbit on its second attempt last week. NASA Administrator Bill Nelson confirmed
that Starship remains on track as the Human Landing System for Artemis III, now scheduled for
late 2027. The total development cost of Starship has exceeded $5 billion according to analyst
estimates, though SpaceX claims the per-launch cost will eventually drop below $10 million.
"""

# ── Test content for extraction ─────────────────────────────────────────────
EXTRACT_TEXT = """
Meeting Notes - Q1 2026 Budget Review
Date: March 3, 2026
Attendees: Sarah Chen (CFO), Mike Torres (VP Engineering), Lisa Park (VP Sales), James Wright (CEO)

Key Decisions:
- Engineering budget increased by 15% to $4.2M for Q2
- Sales team headcount approved: 8 new hires in APAC region
- Marketing budget reduced by 10% ($180K savings)
- Server infrastructure migration to AWS deadline: June 30, 2026
- Annual company retreat moved from September to October
- New product launch "Project Aurora" greenlit with $1.5M budget

Action Items:
- Sarah to finalize revised P&L by March 10
- Mike to submit cloud migration timeline by March 7
- Lisa to present APAC hiring plan by March 14
- James to announce Project Aurora at all-hands on March 15

Revenue: Q1 actual $12.3M vs $11.8M forecast (4.2% above target)
Burn rate: $2.1M/month (down from $2.4M in Q4 2025)
Runway: 18 months at current burn rate
"""

# ── Test prompts for TTS ────────────────────────────────────────────────────
TTS_PROMPTS = [
    "What's the weather like in Seattle in March?",
    "Tell me something interesting about octopuses.",
    "How do I make a good cup of coffee?",
]

# ── System prompt variants ──────────────────────────────────────────────────
SYSTEM_PROMPTS = {
    "summarizer_v1": "You are a precision summarizer. Extract ALL key facts, names, numbers, decisions, and actionable items. Be thorough but concise. Use bullet points. Never fabricate information not in the source text.",
    "summarizer_v2": "Summarize in bullet points. Include: who, what, numbers, dates, decisions. No intro sentence. Start with bullets immediately. Be exact.",
    "summarizer_v3": "Output a structured summary. Format:\n## Key Facts\n- fact\n## Numbers\n- number\n## Decisions\n- decision\nNo other text.",
    "extractor_v1": "Extract ONLY the information requested. Be precise. If the information is not present, say NOT FOUND. Do not guess.",
    "extractor_v2": "You are a data extraction tool. Output requested fields as key: value pairs. Nothing else. If missing, output: MISSING.",
    "tts_v1": "You are a friendly, conversational assistant. Respond naturally as if speaking aloud. No markdown formatting, no bullet points, no headers. Use plain sentences. Keep responses concise — 2-3 sentences unless asked for more.",
    "tts_v2": "Speak naturally and casually. No formatting. No lists. Short answers. Like talking to a friend.",
    "tts_v3": "You are a warm, helpful voice assistant. Answer in natural spoken English. Never use markdown, asterisks, bullet points, or numbered lists. Keep it brief and conversational.",
}

# ── Temperature / sampling configs to test ──────────────────────────────────
CONFIGS = {
    "cold":    {"temperature": 0.05, "top_p": 0.9,  "repeat_penalty": 1.1},
    "cool":    {"temperature": 0.1,  "top_p": 0.9,  "repeat_penalty": 1.1},
    "warm":    {"temperature": 0.3,  "top_p": 0.9,  "repeat_penalty": 1.1},
    "hot":     {"temperature": 0.6,  "top_p": 0.95, "repeat_penalty": 1.15},
    "creative":{"temperature": 0.8,  "top_p": 0.95, "repeat_penalty": 1.2},
}


def query(system_prompt, user_prompt, config, max_tokens=500):
    """Send a query to the 0.8B model and return (response_text, tokens_used, time_ms)."""
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": config["temperature"],
        "top_p": config["top_p"],
        "repeat_penalty": config["repeat_penalty"],
        "stream": False,
    }).encode()

    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
        elapsed = (time.perf_counter() - start) * 1000

        text = resp["choices"][0]["message"]["content"]
        # Strip thinking blocks if present
        if "<think>" in text:
            import re
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        usage = resp.get("usage", {})
        tokens = usage.get("completion_tokens", len(text.split()))
        return text, tokens, elapsed
    except Exception as e:
        return f"ERROR: {e}", 0, 0


def print_divider(char="=", width=80):
    print(char * width)


def test_summarizer():
    """Test summarization across system prompts and temperatures."""
    print("\n" + "=" * 80)
    print("ROLE: SUMMARIZER")
    print("=" * 80)

    prompt_variants = ["summarizer_v1", "summarizer_v2", "summarizer_v3"]
    temp_variants = ["cold", "cool", "warm"]

    for sp_name in prompt_variants:
        for cfg_name in temp_variants:
            cfg = CONFIGS[cfg_name]
            sp = SYSTEM_PROMPTS[sp_name]

            text, tokens, ms = query(sp, f"Summarize:\n\n{LONG_TEXT}", cfg, max_tokens=400)

            print(f"\n--- {sp_name} + {cfg_name} (temp={cfg['temperature']}) [{ms:.0f}ms, {tokens}tok] ---")
            print(text[:600])
            if len(text) > 600:
                print(f"  ... ({len(text)} chars total)")
            print()


def test_extractor():
    """Test extraction accuracy across configs."""
    print("\n" + "=" * 80)
    print("ROLE: EXTRACTOR")
    print("=" * 80)

    questions = [
        "Extract all dollar amounts and what they refer to.",
        "List all people mentioned with their titles.",
        "What are the deadlines and their dates?",
        "What is the company's burn rate and runway?",
    ]

    prompt_variants = ["extractor_v1", "extractor_v2"]
    temp_variants = ["cold", "cool"]

    for sp_name in prompt_variants:
        for cfg_name in temp_variants:
            cfg = CONFIGS[cfg_name]
            sp = SYSTEM_PROMPTS[sp_name]

            print(f"\n--- {sp_name} + {cfg_name} (temp={cfg['temperature']}) ---")
            for q in questions:
                text, tokens, ms = query(sp, f"{q}\n\nContent:\n{EXTRACT_TEXT}", cfg, max_tokens=300)
                print(f"\n  Q: {q}")
                print(f"  [{ms:.0f}ms, {tokens}tok]")
                for line in text.strip().split("\n")[:8]:
                    print(f"    {line}")
                if text.count("\n") > 8:
                    print(f"    ... ({text.count(chr(10))+1} lines total)")


def test_tts():
    """Test TTS-friendly output across configs."""
    print("\n" + "=" * 80)
    print("ROLE: TTS COMPANION")
    print("=" * 80)

    prompt_variants = ["tts_v1", "tts_v2", "tts_v3"]
    temp_variants = ["warm", "hot", "creative"]

    for sp_name in prompt_variants:
        for cfg_name in temp_variants:
            cfg = CONFIGS[cfg_name]
            sp = SYSTEM_PROMPTS[sp_name]

            print(f"\n--- {sp_name} + {cfg_name} (temp={cfg['temperature']}) ---")
            for prompt in TTS_PROMPTS:
                text, tokens, ms = query(sp, prompt, cfg, max_tokens=150)
                # Check for markdown artifacts
                has_markdown = any(c in text for c in ["**", "##", "- ", "* ", "1.", "`"])
                flag = " [MARKDOWN!]" if has_markdown else ""
                print(f"  Q: {prompt}")
                print(f"  A: {text.strip()[:200]}{flag}")
                print(f"     [{ms:.0f}ms, {tokens}tok]")
                print()


def test_repetition():
    """Test if the model loops at various temperatures."""
    print("\n" + "=" * 80)
    print("REPETITION LOOP TEST")
    print("=" * 80)

    prompt = "Write a brief paragraph about the history of the internet."
    sp = "Write clearly and concisely. Do not repeat yourself."

    for cfg_name, cfg in CONFIGS.items():
        text, tokens, ms = query(sp, prompt, cfg, max_tokens=300)

        # Detect repetition: check if any 20-char substring repeats 3+ times
        repeated = False
        for i in range(len(text) - 60):
            chunk = text[i:i+20]
            if text.count(chunk) >= 3:
                repeated = True
                break

        flag = " [REPETITION DETECTED]" if repeated else ""
        print(f"\n  {cfg_name} (temp={cfg['temperature']}, rp={cfg['repeat_penalty']}): {tokens}tok, {ms:.0f}ms{flag}")
        print(f"  {text.strip()[:200]}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("all", "summarizer"):
        test_summarizer()
    if mode in ("all", "extractor"):
        test_extractor()
    if mode in ("all", "tts"):
        test_tts()
    if mode in ("all", "repetition"):
        test_repetition()

    if mode == "all":
        print("\n" + "=" * 80)
        print("ALL TESTS COMPLETE")
        print("=" * 80)


if __name__ == "__main__":
    main()

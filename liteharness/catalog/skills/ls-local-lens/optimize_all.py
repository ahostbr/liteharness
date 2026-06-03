#!/usr/bin/env python3
"""
Optimize All Models — Runs a focused test battery against any loaded model.
Usage: python optimize_all.py <model_identifier> [role]
  role: summarizer | extractor | reasoning | tts | coder | all (default: all)
"""

import json
import urllib.request
import time
import sys
import io
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

API = "http://localhost:1234/v1/chat/completions"


# ── Shared test content ─────────────────────────────────────────────────────

SUMMARY_TEXT = """
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

EXTRACT_TEXT = """
Meeting Notes - Q1 2026 Budget Review
Date: March 3, 2026
Attendees: Sarah Chen (CFO), Mike Torres (VP Engineering), Lisa Park (VP Sales), James Wright (CEO)
Key Decisions:
- Engineering budget increased by 15% to $4.2M for Q2
- Sales team headcount approved: 8 new hires in APAC region
- New product launch "Project Aurora" greenlit with $1.5M budget
Action Items:
- Sarah to finalize revised P&L by March 10
- Mike to submit cloud migration timeline by March 7
Revenue: Q1 actual $12.3M vs $11.8M forecast (4.2% above target)
Burn rate: $2.1M/month (down from $2.4M in Q4 2025)
Runway: 18 months at current burn rate
"""

REASONING_PROBLEMS = [
    {
        "name": "Logic puzzle",
        "prompt": "A farmer has a fox, a chicken, and a bag of grain. He needs to cross a river in a boat that can only carry him and one item. The fox will eat the chicken if left alone, and the chicken will eat the grain if left alone. How does he get everything across?",
        "check": ["chicken", "grain", "fox"],
    },
    {
        "name": "Math word problem",
        "prompt": "A store sells apples for $1.50 each. If you buy 5 or more, you get a 20% discount. How much do 7 apples cost?",
        "check": ["8.40", "8.4"],
    },
    {
        "name": "Common sense trap",
        "prompt": "I need to wash my car. The car wash is 100 meters away. Should I walk or drive?",
        "check": ["drive"],
    },
    {
        "name": "Counting trick",
        "prompt": "A farmer has 17 sheep. All but 9 die. How many sheep does the farmer have left?",
        "check": ["9"],
    },
]

CODE_PROBLEMS = [
    {
        "name": "FizzBuzz",
        "prompt": "Write a Python function that prints FizzBuzz for numbers 1-15. Fizz for multiples of 3, Buzz for 5, FizzBuzz for both.",
        "check": ["def", "fizz", "buzz", "15"],
    },
    {
        "name": "Bug detection",
        "prompt": "Find the bug in this Python code:\ndef avg(numbers):\n    total = 0\n    for n in numbers:\n        total += n\n    return total / len(numbers)\nWhat happens if numbers is empty?",
        "check": ["zero", "empty", "ZeroDivision", "division"],
    },
]


def query(model, system_prompt, user_prompt, config, max_tokens=500):
    """Send a query and return (response_text, tokens, time_ms)."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": config.get("temperature", 0.1),
        "top_p": config.get("top_p", 0.9),
        "repeat_penalty": config.get("repeat_penalty", 1.1),
        "stream": False,
    }).encode()

    req = urllib.request.Request(API, data=payload, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=120).read())
        elapsed = (time.perf_counter() - start) * 1000
        text = resp["choices"][0]["message"]["content"]
        if "<think>" in text:
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        usage = resp.get("usage", {})
        tokens = usage.get("completion_tokens", len(text.split()))
        return text, tokens, elapsed
    except Exception as e:
        return f"ERROR: {e}", 0, 0


def test_summarizer(model):
    print(f"\n{'='*80}\n[{model}] SUMMARIZER TEST\n{'='*80}")
    configs = [
        ("cold-0.05", {"temperature": 0.05}),
        ("cool-0.1", {"temperature": 0.1}),
        ("warm-0.3", {"temperature": 0.3}),
    ]
    sp = "Summarize in bullet points. Include: who, what, numbers, dates, decisions. No intro sentence. Start with bullets immediately. Be exact."
    for name, cfg in configs:
        text, tok, ms = query(model, sp, f"Summarize:\n\n{SUMMARY_TEXT}", cfg, max_tokens=400)
        print(f"\n--- {name} [{ms:.0f}ms, {tok}tok] ---")
        print(text[:500])


def test_extractor(model):
    print(f"\n{'='*80}\n[{model}] EXTRACTOR TEST\n{'='*80}")
    configs = [
        ("cold-0.05", {"temperature": 0.05}),
        ("cool-0.1", {"temperature": 0.1}),
    ]
    sp = "You are a data extraction tool. Output requested fields as key: value pairs. Nothing else. If missing, output: MISSING."
    questions = [
        "Extract all dollar amounts and what they refer to.",
        "What is the company's burn rate and runway?",
    ]
    for name, cfg in configs:
        print(f"\n--- {name} ---")
        for q in questions:
            text, tok, ms = query(model, sp, f"{q}\n\nContent:\n{EXTRACT_TEXT}", cfg, max_tokens=300)
            print(f"  Q: {q} [{ms:.0f}ms, {tok}tok]")
            for line in text.strip().split("\n")[:6]:
                print(f"    {line}")


def test_reasoning(model):
    print(f"\n{'='*80}\n[{model}] REASONING TEST\n{'='*80}")
    configs = [
        ("cold-0.05", {"temperature": 0.05}),
        ("cool-0.1", {"temperature": 0.1}),
        ("warm-0.3", {"temperature": 0.3}),
    ]
    sp = "Answer the question directly and briefly. Explain your reasoning in 1-2 sentences."
    for name, cfg in configs:
        print(f"\n--- {name} ---")
        for prob in REASONING_PROBLEMS:
            text, tok, ms = query(model, sp, prob["prompt"], cfg, max_tokens=300)
            hit = any(c.lower() in text.lower() for c in prob["check"])
            flag = "PASS" if hit else "FAIL"
            print(f"  [{flag}] {prob['name']} [{ms:.0f}ms, {tok}tok]")
            print(f"    {text.strip()[:200]}")


def test_coder(model):
    print(f"\n{'='*80}\n[{model}] CODER TEST\n{'='*80}")
    configs = [
        ("cold-0.05", {"temperature": 0.05}),
        ("cool-0.1", {"temperature": 0.1}),
    ]
    sp = "You are a precise code analyst. Answer directly with code when appropriate. Do not use any tools. Be concise."
    for name, cfg in configs:
        print(f"\n--- {name} ---")
        for prob in CODE_PROBLEMS:
            text, tok, ms = query(model, sp, prob["prompt"], cfg, max_tokens=400)
            hit = any(c.lower() in text.lower() for c in prob["check"])
            flag = "PASS" if hit else "FAIL"
            print(f"  [{flag}] {prob['name']} [{ms:.0f}ms, {tok}tok]")
            for line in text.strip().split("\n")[:10]:
                print(f"    {line}")


def test_tts(model):
    print(f"\n{'='*80}\n[{model}] TTS TEST\n{'='*80}")
    configs = [
        ("warm-0.3", {"temperature": 0.3}),
        ("hot-0.6", {"temperature": 0.6}),
    ]
    sp = "You are a friendly, conversational assistant. Respond naturally as if speaking aloud. No markdown formatting, no bullet points, no headers. Use plain sentences. Keep responses concise - 2-3 sentences."
    prompts = [
        "What's the weather like in Seattle in March?",
        "Tell me something interesting about octopuses.",
    ]
    for name, cfg in configs:
        print(f"\n--- {name} ---")
        for p in prompts:
            text, tok, ms = query(model, sp, p, cfg, max_tokens=150)
            has_md = any(c in text for c in ["**", "##", "- ", "* ", "1."])
            flag = " [MARKDOWN!]" if has_md else ""
            print(f"  Q: {p}")
            print(f"  A: {text.strip()[:200]}{flag} [{ms:.0f}ms, {tok}tok]")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    model = sys.argv[1]
    role = sys.argv[2] if len(sys.argv) > 2 else "all"

    print(f"Testing model: {model}")

    if role in ("all", "summarizer"):
        test_summarizer(model)
    if role in ("all", "extractor"):
        test_extractor(model)
    if role in ("all", "reasoning"):
        test_reasoning(model)
    if role in ("all", "coder"):
        test_coder(model)
    if role in ("all", "tts"):
        test_tts(model)

    print(f"\n{'='*80}\nALL TESTS COMPLETE for {model}\n{'='*80}")


if __name__ == "__main__":
    main()

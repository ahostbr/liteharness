#!/usr/bin/env python3
"""Role presets for Local Lens — model-AGNOSTIC sampling + system prompts.

Lifted verbatim out of lms_switch.py when that script was rewritten to stop
hardcoding a model lineup (Ryan, 2026-08-23). These presets never named a
model, so they never went stale: they describe HOW to ask, not WHOM.

Empirically tuned via optimize_08b.py's test battery.
"""

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

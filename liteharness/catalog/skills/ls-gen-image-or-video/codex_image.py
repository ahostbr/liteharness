#!/usr/bin/env python3
"""Generate images through Codex's ChatGPT OAuth bridge (no OPENAI_API_KEY).

Spoofs the codex_cli_rs client against https://chatgpt.com/backend-api/codex.

Backends:
  responses  POST /responses          native image_generation tool (default; model gpt-5.5)
  images     POST /images/generations typed endpoint, OpenAI-style JSON (model gpt-image-2)

Reference images (--image PATH, repeatable) turn generation into an EDIT:
  responses  input_image content parts with data URLs in the user message
  images     POST /images/edits      same body plus "images": [{image_url}]

Auth comes from ~/.codex/auth.json (tokens.access_token / refresh_token / account_id).
Expired tokens are refreshed via https://auth.openai.com/oauth/token and written back.

Usage:
  python codex_image.py "a red fox on a mossy rock at dawn" --out fox.png
  python codex_image.py "..." --backend images --model gpt-image-2
  python codex_image.py "restyle this fox in watercolor" --image fox.png
  python codex_image.py --refresh-only
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = "https://chatgpt.com/backend-api/codex"
REFRESH_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex_cli_rs client id (matches Ryan's token)

DEFAULT_MODELS = {"responses": "gpt-5.5", "images": "gpt-image-2"}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def image_data_url(path: str) -> str:
    """Read a local image and return it as a data URL (what the API wants)."""
    ext = os.path.splitext(path)[1].lower()
    mime = _MIME_BY_EXT.get(ext, "image/png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def log(msg: str) -> None:
    print(f"[codex_image] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- auth store

def auth_path() -> str:
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    return os.path.join(codex_home, "auth.json")


def load_auth(path: str | None = None) -> dict:
    p = path or auth_path()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    tokens = data.get("tokens", {})
    return {
        "path": p,
        "access_token": tokens.get("access_token"),
        "refresh_token": tokens.get("refresh_token"),
        "id_token": tokens.get("id_token"),
        "account_id": tokens.get("account_id") or data.get("account_id"),
    }


def save_auth(auth: dict) -> None:
    with open(auth["path"], encoding="utf-8") as f:
        data = json.load(f)
    tokens = data.setdefault("tokens", {})
    if auth.get("access_token"):
        tokens["access_token"] = auth["access_token"]
    if auth.get("refresh_token"):
        tokens["refresh_token"] = auth["refresh_token"]
    if auth.get("id_token"):
        tokens["id_token"] = auth["id_token"]
    data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime())
    with open(auth["path"], "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def jwt_exp(token: str | None) -> float | None:
    if not token or token.count(".") < 2:
        return None
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims["exp"]) if "exp" in claims else None
    except Exception:
        return None


def refresh_auth(auth: dict, force: bool = False) -> bool:
    """Refresh tokens in place. Returns True if a new access token was obtained."""
    exp = jwt_exp(auth.get("access_token"))
    if not force and exp is not None and exp - time.time() > 300:
        return False
    if not auth.get("refresh_token"):
        raise RuntimeError("no refresh_token in auth.json; re-login with `codex login`")

    body = json.dumps({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": auth["refresh_token"],
    }).encode()
    req = urllib.request.Request(REFRESH_URL, data=body, method="POST", headers={
        "accept": "application/json",
        "content-type": "application/json",
        "originator": "codex_cli_rs",
        "user-agent": user_agent(),
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    if not data.get("access_token"):
        raise RuntimeError(f"refresh failed: {json.dumps(data)[:300]}")

    auth["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        auth["refresh_token"] = data["refresh_token"]
    if data.get("id_token"):
        auth["id_token"] = data["id_token"]
    save_auth(auth)
    log(f"token refreshed (new exp in {int(jwt_exp(auth['access_token']) - time.time())}s)")
    return True


# ---------------------------------------------------------------- http bits

def user_agent() -> str:
    win = platform.release() if os.name == "nt" else platform.system()
    return f"codex_cli_rs/0.137.0 ({win}; {platform.machine() or 'x64'}) unknown"


def do_request(url: str, headers: dict, body: bytes | None, timeout: float):
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    return urllib.request.urlopen(req, timeout=timeout)


# ---------------------------------------------------------------- backends

def build_responses_body(prompt: str, model: str, request_id: str, stream: bool = True,
                         refs: list[str] | None = None) -> dict:
    content: list[dict] = []
    for i, url in enumerate(refs or []):
        content.append({"type": "input_text", "text": f"<image name=image_{i}>"})
        content.append({"type": "input_image", "image_url": url, "detail": "auto"})
        content.append({"type": "input_text", "text": "</image>"})
    content.append({"type": "input_text", "text": prompt})
    return {
        "model": model,
        "instructions": "",
        "input": [
            {"type": "message", "role": "user", "content": content},
        ],
        "tools": [{"type": "image_generation", "output_format": "png"}],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "prompt_cache_key": request_id,
        "stream": stream,
        "store": False,
        "reasoning": None,
    }


def build_images_body(prompt: str, model: str) -> dict:
    return {
        "model": model,
        "prompt": prompt,
        "background": "auto",
        "quality": "auto",
        "size": "auto",
    }


def base_headers(auth: dict, request_id: str | None = None) -> dict:
    h = {
        "authorization": f"Bearer {auth['access_token']}",
        "chatgpt-account-id": auth.get("account_id") or "",
        "content-type": "application/json",
        "originator": "codex_cli_rs",
        "user-agent": user_agent(),
    }
    if request_id:
        h["accept"] = "text/event-stream, application/json"
        h["session-id"] = str(uuid.uuid4())
        h["thread-id"] = request_id
        h["x-client-request-id"] = request_id
    else:
        h["accept"] = "application/json"
    return h


def iter_sse(resp):
    """Yield (event, data) pairs from an SSE stream."""
    event_name = None
    data_lines: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines or event_name:
                yield (event_name, "\n".join(data_lines))
            event_name, data_lines = None, []
            continue
        if line.startswith(":"):  # comment/keepalive
            continue
        field, _, value = line.partition(":")
        value = value.lstrip(" ")
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)


def collect_image_calls(node, calls: list):
    """Recursively find image_generation_call objects in a payload tree."""
    if isinstance(node, dict):
        if node.get("type") == "image_generation_call":
            calls.append(node)
        for k, v in node.items():
            if k != "result" and not isinstance(v, str):
                collect_image_calls(v, calls)
    elif isinstance(node, list):
        for item in node:
            collect_image_calls(item, calls)


def generate_responses(auth: dict, prompt: str, model: str, timeout: float,
                       refs: list[str] | None = None) -> list[bytes]:
    request_id = str(uuid.uuid4())
    url = f"{BASE_URL}/responses"
    body = json.dumps(build_responses_body(prompt, model, request_id, refs=refs)).encode()
    log(f"POST {url} (model={model})")

    with do_request(url, base_headers(auth, request_id), body, timeout) as resp:
        best: dict | None = None
        for _event, data in iter_sse(resp):
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            # partial image chunks (progressive preview)
            if isinstance(payload, dict) and payload.get("type") == "response.image_generation_call.partial_image" \
                    and payload.get("partial_image_b64"):
                best = {"result": payload["partial_image_b64"], "status": "partial"}

            calls: list[dict] = []
            collect_image_calls(payload, calls)
            for call in calls:
                if not call.get("result"):
                    continue
                if best is None or (call.get("status") == "completed" and best.get("status") != "completed"):
                    best = call

    if best is None or not best.get("result"):
        raise RuntimeError("stream ended without a completed image_generation_call result")
    return [base64.b64decode(best["result"])]


def _extract_images(data, timeout: float) -> list[bytes]:
    """Pull PNG bytes out of the typed endpoint's {"data": [...]} envelope."""
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise RuntimeError(f"unexpected images response shape: {json.dumps(data)[:300]}")
    out = []
    for item in items:
        b64 = item.get("b64_json") or (item.get("url"))
        if b64 and not str(b64).startswith("http"):
            out.append(base64.b64decode(b64))
        elif b64:
            with urllib.request.urlopen(str(b64), timeout=timeout) as r:
                out.append(r.read())
    if not out:
        raise RuntimeError(f"no image data in response: {json.dumps(data)[:300]}")
    return out


def generate_images(auth: dict, prompt: str, model: str, timeout: float) -> list[bytes]:
    url = f"{BASE_URL}/images/generations"
    body = json.dumps(build_images_body(prompt, model)).encode()
    log(f"POST {url} (model={model})")

    with do_request(url, base_headers(auth), body, timeout) as resp:
        data = json.loads(resp.read().decode())
    return _extract_images(data, timeout)


def generate_edits(auth: dict, prompt: str, model: str, refs: list[str],
                   timeout: float) -> list[bytes]:
    url = f"{BASE_URL}/images/edits"
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "background": "auto",
        "quality": "auto",
        "size": "auto",
        "images": [{"image_url": u} for u in refs],
    }).encode()
    log(f"POST {url} (model={model}, {len(refs)} ref(s))")

    with do_request(url, base_headers(auth), body, timeout) as resp:
        data = json.loads(resp.read().decode())
    return _extract_images(data, timeout)


# ---------------------------------------------------------------- main

def generate(prompt: str, backend: str, model: str | None, timeout: float,
             refs: list[str] | None) -> list[bytes]:
    auth = load_auth()
    if not auth.get("access_token"):
        raise RuntimeError(f"no access_token in {auth['path']}")

    model = model or DEFAULT_MODELS[backend]
    if backend == "responses":
        gen = lambda a, p, m, t: generate_responses(a, p, m, t, refs=refs)
    elif refs:
        gen = lambda a, p, m, t: generate_edits(a, p, m, refs, t)
    else:
        gen = generate_images

    for attempt in (1, 2):
        try:
            return gen(auth, prompt, model, timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:
                pass
            if e.code == 401 and attempt == 1:
                log("HTTP 401 — refreshing token, retrying once")
                refresh_auth(auth, force=True)
                continue
            raise RuntimeError(f"HTTP {e.code} from Codex backend: {detail}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate images via Codex ChatGPT OAuth (no API key).")
    ap.add_argument("prompt", nargs="?", help="image prompt")
    ap.add_argument("--out", default=None, help="output PNG path (default: codex-image-<ts>.png in cwd)")
    ap.add_argument("--backend", choices=["responses", "images"], default="responses")
    ap.add_argument("--model", default=None, help=f"override model (defaults: {DEFAULT_MODELS})")
    ap.add_argument("--image", action="append", default=[], metavar="PATH",
                    help="reference image for an EDIT; repeatable (png/jpg/gif/webp)")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--refresh-only", action="store_true", help="refresh OAuth token and exit")
    args = ap.parse_args()

    if args.refresh_only:
        auth = load_auth()
        refresh_auth(auth, force=True)
        print("refreshed OK")
        return 0

    if not args.prompt:
        ap.error("prompt required (or --refresh-only)")

    refs = [image_data_url(p) for p in args.image]

    started = time.time()
    images = generate(args.prompt, args.backend, args.model, args.timeout, refs or None)

    out_path = args.out or f"codex-image-{int(time.time())}.png"
    for i, img in enumerate(images):
        p = out_path if len(images) == 1 else re.sub(r"\.([a-z]+)$", rf"-{i+1}.\1", out_path)
        with open(p, "wb") as f:
            f.write(img)
        print(p)

    log(f"done in {time.time() - started:.1f}s ({len(images)} image(s))")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"[codex_image] ERROR: {e}", file=sys.stderr)
        sys.exit(1)

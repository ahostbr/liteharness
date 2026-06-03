"""Chrome DevTools Protocol bridge for ChatGPT Desktop.

Requirements:
    pip install requests websockets
    ChatGPT Desktop installed from the Windows Store (OpenAI.ChatGPT-Desktop)
    and signed in with a Pro account.

The ChatGPT Desktop executable is resolved at runtime via Get-AppxPackage
to avoid hardcoding the version-pinned WindowsApps path.
"""

import json
import asyncio
import time
import subprocess
import requests
from pathlib import Path
from typing import Optional


def _find_chatgpt_exe() -> Optional[str]:
    """Resolve ChatGPT Desktop exe via Get-AppxPackage (version-independent)."""
    try:
        result = subprocess.run(
            [
                "powershell", "-Command",
                "(Get-AppxPackage -Name 'OpenAI.ChatGPT-Desktop' | "
                "Select-Object -ExpandProperty InstallLocation) + '\\app\\ChatGPT.exe'"
            ],
            capture_output=True, text=True, timeout=10,
        )
        path = result.stdout.strip()
        if path and Path(path).exists():
            return path
    except Exception:
        pass
    return None


# Resolved lazily on first launch call
_CHATGPT_EXE: Optional[str] = None

CDP_PORT = 9223
CDP_URL = f"http://localhost:{CDP_PORT}"


def is_running() -> bool:
    try:
        r = requests.get(f"{CDP_URL}/json/version", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def get_page_id() -> Optional[str]:
    try:
        pages = requests.get(f"{CDP_URL}/json", timeout=2).json()
        for p in pages:
            if p.get("type") == "page" and "chatgpt.com" in p.get("url", ""):
                return p["id"]
    except Exception:
        pass
    return None


def launch():
    if is_running():
        return {"status": "already_running", "port": CDP_PORT}

    global _CHATGPT_EXE
    if _CHATGPT_EXE is None:
        _CHATGPT_EXE = _find_chatgpt_exe()
    if not _CHATGPT_EXE:
        return {
            "status": "failed",
            "error": (
                "ChatGPT Desktop not found. Install it from the Windows Store "
                "(search 'ChatGPT' by OpenAI) and sign in with a Pro account."
            ),
        }

    # Kill existing instances
    subprocess.run(
        ["powershell", "-Command", 'Stop-Process -Name "ChatGPT" -Force -ErrorAction SilentlyContinue'],
        capture_output=True
    )
    time.sleep(2)

    subprocess.Popen(
        [_CHATGPT_EXE, f"--remote-debugging-port={CDP_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    for _ in range(15):
        time.sleep(1)
        if is_running():
            return {"status": "launched", "port": CDP_PORT}

    return {"status": "failed", "error": "CDP not responding after 15s"}


def evaluate(expression: str) -> str:
    page_id = get_page_id()
    if not page_id:
        raise RuntimeError("No ChatGPT page found")

    async def _eval():
        import websockets
        uri = f"ws://localhost:{CDP_PORT}/devtools/page/{page_id}"
        async with websockets.connect(uri, max_size=2**24) as ws:
            await ws.send(json.dumps({
                "id": 1,
                "method": "Runtime.evaluate",
                "params": {"expression": expression, "awaitPromise": True}
            }))
            r = json.loads(await ws.recv())
            result = r.get("result", {}).get("result", {})
            if result.get("type") == "string":
                return result["value"]
            return json.dumps(result)

    return asyncio.run(_eval())


def type_and_submit(text: str) -> str:
    page_id = get_page_id()
    if not page_id:
        return "ERROR: No ChatGPT page found"

    async def _type():
        import websockets
        uri = f"ws://localhost:{CDP_PORT}/devtools/page/{page_id}"
        async with websockets.connect(uri, max_size=2**24) as ws:
            # Focus the prompt input (contenteditable div)
            await ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                "expression": "document.getElementById('prompt-textarea')?.focus() || document.querySelector('textarea')?.focus(); 'focused'"
            }}))
            await ws.recv()

            # Type via CDP Input.insertText (works with contenteditable + React)
            await ws.send(json.dumps({"id": 2, "method": "Input.insertText", "params": {"text": text}}))
            await ws.recv()

            # Press Enter to submit
            await ws.send(json.dumps({"id": 3, "method": "Input.dispatchKeyEvent", "params": {
                "type": "keyDown", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13
            }}))
            await ws.recv()
            await ws.send(json.dumps({"id": 4, "method": "Input.dispatchKeyEvent", "params": {
                "type": "keyUp", "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13
            }}))
            await ws.recv()
            return "OK: prompt submitted"

    return asyncio.run(_type())


def wait_for_image(timeout: int = 120, poll_interval: int = 5) -> Optional[str]:
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = evaluate('''
                (() => {
                    // Look for generated images
                    const imgs = document.querySelectorAll('img[alt*="Generated"], img[alt*="image"], img[data-testid*="image"]');
                    const latest = Array.from(imgs).pop();
                    if (latest && latest.src && latest.src.startsWith('http')) {
                        return JSON.stringify({found: true, src: latest.src, alt: latest.alt || ''});
                    }
                    // Check if still generating
                    const thinking = document.querySelector('[data-testid*="thinking"], [class*="thinking"], [class*="streaming"]');
                    if (thinking) return JSON.stringify({found: false, status: "generating"});
                    return JSON.stringify({found: false, status: "waiting"});
                })()
            ''')
            data = json.loads(result)
            if data.get("found"):
                return data["src"]
            if data.get("status") == "waiting" and time.time() - start > 30:
                # After 30s of no activity, might be done without image
                break
        except Exception:
            pass
        time.sleep(poll_interval)
    return None


def read_response() -> str:
    return evaluate('''
        (() => {
            const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
            const last = Array.from(msgs).pop();
            if (last) return last.innerText.substring(0, 5000);
            return "(no assistant response found)";
        })()
    ''')


def get_page_text(max_chars: int = 3000) -> str:
    return evaluate(f'document.body.innerText.substring(0, {max_chars})')


def navigate_new_chat() -> str:
    return evaluate('''
        (() => {
            const btn = Array.from(document.querySelectorAll('a, button')).find(
                el => el.textContent.trim() === 'New chat'
            );
            if (btn) { btn.click(); return "OK: navigated to new chat"; }
            return "ERROR: New chat button not found";
        })()
    ''')

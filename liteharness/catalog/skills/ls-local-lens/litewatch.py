#!/usr/bin/env python3
"""
LiteWatch — Screen Context Capture Tool

Captures screenshots on a timer, sends them to a local VLM for description,
and writes concise text summaries to a rolling file. Gives Claude Code
screen awareness without burning tokens on raw images.

Usage: python litewatch.py
"""

import json
import urllib.request
import base64
import io
import re
import time
import ctypes
import ctypes.wintypes
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.widgets import Header, Footer, Static, Label, Input, Select, TextArea, Button, Rule
from textual.screen import ModalScreen
from textual.reactive import reactive
from textual.worker import Worker, get_current_worker
from textual import work
from textual.message import Message

import mss
from PIL import Image

# ── Paths ────────────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "litewatch_config.json"

DEFAULT_SYSTEM_PROMPT = (
    "You are a screen reader for an AI coding assistant. "
    "Describe what the user is currently doing on their computer. Focus on:\n"
    "- Active application and window title\n"
    "- File or page open (name, path if visible)\n"
    "- Key visible content (code, text, UI elements)\n"
    "- User's apparent task or activity\n"
    "Be concise. 2-4 sentences maximum. No markdown formatting."
)

DEFAULT_CONFIG = {
    "interval_seconds": 30,
    "capture_mode": "primary",
    "model": "glm-ocr",
    "max_summaries": 20,
    "output_path": str(SCRIPT_DIR / "screen_context.txt"),
    "api_url": "http://localhost:1234/v1/chat/completions",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "max_tokens": 200,
    "temperature": 0.1,
}


# ── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                saved = json.load(f)
            cfg = {**DEFAULT_CONFIG, **saved}
            return cfg
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Screenshot Capture ───────────────────────────────────────────────────────

def capture_screenshot(mode: str = "primary", max_dim: int = 960) -> bytes:
    """Capture screenshot, downscale to max_dim, and return PNG bytes."""
    with mss.mss() as sct:
        if mode == "all":
            monitor = sct.monitors[0]
        elif mode == "active":
            monitor = _get_active_window_rect() or sct.monitors[1]
        else:
            monitor = sct.monitors[1]

        img = sct.grab(monitor)
        png_bytes = mss.tools.to_png(img.rgb, img.size)

        # Downscale large screenshots to avoid LM Studio image processing failures
        pil_img = Image.open(io.BytesIO(png_bytes))
        w, h = pil_img.size
        if max(w, h) > max_dim:
            scale = max_dim / max(w, h)
            pil_img = pil_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG")
            return buf.getvalue()
        return png_bytes


def _get_active_window_rect() -> Optional[dict]:
    """Get the active window rectangle using ctypes (Windows)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        width = right - left
        height = bottom - top
        if width <= 0 or height <= 0:
            return None
        return {"left": left, "top": top, "width": width, "height": height}
    except Exception:
        return None


# ── VLM Query ────────────────────────────────────────────────────────────────

def _find_loaded_vlm(api_base: str, preferred: str = "") -> Optional[str]:
    """Auto-detect a loaded VLM model via the v0 API. Prefers the configured model."""
    try:
        v0_url = api_base.replace("/v1/chat/completions", "/api/v0/models")
        resp = json.loads(urllib.request.urlopen(v0_url, timeout=5).read())
        loaded_vlms = [
            m["id"] for m in resp.get("data", [])
            if m.get("state") == "loaded" and m.get("type") == "vlm"
        ]
        if not loaded_vlms:
            return None
        # Prefer the configured model if it's among the loaded VLMs
        for vid in loaded_vlms:
            if preferred and (preferred in vid or vid in preferred):
                return vid
        return loaded_vlms[0]
    except Exception:
        pass
    return None


def query_vlm(png_bytes: bytes, cfg: dict) -> tuple[str, float]:
    """Send screenshot to VLM and return (description, elapsed_ms)."""
    # Auto-detect a loaded VLM, preferring the configured model
    model = cfg["model"]
    detected = _find_loaded_vlm(cfg["api_url"], preferred=model)
    if detected:
        model = detected

    if not detected:
        raise RuntimeError("No VLM model loaded. Load qwen-0.8b in LM Studio.")

    b64 = base64.b64encode(png_bytes).decode()
    data_uri = f"data:image/png;base64,{b64}"

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": cfg["system_prompt"]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe what you see on screen."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "max_tokens": cfg["max_tokens"],
        "temperature": cfg["temperature"],
    }).encode()

    req = urllib.request.Request(
        cfg["api_url"],
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    start = time.perf_counter()
    resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    elapsed = (time.perf_counter() - start) * 1000

    text = resp["choices"][0]["message"]["content"]
    if "<think>" in text:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    return text.strip(), elapsed


# ── Rolling File Writer ──────────────────────────────────────────────────────

def write_rolling_file(entries: list[dict], cfg: dict):
    """Write the rolling summary file."""
    output_path = Path(cfg["output_path"])
    if not output_path.is_absolute():
        output_path = SCRIPT_DIR / output_path

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    max_s = cfg["max_summaries"]

    lines = [
        "# Screen Context - Auto-generated by LiteWatch",
        f"# Last updated: {now}",
        f"# Entries: {len(entries)} of {max_s} max",
        "",
    ]

    for entry in entries:
        lines.append(f"## {entry['time']}")
        lines.append(entry["text"])
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# ── Textual UI ───────────────────────────────────────────────────────────────

class CaptureResult(Message):
    """Posted when a capture completes."""
    def __init__(self, entry: dict, elapsed_ms: float) -> None:
        super().__init__()
        self.entry = entry
        self.elapsed_ms = elapsed_ms


class CaptureError(Message):
    """Posted when a capture fails."""
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class SettingsScreen(ModalScreen[bool]):
    """Settings modal."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("f1", "cancel", "Close"),
    ]
    DEFAULT_CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-container {
        width: 70;
        max-height: 36;
        background: $surface;
        border: tall $accent;
        padding: 1 2;
        overflow-y: auto;
    }
    #settings-container Label {
        margin-top: 1;
        color: $text-muted;
    }
    #settings-container Input {
        margin-bottom: 0;
    }
    #settings-container Select {
        margin-bottom: 0;
    }
    #settings-container TextArea {
        height: 6;
        margin-bottom: 0;
    }
    #close-hint {
        margin-top: 1;
        color: $text-muted;
        text-style: italic;
    }
    #btn-row {
        margin-top: 1;
        height: 3;
        align: center middle;
    }
    #btn-row Button {
        margin: 0 1;
    }
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = dict(cfg)

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-container"):
            yield Label("LiteWatch Settings", classes="title")
            yield Rule()

            yield Label("Interval (seconds)")
            yield Input(
                value=str(self.cfg["interval_seconds"]),
                id="input-interval",
                type="integer",
            )

            yield Label("Capture Mode")
            yield Select(
                [(m, m) for m in ("primary", "all", "active")],
                value=self.cfg["capture_mode"],
                id="select-mode",
            )

            yield Label("Model Identifier")
            yield Input(value=self.cfg["model"], id="input-model")

            yield Label("Max Summaries in File")
            yield Input(
                value=str(self.cfg["max_summaries"]),
                id="input-max",
                type="integer",
            )

            yield Label("Output File Path")
            yield Input(value=self.cfg["output_path"], id="input-output")

            yield Label("System Prompt")
            yield TextArea(self.cfg["system_prompt"], id="ta-prompt")

            yield Label("Press F1 or Escape to close without saving", id="close-hint")
            with Horizontal(id="btn-row"):
                yield Button("Save", variant="primary", id="btn-save")
                yield Button("Cancel", id="btn-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self.cfg["interval_seconds"] = max(5, int(self.query_one("#input-interval", Input).value or "30"))
            sel = self.query_one("#select-mode", Select)
            self.cfg["capture_mode"] = sel.value if sel.value != Select.BLANK else "primary"
            self.cfg["model"] = self.query_one("#input-model", Input).value or "qwen-0.8b"
            self.cfg["max_summaries"] = max(1, int(self.query_one("#input-max", Input).value or "20"))
            self.cfg["output_path"] = self.query_one("#input-output", Input).value
            self.cfg["system_prompt"] = self.query_one("#ta-prompt", TextArea).text
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class LiteWatchApp(App):
    """Main LiteWatch TUI application."""

    TITLE = "LiteWatch"
    SUB_TITLE = "Screen Context Capture"

    CSS = """
    #status-bar {
        height: 3;
        background: $boost;
        padding: 0 2;
    }
    #status-line1, #status-line2 {
        height: 1;
    }
    #log-area {
        padding: 1 2;
    }
    .entry-time {
        color: $accent;
        text-style: bold;
    }
    .entry-text {
        margin-bottom: 1;
    }
    .error-text {
        color: $error;
    }
    """

    BINDINGS = [
        ("f1", "open_settings", "Settings"),
        ("f2", "toggle_pause", "Pause/Resume"),
        ("f3", "manual_capture", "Capture Now"),
        ("f4", "clear_log", "Clear"),
        ("q", "quit", "Quit"),
    ]

    running = reactive(True)
    capture_count = reactive(0)
    last_elapsed = reactive(0.0)
    countdown = reactive(0)

    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.entries: list[dict] = []
        self._capture_worker: Optional[Worker] = None
        self._countdown_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            with Vertical(id="status-bar"):
                yield Static(id="status-line1")
                yield Static(id="status-line2")
            yield Rule()
            with VerticalScroll(id="log-area"):
                yield Static("Waiting for first capture...", id="log-content")
        yield Footer()

    def on_mount(self) -> None:
        self._update_status()
        self._start_capture_loop()
        self._countdown_timer = self.set_interval(1, self._tick_countdown)

    def _tick_countdown(self) -> None:
        if self.running and self.countdown > 0:
            self.countdown -= 1

    def _update_status(self) -> None:
        state = "[green]Running[/]" if self.running else "[yellow]Paused[/]"
        model = self.cfg["model"]
        mode = self.cfg["capture_mode"]
        if not self.running:
            cd = "[yellow]paused[/]"
        elif self.countdown == 0:
            cd = "[bold magenta]capturing...[/]"
        elif self.countdown <= 5:
            cd = f"[bold red]{self.countdown}s[/]"
        elif self.countdown <= 10:
            cd = f"[bold yellow]{self.countdown}s[/]"
        elif self.countdown <= 20:
            cd = f"[green]{self.countdown}s[/]"
        else:
            cd = f"[dim]{self.countdown}s[/]"
        line1 = f"Status: {state}  Next: {cd}  Model: {model}  Mode: {mode}"
        self.query_one("#status-line1", Static).update(line1)

        count = self.capture_count
        elapsed = f"{self.last_elapsed:.0f}ms" if self.last_elapsed > 0 else "-"
        out = Path(self.cfg["output_path"]).name
        line2 = f"Captures: {count}  Last: {elapsed}  Output: {out}"
        self.query_one("#status-line2", Static).update(line2)

    def watch_running(self) -> None:
        self._update_status()

    def watch_capture_count(self) -> None:
        self._update_status()

    def watch_last_elapsed(self) -> None:
        self._update_status()

    def watch_countdown(self) -> None:
        self._update_status()

    def _render_log(self) -> None:
        if not self.entries:
            self.query_one("#log-content", Static).update("No captures yet.")
            return

        parts = []
        for entry in self.entries:
            parts.append(f"[bold cyan]{entry['time']}[/]")
            parts.append(entry["text"])
            parts.append("")

        self.query_one("#log-content", Static).update("\n".join(parts))

    # ── Capture Loop ─────────────────────────────────────────────────────

    def _start_capture_loop(self) -> None:
        self._capture_worker = self._run_capture_loop()

    @work(thread=True, exclusive=True, group="capture")
    def _run_capture_loop(self) -> None:
        worker = get_current_worker()
        while not worker.is_cancelled:
            if self.running:
                self.countdown = 0  # Show "capturing..." state
                try:
                    png = capture_screenshot(self.cfg["capture_mode"])
                    text, elapsed = query_vlm(png, self.cfg)
                    entry = {
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "text": text,
                    }
                    self.post_message(CaptureResult(entry, elapsed))
                except Exception as e:
                    self.post_message(CaptureError(str(e)[:200]))

            # Reset countdown and wait
            self.countdown = self.cfg["interval_seconds"]
            for _ in range(self.cfg["interval_seconds"] * 10):
                if worker.is_cancelled:
                    return
                time.sleep(0.1)

    def on_capture_result(self, msg: CaptureResult) -> None:
        self.entries.insert(0, msg.entry)
        max_s = self.cfg["max_summaries"]
        self.entries = self.entries[:max_s]
        self.capture_count += 1
        self.last_elapsed = msg.elapsed_ms
        self._render_log()
        write_rolling_file(self.entries, self.cfg)

    def on_capture_error(self, msg: CaptureError) -> None:
        error_entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "text": f"[red]ERROR: {msg.error}[/]",
        }
        self.entries.insert(0, error_entry)
        self.entries = self.entries[: self.cfg["max_summaries"]]
        self._render_log()

    # ── Actions ──────────────────────────────────────────────────────────

    def action_open_settings(self) -> None:
        def on_dismiss(saved: Optional[bool]) -> None:
            if saved:
                screen = self._settings_screen
                self.cfg = screen.cfg
                save_config(self.cfg)
                self._update_status()
                # Restart capture loop with new settings
                self.workers.cancel_group(self, "capture")
                self._start_capture_loop()

        self._settings_screen = SettingsScreen(self.cfg)
        self.push_screen(self._settings_screen, on_dismiss)

    def action_toggle_pause(self) -> None:
        self.running = not self.running

    def action_manual_capture(self) -> None:
        self._do_manual_capture()

    @work(thread=True)
    def _do_manual_capture(self) -> None:
        try:
            png = capture_screenshot(self.cfg["capture_mode"])
            text, elapsed = query_vlm(png, self.cfg)
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "text": text,
            }
            self.post_message(CaptureResult(entry, elapsed))
        except Exception as e:
            self.post_message(CaptureError(str(e)[:200]))

    def action_clear_log(self) -> None:
        self.entries.clear()
        self.capture_count = 0
        self._render_log()

    def action_quit(self) -> None:
        self.workers.cancel_group(self, "capture")
        self.exit()


# ── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = LiteWatchApp()
    app.run()

"""
LiteHarness Embed Service — HTTP server for text embeddings.

Supports two backends:
  cpu (default): ONNX Runtime with all-MiniLM-L6-v2 (~90 MB, CPU-only)
  gpu:           llama-server --embedding with GGUF model (~27 MB, GPU-accelerated)

Endpoints:
  POST /embed   {"texts": ["..."]}  →  {"vectors": [[...]]}
  POST /health  {}                  →  {"status": "ok", "model": "...", "dim": 384}

Usage:
  # CPU backend
  python -m liteharness.embed_service --port 7439 --model-dir /path/to/models
  python -m liteharness.embed_service --download --model-dir /path/to/models

  # GPU backend
  python -m liteharness.embed_service --port 7439 --backend gpu --model-file /path/to/model.gguf
  python -m liteharness.embed_service --download-gguf --gguf-dir /path/to/dir
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── Constants ───────────────────────────────────────────────────────────────────

MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
GGUF_REPO = "leliuga/all-MiniLM-L6-v2-GGUF"
GGUF_FILENAME = "all-MiniLM-L6-v2.Q4_K_M.gguf"
MODEL_DIM = 384  # same for both ONNX and GGUF variants

# Files to download from HuggingFace Hub (CPU/ONNX backend)
_ONNX_FILES = [
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "onnx/model.onnx",
]

# llama-server internal port when running as GPU backend proxy
_LLAMA_EMBED_PORT = 7440


# ── Helpers ──────────────────────────────────────────────────────────────────────


def _emit(payload: dict) -> None:
    """Emit a @@tag:json line to stdout for Electron to parse."""
    print(f"@@tag:json {json.dumps(payload)}", flush=True)


def _find_llama_server() -> str | None:
    """Locate the llama-server binary shipped with LiteSuite or on PATH."""
    home = pathlib.Path.home()
    candidates = [
        home / ".litesuite" / "llm" / "bin" / "llama-server.exe",
        home / ".litesuite" / "llm" / "bin" / "llama-server",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("llama-server") or shutil.which("llama-server.exe")


# ── Download ─────────────────────────────────────────────────────────────────────


def download_model(model_dir: pathlib.Path) -> None:
    """Download all-MiniLM-L6-v2 ONNX model files (CPU backend).

    Emits @@tag:json download_progress events for Electron progress tracking.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "huggingface_hub not installed. Run: pip install 'liteharness[embed]'",
            file=sys.stderr,
        )
        sys.exit(1)

    model_dir.mkdir(parents=True, exist_ok=True)
    _emit({"type": "download_progress", "pct": 0})

    total = len(_ONNX_FILES)
    for i, filename in enumerate(_ONNX_FILES):
        dest = model_dir / filename
        if dest.exists():
            _emit({"type": "download_progress", "pct": int((i + 1) / total * 100)})
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[embed-service] Downloading {filename}...", file=sys.stderr)
        hf_hub_download(
            repo_id=MODEL_REPO,
            filename=filename,
            local_dir=str(model_dir),
        )
        _emit({"type": "download_progress", "pct": int((i + 1) / total * 100)})

    _emit({"type": "download_progress", "pct": 100})
    _emit({"type": "download_complete", "model_dir": str(model_dir)})
    print(f"[embed-service] Model files ready at {model_dir}", file=sys.stderr)


def download_gguf(gguf_dir: pathlib.Path) -> None:
    """Download all-MiniLM-L6-v2 GGUF model file (GPU backend).

    Emits @@tag:json download_progress events for Electron progress tracking.
    The GGUF variant is ~27 MB (Q4_K_M) vs ~90 MB for the ONNX bundle.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print(
            "huggingface_hub not installed. Run: pip install 'liteharness[embed]'",
            file=sys.stderr,
        )
        sys.exit(1)

    gguf_dir.mkdir(parents=True, exist_ok=True)
    dest = gguf_dir / GGUF_FILENAME

    _emit({"type": "download_progress", "pct": 0})

    if not dest.exists():
        print(f"[embed-service] Downloading {GGUF_FILENAME} (~27 MB)...", file=sys.stderr)
        hf_hub_download(
            repo_id=GGUF_REPO,
            filename=GGUF_FILENAME,
            local_dir=str(gguf_dir),
        )

    _emit({"type": "download_progress", "pct": 100})
    _emit({"type": "download_complete", "model_dir": str(gguf_dir)})
    print(f"[embed-service] GGUF model ready at {dest}", file=sys.stderr)


# ── CPU backend (ONNX) ────────────────────────────────────────────────────────────


class EmbedModel:
    """ONNX inference wrapper for all-MiniLM-L6-v2."""

    def __init__(self, model_dir: pathlib.Path) -> None:
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                f"Missing dependency: {exc}. Run: pip install 'liteharness[embed]'"
            ) from exc

        self._np = np

        tokenizer_path = model_dir / "tokenizer.json"
        model_path = model_dir / "onnx" / "model.onnx"

        if not tokenizer_path.exists():
            raise RuntimeError(
                f"Tokenizer not found: {tokenizer_path}. Run --download first."
            )
        if not model_path.exists():
            raise RuntimeError(
                f"Model not found: {model_path}. Run --download first."
            )

        self._tok = Tokenizer.from_file(str(tokenizer_path))
        self._tok.enable_padding(pad_id=0, pad_token="[PAD]")
        self._tok.enable_truncation(max_length=256)

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 2
        opts.log_severity_level = 3  # suppress onnxruntime info logs
        self._session = ort.InferenceSession(str(model_path), sess_options=opts)

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to L2-normalised 384-dim vectors."""
        np = self._np
        encodings = self._tok.encode_batch(texts)

        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # last_hidden_state: [batch, seq_len, 384]
        hidden = outputs[0].astype(np.float32)

        # Mean pool weighted by attention mask
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)
        summed = (hidden * mask).sum(axis=1)
        counts = mask.sum(axis=1).clip(min=1e-9)
        pooled = summed / counts

        # L2 normalise
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
        return (pooled / norms).tolist()


# ── GPU backend (llama-server --embedding proxy) ──────────────────────────────────


class GpuEmbedModel:
    """GPU embedding backend: proxies requests to llama-server --embedding.

    Uses the llama-server binary shipped with LiteSuite (~/.litesuite/llm/bin/).
    The GGUF model must be a 384-dim variant (all-MiniLM-L6-v2 Q4_K_M) to stay
    compatible with existing BLOB embeddings in the harness_patterns table.
    """

    def __init__(self, model_file: str, llama_port: int = _LLAMA_EMBED_PORT) -> None:
        self.model_file = model_file
        self.llama_port = llama_port
        self._process: subprocess.Popen | None = None  # type: ignore[type-arg]

    def start(self) -> None:
        """Start llama-server in embedding mode and wait for it to be ready."""
        llama_bin = _find_llama_server()
        if not llama_bin:
            raise RuntimeError(
                "llama-server binary not found. "
                "Open LiteSuite → Settings → LLM Engine to install it, "
                "or ensure 'llama-server' is on PATH."
            )
        if not pathlib.Path(self.model_file).exists():
            raise RuntimeError(
                f"GGUF model file not found: {self.model_file}. "
                "Download it first via the Enhanced Memory settings panel."
            )

        cmd = [
            llama_bin,
            "--model", self.model_file,
            "--port", str(self.llama_port),
            "--host", "127.0.0.1",
            "--embedding",
            "--ctx-size", "512",   # small context — embeddings don't need much
            "-ngl", "99",          # all layers on GPU
            "--parallel", "1",
        ]
        print(f"[embed-service] Starting llama-server --embedding on :{self.llama_port}...", file=sys.stderr)
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Poll health endpoint until ready (max 30 s)
        for _ in range(60):
            time.sleep(0.5)
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited early (code {self._process.returncode}). "
                    "Check that the GGUF model is valid and llama-server supports --embedding."
                )
            try:
                resp = urllib.request.urlopen(
                    f"http://127.0.0.1:{self.llama_port}/health", timeout=1
                )
                if resp.status == 200:
                    print("[embed-service] llama-server --embedding ready", file=sys.stderr)
                    return
            except Exception:
                pass

        raise RuntimeError("Timed out waiting for llama-server --embedding to start")

    def stop(self) -> None:
        """Terminate the llama-server subprocess."""
        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts via llama-server /v1/embeddings.

        Translates between our simple {"texts":[...]} API and the
        OpenAI-compatible {"input":[...]} format used by llama-server.
        Applies L2 normalisation to match the ONNX backend output.
        """
        try:
            import numpy as np
        except ImportError:
            raise RuntimeError("numpy required for GPU backend. Run: pip install numpy")

        body = json.dumps({"input": texts}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.llama_port}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            raw = urllib.request.urlopen(req, timeout=30).read()
        except Exception as exc:
            raise RuntimeError(f"llama-server /v1/embeddings request failed: {exc}") from exc

        data = json.loads(raw)
        # OpenAI format: {"data": [{"embedding": [...], "index": 0}, ...]}
        # Sort by index to preserve input order
        items = sorted(data["data"], key=lambda x: x["index"])
        vectors = np.array([item["embedding"] for item in items], dtype=np.float32)

        # L2 normalise (llama-server may or may not normalise)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True).clip(min=1e-9)
        return (vectors / norms).tolist()


# ── Model registry ───────────────────────────────────────────────────────────────

_cpu_model: EmbedModel | None = None
_gpu_model: GpuEmbedModel | None = None


def _get_cpu_model(model_dir: pathlib.Path) -> EmbedModel:
    global _cpu_model
    if _cpu_model is None:
        _cpu_model = EmbedModel(model_dir)
    return _cpu_model


# ── HTTP handler ──────────────────────────────────────────────────────────────────


def _make_handler(
    backend: str,
    model_dir: pathlib.Path | None,
    gpu_model: GpuEmbedModel | None,
) -> type[BaseHTTPRequestHandler]:
    model_label = GGUF_REPO if backend == "gpu" else MODEL_REPO

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
            pass  # suppress default access log noise

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            try:
                req = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self._send(400, {"error": "Invalid JSON"})
                return

            if self.path == "/health":
                self._send(200, {
                    "status": "ok",
                    "model": model_label,
                    "dim": MODEL_DIM,
                    "backend": backend,
                })
                return

            if self.path == "/embed":
                texts = req.get("texts", [])
                if not isinstance(texts, list) or not texts:
                    self._send(400, {"error": "texts must be a non-empty list"})
                    return
                try:
                    if backend == "gpu" and gpu_model:
                        vectors = gpu_model.encode(texts)
                    else:
                        vectors = _get_cpu_model(model_dir).encode(texts)
                    self._send(200, {"vectors": vectors})
                except Exception as exc:  # noqa: BLE001
                    self._send(500, {"error": str(exc)})
                return

            self._send(404, {"error": "Not found"})

        def _send(self, code: int, data: dict) -> None:
            payload = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return _Handler


# ── Server entrypoints ────────────────────────────────────────────────────────────


def serve_cpu(port: int, model_dir: pathlib.Path) -> None:
    """Load ONNX model then start the HTTP server (CPU backend)."""
    print(f"[embed-service] Loading model from {model_dir}...", file=sys.stderr)
    try:
        _get_cpu_model(model_dir)
    except RuntimeError as exc:
        print(f"[embed-service] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    handler = _make_handler("cpu", model_dir, None)
    server = HTTPServer(("127.0.0.1", port), handler)
    print(f"[embed-service] Listening on 127.0.0.1:{port}", file=sys.stderr)
    _emit({"type": "embed_service_ready", "port": port, "backend": "cpu"})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_gpu(port: int, model_file: str) -> None:
    """Start llama-server --embedding then proxy requests (GPU backend)."""
    gpu = GpuEmbedModel(model_file)
    try:
        gpu.start()
    except RuntimeError as exc:
        print(f"[embed-service] FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    global _gpu_model
    _gpu_model = gpu

    handler = _make_handler("gpu", None, gpu)
    server = HTTPServer(("127.0.0.1", port), handler)
    print(f"[embed-service] Listening on 127.0.0.1:{port} (GPU proxy → :{_LLAMA_EMBED_PORT})", file=sys.stderr)
    _emit({"type": "embed_service_ready", "port": port, "backend": "gpu"})
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        gpu.stop()


# ── CLI ───────────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="LiteHarness embed service")
    parser.add_argument("--port", type=int, default=7439, help="Port to listen on")
    parser.add_argument(
        "--backend", choices=["cpu", "gpu"], default="cpu",
        help="Embedding backend: cpu (ONNX, default) or gpu (llama-server)",
    )
    # CPU backend args
    parser.add_argument("--model-dir", help="Directory for ONNX model files (cpu backend)")
    parser.add_argument(
        "--download", action="store_true",
        help="Download ONNX model files then exit (emits @@tag:json progress)",
    )
    # GPU backend args
    parser.add_argument("--model-file", help="Path to GGUF model file (gpu backend)")
    parser.add_argument(
        "--download-gguf", action="store_true",
        help="Download GGUF model file then exit (emits @@tag:json progress)",
    )
    parser.add_argument("--gguf-dir", help="Directory to download GGUF model into")

    args = parser.parse_args()

    if args.download:
        if not args.model_dir:
            parser.error("--model-dir is required with --download")
        download_model(pathlib.Path(args.model_dir))

    elif args.download_gguf:
        if not args.gguf_dir:
            parser.error("--gguf-dir is required with --download-gguf")
        download_gguf(pathlib.Path(args.gguf_dir))

    elif args.backend == "gpu":
        if not args.model_file:
            parser.error("--model-file is required for --backend gpu")
        serve_gpu(args.port, args.model_file)

    else:
        if not args.model_dir:
            parser.error("--model-dir is required for --backend cpu")
        serve_cpu(args.port, pathlib.Path(args.model_dir))


if __name__ == "__main__":
    main()

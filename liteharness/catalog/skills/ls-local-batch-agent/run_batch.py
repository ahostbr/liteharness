"""Run evidence-only batch judgments through an isolated, headless LiteTUI seat.

The model has no tools. Evidence commands are restricted to read-only git verbs.
No model load or unload is performed here; the caller owns any approved load.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time


READ_VERBS = {"branch", "cherry", "diff", "log", "rev-list", "show", "status", "worktree"}
FORBIDDEN = {"-c", "--config-env", "--exec-path", "--ext-diff", "--textconv", "--output", "-o"}


def resident_models(lms: str | None = None) -> list[str]:
    lms = lms or shutil.which("lms") or str(Path.home() / ".lmstudio" / "bin" / "lms.exe")
    result = subprocess.run([lms, "ps", "--json"], capture_output=True, text=True, timeout=30, check=True)
    rows = json.loads(result.stdout)
    if not isinstance(rows, list):
        raise ValueError("lms ps --json did not return a list")
    return [row["identifier"] for row in rows if isinstance(row, dict) and isinstance(row.get("identifier"), str)]


def evidence_argv(template: list[str], repo: Path, item: str) -> list[str]:
    if not isinstance(template, list) or not all(isinstance(x, str) for x in template):
        raise ValueError("evidence argv must be a JSON list of strings")
    argv = [x.replace("{repo}", str(repo)).replace("{item}", item) for x in template]
    if len(argv) < 4 or argv[:3] != ["git", "-C", str(repo)] or argv[3] not in READ_VERBS:
        raise ValueError("evidence commands must be git -C {repo} <read-only verb> ...")
    if argv[3] == "worktree" and (len(argv) < 5 or argv[4] != "list"):
        raise ValueError("only git worktree list is allowed")
    if argv[3] == "worktree" and any(arg.startswith("-") and arg not in {"--porcelain", "-z"} for arg in argv[5:]):
        raise ValueError("unsupported git worktree list option")
    if argv[3] == "branch":
        allowed = {"--list", "--merged", "--no-merged", "--all", "--contains", "--no-contains", "--points-at", "--no-color", "-a", "-r"}
        if not any(arg in {"--list", "--merged", "--no-merged"} for arg in argv[4:]) or any(
            arg.startswith("-") and arg not in allowed and not arg.startswith(("--format=", "--sort="))
            for arg in argv[4:]
        ):
            raise ValueError("git branch is restricted to listing options")
    if any(arg in FORBIDDEN or any(arg.startswith(flag + "=") for flag in FORBIDDEN) for arg in argv[4:]):
        raise ValueError("evidence command contains a forbidden option")
    if argv[3] in {"diff", "log", "show"}:
        argv[4:4] = ["--no-ext-diff", "--no-textconv"]
    return argv


def collect_evidence(templates: list[list[str]], repo: Path, item: str) -> str:
    env = dict(os.environ, GIT_PAGER="cat", GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_COUNT="0", GIT_CONFIG_PARAMETERS="",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_EXTERNAL_DIFF="")
    sections = []
    for template in templates:
        argv = evidence_argv(template, repo, item)
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=45, env=env)
        if result.returncode:
            raise RuntimeError(f"evidence failed ({result.returncode}): {argv!r}: {result.stderr[:500]}")
        sections.append(f"$ {' '.join(argv)}\n{result.stdout[:30000]}")
    return "\n\n".join(sections)


def parse_answer(answer: str, required: list[str]) -> dict:
    answer = answer.strip()
    if answer.startswith("```"):
        answer = "\n".join(answer.splitlines()[1:-1]).strip()
    try:
        value = json.loads(answer)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model did not return JSON: {answer[:300]!r}") from exc
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise ValueError(f"model response is missing required keys: {required}")
    return value


def run_litetui(exe: str, model: str, system: str, prompt: str, repo: Path, max_tokens: int,
                timeout: int) -> str:
    with tempfile.TemporaryDirectory(prefix="litetui-batch-") as scratch:
        root = Path(scratch)
        (root / "settings.json").write_text(json.dumps({"tools_enabled": False, "backend": "lmstudio",
                                                       "max_tokens_chat": max_tokens, "max_tokens_tools": max_tokens,
                                                       "tts_enabled": False}), encoding="utf-8")
        env = dict(os.environ, LITETUI_DATA_ROOT=str(root), LITETUI_LM_HOST="http://127.0.0.1:1234")
        cmd = [exe, "--rpc", "--backend", "lmstudio", "--server-mode", "connect",
               "--base-url", "http://127.0.0.1:1234", "--model", model,
               "--cwd", str(repo), "--system-prompt", system, "--max-tokens", str(max_tokens)]
        with (root / "stderr.log").open("w", encoding="utf-8") as stderr:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1, env=env)
            events: queue.Queue[str | None] = queue.Queue()

            def read_stdout() -> None:
                assert proc.stdout is not None
                for line in proc.stdout:
                    events.put(line)
                events.put(None)

            threading.Thread(target=read_stdout, daemon=True).start()
            deadline = time.monotonic() + timeout
            ready = False
            text_parts: list[str] = []
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"LiteTUI turn exceeded {timeout}s")
                    try:
                        line = events.get(timeout=min(remaining, 1))
                    except queue.Empty:
                        if proc.poll() is not None:
                            raise RuntimeError(f"LiteTUI exited {proc.returncode} before turn_end")
                        continue
                    if line is None:
                        raise RuntimeError(f"LiteTUI closed before turn_end (exit {proc.poll()})")
                    event = json.loads(line)
                    kind = event.get("type")
                    if kind == "ready":
                        if (event.get("launch_status") == "blocked" or event.get("backend") != "lmstudio"
                                or event.get("model") != model
                                or not str(event.get("base_url", "")).startswith("http://127.0.0.1:1234")):
                            raise RuntimeError(f"LiteTUI refused requested resident model: {event}")
                        ready = True
                        assert proc.stdin is not None
                        proc.stdin.write(json.dumps({"type": "prompt", "id": "batch-item", "message": prompt}) + "\n")
                        proc.stdin.flush()
                    elif kind == "response" and event.get("id") == "batch-item" and not event.get("ok"):
                        raise RuntimeError(f"LiteTUI refused batch prompt: {event}")
                    elif kind == "text_delta":
                        text_parts.append(event.get("text", ""))
                    elif kind in {"error", "submit_refused", "model_load_requested"}:
                        raise RuntimeError(f"LiteTUI {kind}: {event}")
                    elif kind == "turn_end":
                        if not ready or event.get("stopReason") != "stop":
                            raise RuntimeError(f"LiteTUI did not complete normally: {event}")
                        answer = "".join(text_parts).strip()
                        if not answer:
                            raise RuntimeError("LiteTUI returned no final text (reasoning alone is not an answer)")
                        return answer
            finally:
                if proc.poll() is None:
                    try:
                        assert proc.stdin is not None
                        proc.stdin.write('{"type":"shutdown","id":"batch-shutdown"}\n')
                        proc.stdin.flush()
                        proc.wait(timeout=5)
                    except (BrokenPipeError, subprocess.TimeoutExpired):
                        proc.kill()
                        proc.wait(timeout=5)


def run(manifest_path: Path, output: Path, model: str | None, exe: str, timeout: int) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if output.exists():
        raise ValueError(f"output already exists; choose a fresh path to avoid mistaking stale results for this run: {output}")
    repo = Path(manifest["repo"]).resolve()
    if not repo.is_dir():
        raise ValueError(f"repo does not exist: {repo}")
    models = resident_models()
    if not models:
        raise RuntimeError("No LM Studio model is resident. Ask Sentinel via inbox before loading; this runner will not load one.")
    chosen = model or (models[0] if len(models) == 1 else None)
    if chosen is None or chosen not in models:
        raise ValueError(f"choose an already-resident model with --model; resident: {models}")
    required = manifest["required_keys"]
    if not isinstance(required, list) or not required or not all(isinstance(x, str) for x in required):
        raise ValueError("required_keys must be a nonempty list of strings")
    system = Path(manifest["preamble"]).read_text(encoding="utf-8")
    if system.startswith("---"):
        system = system.split("---", 2)[-1].strip()
    # Windows' process command line is bounded; the prior two branch jobs also
    # used only the leading 5-6k characters of the Holmes method file.
    system = system[:6000]
    system += ("\n\nYou are READ-ONLY. You have no tools. Use only the supplied evidence; do not infer missing facts. "
               "Return exactly one JSON object, no code fence, following this schema: " + manifest["output_schema"])
    results = []
    for entry in manifest["items"]:
        item = entry["id"]
        templates = entry.get("evidence_argv", manifest.get("evidence_argv", []))
        if not templates:
            raise ValueError(f"no evidence commands for {item}")
        evidence = collect_evidence(templates, repo, item)
        prompt = f"TASK: {manifest['task']}\nITEM: {item}\nEVIDENCE:\n{evidence}"
        answer = run_litetui(exe, chosen, system, prompt, repo, manifest.get("max_tokens", 24000), timeout)
        results.append({"item": item, "result": parse_answer(answer, required)})
        print(f"[{len(results)}/{len(manifest['items'])}] {item}: complete", flush=True)
    if set(resident_models()) != set(models):
        raise RuntimeError("resident model set changed during batch; refusing to label output as stable")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                     prefix=output.name + ".", suffix=".tmp", delete=False) as handle:
        json.dump({"model": chosen, "read_only": True, "results": results}, handle, indent=2, ensure_ascii=False)
        temp = Path(handle.name)
    temp.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", help="exact identifier already listed by lms ps --json")
    parser.add_argument("--litetui-exe", default=shutil.which("litetui") or "litetui")
    parser.add_argument("--timeout", type=int, default=1200, help="seconds per item")
    args = parser.parse_args()
    try:
        run(args.manifest, args.output, args.model, args.litetui_exe, args.timeout)
    except (OSError, ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        raise SystemExit(2) from error

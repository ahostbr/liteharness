"""
PTY Daemon — manages ConPTY sessions for spawned Claude Code agents.

Runs as a single background process, listens on a local TCP socket.
Each spawned agent gets its own PTY (via pywinpty on Windows, pty on Unix).
Clients send JSON commands to spawn, send-input, read-output, list, or kill sessions.

Security model:
  - Bearer token generated at startup, stored in lock file (mode 600)
  - Executable whitelist — only allowed CLIs can be spawned
  - Input validation on all fields (agent_id, cwd, text)
  - Max session limit, recv buffer cap, output buffer cap

Protocol: one JSON object per line (newline-delimited).
Request:  {"token": "...", "cmd": "spawn", "agent_id": "...", ...}
Response: {"ok": true, "agent_id": "...", ...} or {"ok": false, "error": "..."}
"""

import json
import os
import platform
import re
import secrets
import shlex
import signal
import socket
import sys
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# 7460, not 7450: LiteImage's FaceSwap FastAPI sidecar owns 7450 on the same box
# (see docs/architecture/00-Ecosystem-Overview.md). Sharing it made `spawn --pty`
# hard-fail whenever face-swap was active, and vice versa.
DAEMON_PORT = 7460
DAEMON_HOST = "127.0.0.1"
LOCK_FILE = Path.home() / ".liteharness" / "pty_daemon.lock"
OUTPUT_BUFFER_SIZE = 10_000
MAX_SESSIONS = 20
MAX_RECV_BYTES = 65_536
MAX_INPUT_LEN = 8192

IS_WINDOWS = platform.system() == "Windows"

# Only these executables may be spawned
ALLOWED_EXECUTABLES = {
    "claude", "claude.exe", "claude.cmd",
    "codex", "codex.exe",
    "python", "python.exe", "python3",
    "cmd", "cmd.exe",
    "bun", "bun.exe",
    "node", "node.exe",
}

ALLOWED_PERM_MODES = {"default", "plan", "auto", "bypassPermissions", "acceptEdits"}

AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")

DANGEROUS_CONTROL_CHARS = {"\x00", "\x1a"}


# ─── Validation ──────────────────────────────────────────────────────────────

def _validate_agent_id(agent_id: str) -> str | None:
    if not agent_id:
        return "agent_id is required"
    if not AGENT_ID_PATTERN.match(agent_id):
        return "agent_id must be alphanumeric/dash/underscore, max 128 chars"
    return None


def _validate_spawn_cmd(cli_cmd: str, cwd: str) -> str | None:
    try:
        parts = shlex.split(cli_cmd, posix=False)
    except ValueError as e:
        return f"malformed command: {e}"

    if not parts:
        return "empty command"

    # Check metacharacters only in the executable and flag names, not in
    # quoted prompt arguments (the last positional arg is typically the prompt).
    for part in parts[:-1]:
        if re.search(r"[;&|`$]", part):
            return "shell metacharacters are not permitted"

    exe = os.path.basename(parts[0].strip('"').strip("'")).lower()
    if exe not in ALLOWED_EXECUTABLES:
        return f"executable '{exe}' is not in the spawn whitelist"

    resolved = Path(cwd).resolve()
    if not resolved.is_dir():
        return f"cwd is not a valid directory"

    if "--permission-mode" in parts:
        idx = parts.index("--permission-mode")
        if idx + 1 < len(parts):
            mode = parts[idx + 1].strip('"').strip("'")
            if mode not in ALLOWED_PERM_MODES:
                return f"permission-mode '{mode}' is not allowed"

    return None


_BLOCKED_COMMANDS = {"/exit", "/exit\r", "/exit\n", "/exit\r\n"}


def _validate_send_input(text: str) -> str | None:
    if len(text) > MAX_INPUT_LEN:
        return f"input exceeds maximum length ({MAX_INPUT_LEN})"
    if any(c in text for c in DANGEROUS_CONTROL_CHARS):
        return "dangerous control character not permitted"
    if text.strip().lower() in _BLOCKED_COMMANDS:
        return "/exit is blocked — use /clear to reset sessions, or pty-kill to terminate"
    return None


def _hide_conhost(child_pid: int) -> None:
    """Hide ONLY the ConHost window belonging to the spawned child (or its tree).

    ConPTY (backend=1) usually creates no visible window at all, so this is
    belt-and-suspenders. The earlier version hid EVERY ConsoleWindowClass window
    on the desktop — including the user's own open cmd.exe — because it never
    compared the window's owning pid to child_pid. Now we hide a console window
    only when its owning process is child_pid or a descendant of it.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32

        # Build the set of pids we own: child_pid + its descendants (best-effort).
        owned_pids = {int(child_pid)}
        try:
            import psutil
            for descendant in psutil.Process(child_pid).children(recursive=True):
                owned_pids.add(descendant.pid)
        except Exception:
            pass

        SW_HIDE = 0
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_cb(hwnd, _lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value == "ConsoleWindowClass" and pid.value in owned_pids:
                found.append(hwnd)
            return True

        user32.EnumWindows(enum_cb, 0)

        for hwnd in found:
            user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass


# ─── PTY Session ─────────────────────────────────────────────────────────────

class PtySession:
    """Wraps a single PTY process."""

    def __init__(self, agent_id: str, cmd: str, cwd: str, name: str | None = None, owner: str | None = None, env: dict[str, str] | None = None):
        self.agent_id = agent_id
        self.name = name
        self.owner = owner
        self.cmd = cmd
        self.cwd = cwd
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.output_buffer: list[str] = []
        self._lock = threading.Lock()
        self._send_queue: queue.Queue[str | None] = queue.Queue()
        self.alive = True
        self.proc = None
        self._pipe_proc = None
        self._reader_thread = None
        self._sender_thread: threading.Thread | None = None

        self._spawn(cmd, cwd, env)

        self._sender_thread = threading.Thread(target=self._send_loop, daemon=True)
        self._sender_thread.start()

    def _spawn(self, cmd: str, cwd: str, env: dict[str, str] | None = None) -> None:
        spawn_env = {**os.environ, **(env or {})}
        if IS_WINDOWS:
            from winpty import PtyProcess
            import shlex, shutil
            # Wrap .cmd/.bat executables so pywinpty doesn't break arg splitting
            parts = shlex.split(cmd, posix=False)
            exe = shutil.which(parts[0]) if parts else None
            if exe and exe.lower().endswith((".cmd", ".bat")):
                cmd = f'cmd /c {cmd}'
            # backend=1 forces ConPTY (invisible pseudoconsole).
            # Default/winpty backend creates visible ConHost windows.
            self.proc = PtyProcess.spawn(cmd, cwd=cwd, env=spawn_env, backend=1)
            self._pipe_proc = self.proc
            _hide_conhost(self.proc.pid)
        else:
            import pty as unix_pty
            import subprocess
            master, slave = unix_pty.openpty()
            self.proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd, env=spawn_env,
                stdin=slave, stdout=slave, stderr=slave,
                close_fds=True, start_new_session=True,
            )
            os.close(slave)
            self._master_fd = master

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self) -> None:
        try:
            while self.alive:
                try:
                    if IS_WINDOWS:
                        data = self.proc.read(4096)
                    else:
                        data = os.read(self._master_fd, 4096).decode("utf-8", errors="replace")

                    if not data:
                        break

                    with self._lock:
                        self.output_buffer.append(data)
                        if len(self.output_buffer) > OUTPUT_BUFFER_SIZE:
                            self.output_buffer = self.output_buffer[-OUTPUT_BUFFER_SIZE:]
                except EOFError:
                    break
                except OSError:
                    break
        finally:
            self.alive = False

    def _send_loop(self) -> None:
        while self.alive:
            try:
                text = self._send_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if text is None:
                break
            try:
                if IS_WINDOWS:
                    self.proc.write(text)
                else:
                    os.write(self._master_fd, text.encode("utf-8"))
            except (OSError, EOFError):
                self.alive = False
                break

    def write(self, text: str) -> bool:
        if not self.alive:
            return False
        try:
            self._send_queue.put(text, timeout=5)
            return True
        except queue.Full:
            return False

    def read_recent(self, lines: int = 50) -> str:
        with self._lock:
            chunks = self.output_buffer[-lines:]
            return "".join(chunks)

    def kill(self) -> None:
        self.alive = False
        try:
            self._send_queue.put_nowait(None)
        except queue.Full:
            pass
        try:
            if IS_WINDOWS and self.proc:
                self.proc.close()
            elif hasattr(self, "_master_fd"):
                os.close(self._master_fd)
                self.proc.terminate()
        except (OSError, ProcessLookupError):
            pass

    def info(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "cmd": self.cmd,
            "cwd": self.cwd,
            "alive": self.alive,
            "started_at": self.started_at,
            "output_lines": len(self.output_buffer),
        }


# ─── Daemon ──────────────────────────────────────────────────────────────────

class PtyDaemon:
    """TCP server managing multiple PTY sessions with bearer token auth."""

    IDLE_TIMEOUT = 2 * 60 * 60  # 2 hours

    def __init__(self, host: str = DAEMON_HOST, port: int = DAEMON_PORT):
        self.host = host
        self.port = port
        self.sessions: dict[str, PtySession] = {}
        self._lock = threading.Lock()
        self.running = False
        self.token = secrets.token_hex(32)
        self._last_activity = time.monotonic()

    def start(self) -> None:
        self.running = True
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)

        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind((self.host, self.port))
        except OSError:
            # Port held. Only treat this as a benign duplicate if a REAL
            # authenticated daemon answers on it — otherwise a foreign process
            # (a sibling service that grabbed the port) must surface as a hard
            # error, not a silent exit-0 that strands `spawn --pty`.
            if is_daemon_running():
                print(f"[pty-daemon] Port {self.port} already served by a live daemon. Exiting.", file=sys.stderr)
                sys.exit(0)
            print(
                f"[pty-daemon] Port {self.port} is held by a non-daemon process. "
                f"Refusing to start; free the port or change DAEMON_PORT.",
                file=sys.stderr,
            )
            sys.exit(1)

        lock_data = json.dumps({
            "token": self.token,
            "port": self.port,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        LOCK_FILE.write_text(lock_data)

        # Restrict lock file permissions (owner-only on Windows via icacls)
        if IS_WINDOWS:
            try:
                import subprocess
                subprocess.run(
                    ["icacls", str(LOCK_FILE), "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME', 'CURRENT_USER')}:(R,W)"],
                    capture_output=True, timeout=5,
                )
            except Exception:
                pass
        else:
            os.chmod(str(LOCK_FILE), 0o600)

        server.listen(10)
        server.settimeout(1.0)

        # Handle SIGTERM for clean shutdown (main thread only)
        try:
            def _sigterm_handler(signum, frame):
                self.running = False
            signal.signal(signal.SIGTERM, _sigterm_handler)
        except ValueError:
            pass

        print(f"[pty-daemon] Listening on {self.host}:{self.port} (token-authenticated)", file=sys.stderr)

        try:
            while self.running:
                try:
                    conn, addr = server.accept()
                    self._last_activity = time.monotonic()
                    threading.Thread(
                        target=self._handle_client, args=(conn,), daemon=True
                    ).start()
                except socket.timeout:
                    if time.monotonic() - self._last_activity > self.IDLE_TIMEOUT:
                        with self._lock:
                            has_alive = any(s.alive for s in self.sessions.values())
                        if not has_alive:
                            print("[pty-daemon] Idle for 2h with no active sessions — shutting down.", file=sys.stderr)
                            self.running = False
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
            self._cleanup()

    def _cleanup(self) -> None:
        with self._lock:
            for session in self.sessions.values():
                session.kill()
            self.sessions.clear()
        LOCK_FILE.unlink(missing_ok=True)

    def _handle_client(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(30.0)
            data = b""
            while b"\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
                if len(data) > MAX_RECV_BYTES:
                    conn.sendall(b'{"ok":false,"error":"request too large"}\n')
                    return

            line = data.split(b"\n", 1)[0]
            request = json.loads(line.decode("utf-8"))

            # Authenticate
            if request.get("token") != self.token:
                conn.sendall(b'{"ok":false,"error":"invalid or missing token"}\n')
                return

            response = self._dispatch(request)
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except Exception:
            try:
                conn.sendall(b'{"ok":false,"error":"internal error"}\n')
            except OSError:
                pass
        finally:
            conn.close()

    def _dispatch(self, req: dict) -> dict:
        cmd = req.get("cmd", "")

        if cmd == "spawn":
            return self._cmd_spawn(req)
        elif cmd == "send-input":
            return self._cmd_send_input(req)
        elif cmd == "read-output":
            return self._cmd_read_output(req)
        elif cmd == "list":
            return self._cmd_list()
        elif cmd == "kill":
            return self._cmd_kill(req)
        elif cmd == "ping":
            return {"ok": True, "sessions": len(self.sessions)}
        elif cmd == "shutdown":
            self.running = False
            return {"ok": True, "message": "shutting down"}
        else:
            return {"ok": False, "error": "unknown command"}

    def _cmd_spawn(self, req: dict) -> dict:
        agent_id = req.get("agent_id", "")
        cli_cmd = req.get("cli_cmd", "claude")
        cwd = req.get("cwd", os.getcwd())
        name = req.get("name")
        env = req.get("env")

        err = _validate_agent_id(agent_id)
        if err:
            return {"ok": False, "error": err}

        err = _validate_spawn_cmd(cli_cmd, cwd)
        if err:
            return {"ok": False, "error": f"spawn blocked: {err}"}

        # Sanitize env: only harness identity namespaces (prevent injection).
        # LITESUITE_* carries spatial identity (project/pane/leaf) — allowing
        # only LITEHARNESS_* silently dropped project_id from every PTY spawn,
        # masked nondeterministically when the daemon itself had inherited the
        # var from whichever agent started it (found 2026-08-06).
        safe_env: dict[str, str] | None = None
        if env and isinstance(env, dict):
            safe_env = {
                k: str(v) for k, v in env.items()
                if k.startswith("LITEHARNESS_") or k.startswith("LITESUITE_")
            }

        with self._lock:
            if len(self.sessions) >= MAX_SESSIONS:
                return {"ok": False, "error": f"max sessions ({MAX_SESSIONS}) reached"}

            if agent_id in self.sessions and self.sessions[agent_id].alive:
                return {"ok": False, "error": f"session {agent_id} already exists"}

            try:
                session = PtySession(agent_id, cli_cmd, str(Path(cwd).resolve()), name, env=safe_env)
                self.sessions[agent_id] = session
                return {"ok": True, "agent_id": agent_id, "name": name}
            except Exception:
                return {"ok": False, "error": "spawn failed — check daemon logs"}

    def _cmd_send_input(self, req: dict) -> dict:
        agent_id = req.get("agent_id", "")
        text = req.get("text", "")

        err = _validate_agent_id(agent_id)
        if err:
            return {"ok": False, "error": err}

        err = _validate_send_input(text)
        if err:
            return {"ok": False, "error": err}

        with self._lock:
            session = self.sessions.get(agent_id)

        if not session:
            return {"ok": False, "error": "session not found"}
        if not session.alive:
            return {"ok": False, "error": "session is dead"}

        # Translate UIAutomation-style special key macros to terminal sequences
        _KEY_MAP = {
            "{ENTER}": "\r",
            "{TAB}": "\t",
            "{BACKSPACE}": "\x7f",
            "{ESCAPE}": "\x1b",
            "{DELETE}": "\x1b[3~",
            "^c": "\x03",
            "^d": "\x04",
            "^z": "\x1a",
            "^l": "\x0c",
        }
        stripped = text.strip()
        if stripped in _KEY_MAP:
            text = _KEY_MAP[stripped]
        elif not text.endswith("\r") and not text.endswith("\n"):
            text += "\r"

        success = session.write(text)
        return {"ok": success, "agent_id": agent_id}

    def _cmd_read_output(self, req: dict) -> dict:
        agent_id = req.get("agent_id", "")
        lines = min(req.get("lines", 50), 1000)

        err = _validate_agent_id(agent_id)
        if err:
            return {"ok": False, "error": err}

        with self._lock:
            session = self.sessions.get(agent_id)

        if not session:
            return {"ok": False, "error": "session not found"}

        output = session.read_recent(lines)
        return {"ok": True, "agent_id": agent_id, "output": output, "alive": session.alive}

    def _cmd_list(self) -> dict:
        with self._lock:
            sessions = [s.info() for s in self.sessions.values()]
        return {"ok": True, "sessions": sessions}

    def _cmd_kill(self, req: dict) -> dict:
        agent_id = req.get("agent_id", "")

        err = _validate_agent_id(agent_id)
        if err:
            return {"ok": False, "error": err}

        with self._lock:
            session = self.sessions.pop(agent_id, None)

        if not session:
            return {"ok": False, "error": "session not found"}

        session.kill()
        return {"ok": True, "agent_id": agent_id}


# ─── Client helper ───────────────────────────────────────────────────────────

def _read_token() -> str | None:
    """Read the daemon token from the lock file."""
    if not LOCK_FILE.exists():
        return None
    try:
        data = json.loads(LOCK_FILE.read_text())
        return data.get("token")
    except (json.JSONDecodeError, OSError):
        return None


def send_command(cmd: dict, host: str = DAEMON_HOST, port: int = DAEMON_PORT) -> dict:
    """Send an authenticated command to the PTY daemon."""
    token = _read_token()
    if not token:
        return {"ok": False, "error": "daemon not running — no lock file found"}

    cmd["token"] = token

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    try:
        sock.connect((host, port))
        sock.sendall((json.dumps(cmd) + "\n").encode("utf-8"))

        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk

        if data:
            return json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
        return {"ok": False, "error": "no response"}
    except ConnectionRefusedError:
        return {"ok": False, "error": "daemon not running — start with: liteharness pty-daemon"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        sock.close()


def is_daemon_running() -> bool:
    """Check if the PTY daemon is running and authenticated."""
    if not LOCK_FILE.exists():
        return False
    try:
        result = send_command({"cmd": "ping"})
        return result.get("ok", False)
    except Exception:
        return False


def _port_in_use(port: int = DAEMON_PORT) -> bool:
    """Check if the daemon port is already bound."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((DAEMON_HOST, port))
        sock.close()
        return False
    except OSError:
        sock.close()
        return True


def ensure_daemon() -> bool:
    """Start the daemon if not running. Returns True if daemon is available."""
    if is_daemon_running():
        return True

    if _port_in_use():
        # Port is held but token doesn't match — lock file is stale/wrong.
        # Rewrite lock won't help; the running daemon owns the port.
        # Try connecting without auth to see if it responds at all.
        if LOCK_FILE.exists():
            LOCK_FILE.unlink(missing_ok=True)
        # Can't start a new daemon while port is occupied — wait for it to die
        for _ in range(10):
            time.sleep(0.5)
            if not _port_in_use():
                break
        else:
            return False

    if LOCK_FILE.exists():
        LOCK_FILE.unlink(missing_ok=True)

    import subprocess
    subprocess.Popen(
        [sys.executable, "-m", "liteharness.pty_daemon"],
        creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if IS_WINDOWS else 0,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    for _ in range(20):
        time.sleep(0.25)
        if is_daemon_running():
            return True
    return False


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    daemon = PtyDaemon()
    daemon.start()

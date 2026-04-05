from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from opencac.agents import InferenceConfig, run_pipeline
from opencac.audit import AuditLog
from opencac.service import FabricRuntime, serve

DEFAULT_TEST_ROLE_PORTS = {
    "antigravity": 18101,
    "claude-code": 18102,
    "codex": 18103,
}

ROLE_URL_ENV = {
    "antigravity": "A2A_ANTIGRAVITY_URL",
    "claude-code": "A2A_CLAUDE_CODE_URL",
    "codex": "A2A_CODEX_URL",
}


def wait_for_port(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.2)
            try:
                sock.connect((host, port))
                return
            except OSError:
                time.sleep(0.05)
    raise TimeoutError(f"port {host}:{port} did not open")


class CallbackRecorder(BaseHTTPRequestHandler):
    records = []
    event = threading.Event()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        self.__class__.records.append(json.loads(raw.decode("utf-8")))
        self.__class__.event.set()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


def start_callback_server(port: int) -> ThreadingHTTPServer:
    CallbackRecorder.records = []
    CallbackRecorder.event = threading.Event()
    server = ThreadingHTTPServer(("127.0.0.1", port), CallbackRecorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_port("127.0.0.1", port)
    return server


def make_local_llm_handler(role: str):
    class LocalLLMHandler(BaseHTTPRequestHandler):
        requests = []

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            self.__class__.requests.append(payload)
            grammar = payload.get("grammar", "")
            match = re.search(r'"([^"]+)"', grammar)
            content = match.group(1) if match else f"{role}_ok"
            body = json.dumps({"content": content}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return LocalLLMHandler


def start_local_llm_server(port: int, role: str):
    handler = make_local_llm_handler(role)
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wait_for_port("127.0.0.1", port)
    return server, handler




class BasePipelineTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._default_role_servers = []
        cls._default_role_env_backup = {name: os.environ.get(name) for name in ROLE_URL_ENV.values()}
        cls._guard_script = Path.home() / ".local" / "bin" / "opencac-private-guard"
        cls._guard_created = not cls._guard_script.exists()
        if cls._guard_created:
            cls._guard_script.parent.mkdir(parents=True, exist_ok=True)
            cls._guard_script.write_text(
                """#!/bin/sh
STATE_FILE="${HOME}/.local/state/opencac-private-guard.state"
mkdir -p "$(dirname "$STATE_FILE")"
case "${1:-status}" in
  enable)
    printf 'enabled\n' > "$STATE_FILE"
    printf 'enabled\n'
    ;;
  disable)
    printf 'disabled\n' > "$STATE_FILE"
    printf 'disabled\n'
    ;;
  status)
    if [ -f "$STATE_FILE" ]; then
      cat "$STATE_FILE"
    else
      printf 'disabled\n'
    fi
    ;;
  *)
    printf 'usage: %s [enable|disable|status]\n' "$0" >&2
    exit 2
    ;;
esac
""",
                encoding="utf-8",
            )
            cls._guard_script.chmod(0o755)
        for role, port in DEFAULT_TEST_ROLE_PORTS.items():
            server, _handler = start_local_llm_server(port, role)
            cls._default_role_servers.append(server)
            os.environ[ROLE_URL_ENV[role]] = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        for server in getattr(cls, "_default_role_servers", []):
            server.shutdown()
            server.server_close()
        if getattr(cls, "_guard_created", False) and getattr(cls, "_guard_script", None):
            cls._guard_script.unlink(missing_ok=True)
        for name, value in getattr(cls, "_default_role_env_backup", {}).items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        super().tearDownClass()

    def setUp(self) -> None:
        guard = Path.home() / ".local" / "bin" / "opencac-private-guard"
        if guard.exists():
            subprocess.run([str(guard), "enable"], check=False, capture_output=True, text=True)

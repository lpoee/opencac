from __future__ import annotations

import json
import ipaddress
import threading
import urllib.request
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .agents import Antigravity, ClaudeCodePlanner, CodexExecutor, InferenceConfig, RoutingConfig, Sidecar, ensure_private_runtime, make_envelope, run_pipeline
from .audit import AuditLog


class FabricRuntime:
    def __init__(self, *, workspace: Path, audit: AuditLog, host: str = "127.0.0.1", port: int = 8000):
        self.workspace = workspace
        self.audit = audit
        self.host = host
        self.port = port
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()

    def run(self, prompt: str, mode: str, inference: Optional[InferenceConfig] = None, callback_url: Optional[str] = None) -> Dict[str, Any]:
        return run_pipeline(prompt=prompt, mode=mode, workspace=self.workspace, audit=self.audit, inference=inference, callback_url=callback_url)

    def _is_loopback_host(self, host: str) -> bool:
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _enforce_private_target(self, mode: str, target_host: str) -> None:
        if mode == "private" and not self._is_loopback_host(target_host):
            raise ValueError(f"private mode requires loopback target, got: {target_host}")

    def _set_job(self, session_id: str, **state: Any) -> None:
        with self._jobs_lock:
            self._jobs.setdefault(session_id, {}).update(state)

    def agent_card(self) -> Dict[str, Any]:
        base = f"http://{self.host}:{self.port}"
        return {
            "name": "opencac",
            "version": "0.1.0",
            "capabilities": {
                "speculative_decoding_required": True,
                "distributed_run": True,
                "distributed_async_run": True,
                "reverse_post_callbacks": True,
                "audit_log": True,
                "resume": True,
                "private_mode_loopback_only": True,
            },
            "agents": [
                {"id": "antigravity", "endpoint": f"{base}/agents/antigravity/message/send"},
                {"id": "claude-code", "endpoint": f"{base}/agents/claude-code/message/send"},
                {"id": "codex", "endpoint": f"{base}/agents/codex/message/send"},
            ],
        }

    def task_status(self, session_id: str) -> Dict[str, Any]:
        entries = self.audit.read(session_id=session_id, last=5000)
        if not entries:
            with self._jobs_lock:
                job = self._jobs.get(session_id)
            if job:
                return {"session_id": session_id, **job}
            return {"session_id": session_id, "status": "not_found", "steps": []}
        exec_entry = next(
            (
                entry
                for entry in reversed(entries)
                if (
                    entry.get("kind") in {"exec_result", "distributed_exec_result"}
                    and isinstance(entry.get("message"), dict)
                )
            ),
            None,
        )
        if exec_entry is None:
            exec_entry = next(
                (
                    entry
                    for entry in reversed(entries)
                    if entry.get("kind") == "agent_http_reply"
                    and isinstance(entry.get("message"), dict)
                    and entry["message"].get("msg_type") == "exec_result"
                ),
                None,
            )
        if exec_entry:
            payload = exec_entry["message"]["payload"]
            return {
                "session_id": session_id,
                "status": payload["status"],
                "steps": payload.get("steps_completed", []),
                "runtime": payload.get("runtime", {}),
            }
        with self._jobs_lock:
            job = self._jobs.get(session_id)
        if job:
            return {"session_id": session_id, **job}
        steps = [entry for entry in entries if entry.get("kind") == "step_result"]
        return {
            "session_id": session_id,
            "status": "in_progress",
            "steps": [
                {
                    "step_id": entry["step_id"],
                    "status": entry["status"],
                    "output": entry.get("output"),
                }
                for entry in steps
            ],
        }

    def _post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"http://{self.host}:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _run_distributed_session(self, session_id: str, prompt: str, mode: str, inference: InferenceConfig, callback_url: Optional[str] = None) -> Dict[str, Any]:
        self._enforce_private_target(mode, self.host)
        if mode == "private":
            self.audit.append({
                "kind": "private_runtime_validated",
                "session_id": session_id,
                "details": ensure_private_runtime(inference),
            })
        dispatcher_msg_id = str(uuid.uuid4())
        payload = {
            "query": prompt,
            "mode": mode,
            "inference": asdict(inference),
        }
        if callback_url:
            payload["callback_url"] = callback_url
        instruction = {
            "msg_id": dispatcher_msg_id,
            "timestamp": "dispatcher",
            "from_agent": "dispatcher",
            "to_agent": "antigravity",
            "msg_type": "research_request",
            "session_id": session_id,
            "payload": payload,
        }
        self.audit.append({"kind": "instruction_created", "session_id": session_id, "message": instruction})
        self.audit.append({"kind": "distributed_dispatch", "session_id": session_id, "target": "antigravity"})
        self._set_job(session_id, status="in_progress", phase="research", steps=[])

        report = self._post_json("/agents/antigravity/message/send", {"message": instruction})["result"]
        self._set_job(session_id, status="in_progress", phase="planning", steps=[])
        plan = self._post_json("/agents/claude-code/message/send", {"message": report})["result"]
        self._set_job(session_id, status="in_progress", phase="critique", steps=[])
        critique = self._post_json("/agents/codex/message/send", {"message": plan})["result"]
        if critique["payload"]["verdict"] != "approve":
            result = {"session_id": session_id, "status": "rejected", "critique": critique, "audit_path": str(self.audit.path)}
            self._set_job(session_id, status="rejected", phase="critique", steps=[])
            return result
        self._set_job(session_id, status="in_progress", phase="execute", steps=[])
        result = self._post_json("/agents/codex/message/send?execute=1", {"message": plan})["result"]
        self.audit.append({"kind": "distributed_exec_result", "session_id": session_id, "message": result})
        self._set_job(
            session_id,
            status=result["payload"]["status"],
            phase="complete",
            steps=result["payload"].get("steps_completed", []),
            runtime=result["payload"].get("runtime", {}),
        )
        return {"session_id": session_id, "status": result["payload"]["status"], "result": result, "audit_path": str(self.audit.path)}

    def run_distributed(self, prompt: str, mode: str, inference: Optional[InferenceConfig] = None, callback_url: Optional[str] = None) -> Dict[str, Any]:
        inference = inference or InferenceConfig()
        session_id = str(uuid.uuid4())
        return self._run_distributed_session(session_id, prompt, mode, inference, callback_url)

    def run_distributed_async(self, prompt: str, mode: str, inference: Optional[InferenceConfig] = None, callback_url: Optional[str] = None) -> Dict[str, Any]:
        inference = inference or InferenceConfig()
        session_id = str(uuid.uuid4())
        self._set_job(session_id, status="accepted", phase="queued", steps=[])
        self.audit.append({"kind": "distributed_async_started", "session_id": session_id, "mode": mode})

        def worker() -> None:
            try:
                self._run_distributed_session(session_id, prompt, mode, inference, callback_url)
            except Exception as exc:
                self.audit.append({"kind": "distributed_error", "session_id": session_id, "error": str(exc)})
                self._set_job(session_id, status="failed", phase="error", error=str(exc), steps=[])

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return {"session_id": session_id, "status": "accepted", "audit_path": str(self.audit.path)}

    def process_agent_message(self, agent_id: str, message: Dict[str, Any], execute: bool = False) -> Dict[str, Any]:
        session_id = message.get("session_id", "http-session")
        inference_data = message.get("payload", {}).get("inference", {})
        inference = InferenceConfig(**inference_data) if isinstance(inference_data, dict) else InferenceConfig()
        mode = message.get("payload", {}).get("mode", "cloud")
        routing = RoutingConfig(mode=mode)
        sidecar = Sidecar(self.audit)

        if agent_id == "antigravity":
            reply = Antigravity(routing, inference, self.workspace).handle(sidecar.forward(message))
        elif agent_id == "claude-code":
            reply = ClaudeCodePlanner(routing, inference, self.workspace).handle(sidecar.forward(message))
        elif agent_id == "codex":
            executor = CodexExecutor(routing, inference, self.workspace, self.audit)
            validated = sidecar.forward(message)
            if execute:
                reply = executor.execute(validated)
            else:
                assessment = executor.assess_plan(validated)
                reply = make_envelope(
                    from_agent="codex",
                    to_agent="claude-code",
                    msg_type="approval" if assessment["verdict"] == "approve" else "rejection",
                    session_id=validated["session_id"],
                    ref_msg_id=validated["msg_id"],
                    payload=assessment,
                )
        else:
            raise ValueError(f"unknown agent: {agent_id}")

        forwarded = sidecar.forward(reply)
        self.audit.append({"kind": "agent_http_reply", "session_id": session_id, "agent": agent_id, "message": forwarded})
        return forwarded


def make_handler(runtime: FabricRuntime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "A2AFabric/0.1"

        def _send(self, status: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/.well-known/agent.json":
                self._send(200, runtime.agent_card())
                return
            if parsed.path == "/health":
                self._send(
                    200,
                    {
                        "status": "ok",
                        "speculative_decoding_required": True,
                        "private_mode_loopback_only": True,
                        "agents": ["antigravity", "claude-code", "codex"],
                        "host": runtime.host,
                        "port": runtime.port,
                    },
                )
                return
            if parsed.path == "/audit":
                query = parse_qs(parsed.query)
                session_id = query.get("session_id", [None])[0]
                last = int(query.get("last", ["20"])[0])
                self._send(200, {"entries": runtime.audit.read(session_id=session_id, last=last)})
                return
            if parsed.path.startswith("/tasks/"):
                session_id = parsed.path.rsplit("/", 1)[-1]
                self._send(200, runtime.task_status(session_id))
                return
            self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            body = self._read_json()
            if parsed.path == "/run":
                try:
                    inference = InferenceConfig(**body.get("inference", {}))
                    callback_url = body.get("callback_url")
                    query = parse_qs(parsed.query)
                    distributed = query.get("distributed", ["0"])[0] == "1"
                    async_run = query.get("async", ["0"])[0] == "1"
                    if distributed and async_run:
                        result = runtime.run_distributed_async(body["prompt"], body.get("mode", "cloud"), inference, callback_url)
                    elif distributed:
                        result = runtime.run_distributed(body["prompt"], body.get("mode", "cloud"), inference, callback_url)
                    else:
                        result = runtime.run(body["prompt"], body.get("mode", "cloud"), inference, callback_url)
                    self._send(200, result)
                except Exception as exc:
                    self._send(400, {"error": str(exc), "error_type": type(exc).__name__})
                return

            if parsed.path.startswith("/agents/") and parsed.path.endswith("/message/send"):
                parts = parsed.path.strip("/").split("/")
                agent_id = parts[1]
                execute = parse_qs(parsed.query).get("execute", ["0"])[0] == "1"
                try:
                    reply = runtime.process_agent_message(agent_id, body["message"], execute=execute)
                    self._send(200, {"result": reply})
                except Exception as exc:
                    self._send(400, {"error": str(exc)})
                return

            self._send(404, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    return Handler


def serve(*, host: str, port: int, workspace: Path, audit: AuditLog) -> None:
    runtime = FabricRuntime(workspace=workspace, audit=audit, host=host, port=port)
    server = ThreadingHTTPServer((host, port), make_handler(runtime))
    try:
        server.serve_forever()
    finally:
        server.server_close()

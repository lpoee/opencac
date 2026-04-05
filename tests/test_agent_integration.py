from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

from .test_support import *


def make_research_service_handler(response_payload: dict):
    """Create an HTTP handler that speaks Antigravity JSON-RPC 2.0."""

    class Handler(BaseHTTPRequestHandler):
        requests: list = []

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
            self.__class__.requests.append(body)
            rpc_id = body.get("id", "1")
            envelope = json.dumps({
                "msg_id": "svc-1",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "antigravity",
                "to_agent": "claude-code",
                "msg_type": "research_report",
                "session_id": body.get("params", {}).get("sessionId", "s"),
                "payload": response_payload,
            })
            result = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "id": "task-1",
                    "sessionId": body.get("params", {}).get("sessionId", "s"),
                    "status": {
                        "state": "completed",
                        "message": {
                            "role": "agent",
                            "parts": [{"type": "text", "text": envelope}],
                        },
                    },
                },
            }
            resp = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


def make_planner_service_handler(plan_json: dict):
    """Create an HTTP handler that speaks Claude Bridge Anthropic messages API."""

    class Handler(BaseHTTPRequestHandler):
        requests: list = []

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw.decode("utf-8"))
            self.__class__.requests.append(body)
            result = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": json.dumps(plan_json)}],
                "model": "claude-sonnet",
            }
            resp = json.dumps(result).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, format: str, *args) -> None:
            return

    return Handler


class AgentIntegrationTests(BasePipelineTestCase):
    def test_antigravity_uses_research_service_when_configured(self) -> None:
        research_payload = {
            "query": "test query",
            "summary": "Gemini found relevant results",
            "findings": [{"title": "Web result", "content": "Found something useful", "confidence": "high", "source_refs": [0]}],
            "sources": [{"url": "https://example.com", "title": "Example"}],
            "model_used": "gemini-2.5-flash",
            "search_queries": ["test query"],
            "stats": {"duration_ms": 150, "tokens_used": 200, "web_searches": 2},
        }
        handler_cls = make_research_service_handler(research_payload)
        handler_cls.requests = []
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        wait_for_port("127.0.0.1", port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
                inference = InferenceConfig(research_service_url=f"http://127.0.0.1:{port}")
                result = run_pipeline(
                    prompt="test query",
                    mode="private",
                    workspace=workspace,
                    audit=audit,
                    inference=inference,
                )
                self.assertEqual(result["status"], "success")
                report = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "research_report")
                self.assertIn("research_service", report["payload"]["stats"])
                self.assertEqual(report["payload"]["summary"], "Gemini found relevant results")
                self.assertGreaterEqual(len(handler_cls.requests), 1)
                rpc_req = handler_cls.requests[0]
                self.assertEqual(rpc_req["method"], "message/send")
        finally:
            server.shutdown()
            server.server_close()

    def test_planner_uses_claude_bridge_when_configured(self) -> None:
        plan_json = {
            "steps": [
                {"id": 1, "action": "create", "description": "Create artifacts dir", "file_path": "artifacts/<session_id>/"},
                {"id": 2, "action": "edit", "description": "Write plan", "file_path": "artifacts/<session_id>/plan.json", "depends_on": [1], "content_template": "plan-json"},
                {"id": 3, "action": "verify", "description": "Summary", "file_path": "artifacts/<session_id>/result.md", "depends_on": [1, 2]},
            ],
            "constraints": ["AI-generated constraint"],
            "acceptance_criteria": ["AI-generated criterion"],
        }
        handler_cls = make_planner_service_handler(plan_json)
        handler_cls.requests = []
        server = ReusableThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        wait_for_port("127.0.0.1", port)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
                inference = InferenceConfig(planner_service_url=f"http://127.0.0.1:{port}")
                result = run_pipeline(
                    prompt="AI planned task",
                    mode="private",
                    workspace=workspace,
                    audit=audit,
                    inference=inference,
                )
                self.assertEqual(result["status"], "success")
                plan = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "plan")
                self.assertEqual(plan["payload"]["planner_backend"]["probe"], "claude-bridge")
                self.assertIn("AI-generated constraint", plan["payload"]["constraints"])
                self.assertGreaterEqual(len(handler_cls.requests), 1)
                req = handler_cls.requests[0]
                self.assertEqual(req["model"], "claude-sonnet")
        finally:
            server.shutdown()
            server.server_close()

    def test_research_service_fallback_on_failure(self) -> None:
        """When research service is unreachable, falls back to local search."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "docs").mkdir()
            (workspace / "docs" / "notes.md").write_text("fallback test content\n", encoding="utf-8")
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(research_service_url="http://127.0.0.1:19999")
            result = run_pipeline(
                prompt="fallback test",
                mode="private",
                workspace=workspace,
                audit=audit,
                inference=inference,
            )
            self.assertEqual(result["status"], "success")
            report = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "research_report")
            self.assertNotIn("research_service", report["payload"].get("stats", {}))

    def test_planner_service_fallback_on_failure(self) -> None:
        """When planner service is unreachable, falls back to template plan."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(planner_service_url="http://127.0.0.1:19999")
            result = run_pipeline(
                prompt="planner fallback test",
                mode="private",
                workspace=workspace,
                audit=audit,
                inference=inference,
            )
            self.assertEqual(result["status"], "success")
            plan = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "plan")
            self.assertNotEqual(plan["payload"].get("planner_backend", {}).get("probe"), "claude-bridge")

    def test_generate_action_fails_gracefully_without_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            plan = {
                "msg_id": "plan-gen",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "claude-code",
                "to_agent": "codex",
                "msg_type": "plan",
                "session_id": "gen-test",
                "payload": {
                    "goal": "generate code",
                    "context": "test",
                    "steps": [
                        {"id": 1, "action": "create", "description": "mkdir", "file_path": "artifacts/<session_id>/"},
                        {"id": 2, "action": "generate", "description": "Write a hello world script", "depends_on": [1]},
                        {"id": 3, "action": "verify", "description": "summary", "file_path": "artifacts/<session_id>/result.md", "depends_on": [1, 2]},
                    ],
                },
            }
            from opencac.agents import CodexExecutor, RoutingConfig

            executor = CodexExecutor(RoutingConfig(mode="private"), InferenceConfig(), workspace, audit)
            result = executor.execute(plan)
            gen_step = next(s for s in result["payload"]["steps_completed"] if s["step_id"] == 2)
            self.assertEqual(gen_step["status"], "failed")
            self.assertIn("codex binary", gen_step["output"])

    def test_generate_action_calls_codex_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            plan = {
                "msg_id": "plan-gen-ok",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "claude-code",
                "to_agent": "codex",
                "msg_type": "plan",
                "session_id": "gen-ok",
                "payload": {
                    "goal": "generate code",
                    "context": "test",
                    "steps": [
                        {"id": 1, "action": "create", "description": "mkdir", "file_path": "artifacts/<session_id>/"},
                        {"id": 2, "action": "generate", "description": "Write hello world", "depends_on": [1]},
                        {"id": 3, "action": "verify", "description": "summary", "file_path": "artifacts/<session_id>/result.md", "depends_on": [1, 2]},
                    ],
                },
            }
            from opencac.agents import CodexExecutor, RoutingConfig

            mock_result = {
                "thread_id": "t-1",
                "messages": ["Generated hello.py"],
                "output": "Generated hello.py",
                "exit_code": 0,
            }
            inference = InferenceConfig(codex_binary="/usr/bin/fake-codex")
            executor = CodexExecutor(RoutingConfig(mode="private"), inference, workspace, audit)
            with mock.patch("opencac.roles._call_codex_exec", return_value=mock_result) as mock_codex:
                result = executor.execute(plan)
                mock_codex.assert_called_once()
            gen_step = next(s for s in result["payload"]["steps_completed"] if s["step_id"] == 2)
            self.assertEqual(gen_step["status"], "done")
            self.assertIn("Generated hello.py", gen_step["output"])

    def test_full_pipeline_with_all_agents(self) -> None:
        """End-to-end: research service + planner service + local execution."""
        research_payload = {
            "query": "full pipeline",
            "summary": "Research complete",
            "findings": [{"title": "Finding", "content": "Useful info", "confidence": "high", "source_refs": [0]}],
            "sources": [],
            "model_used": "gemini",
            "search_queries": ["full pipeline"],
            "stats": {"duration_ms": 100, "tokens_used": 50, "web_searches": 1},
        }
        plan_json = {
            "steps": [
                {"id": 1, "action": "create", "description": "Create dir", "file_path": "artifacts/<session_id>/"},
                {"id": 2, "action": "edit", "description": "Write plan", "file_path": "artifacts/<session_id>/plan.json", "depends_on": [1], "content_template": "plan-json"},
                {"id": 3, "action": "verify", "description": "Summary", "file_path": "artifacts/<session_id>/result.md", "depends_on": [1, 2]},
            ],
            "constraints": ["Full pipeline constraint"],
            "acceptance_criteria": ["All agents used"],
        }

        research_handler = make_research_service_handler(research_payload)
        research_handler.requests = []
        research_server = ReusableThreadingHTTPServer(("127.0.0.1", 0), research_handler)
        research_port = research_server.server_address[1]

        planner_handler = make_planner_service_handler(plan_json)
        planner_handler.requests = []
        planner_server = ReusableThreadingHTTPServer(("127.0.0.1", 0), planner_handler)
        planner_port = planner_server.server_address[1]

        for srv in (research_server, planner_server):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        wait_for_port("127.0.0.1", research_port)
        wait_for_port("127.0.0.1", planner_port)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp)
                audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
                inference = InferenceConfig(
                    research_service_url=f"http://127.0.0.1:{research_port}",
                    planner_service_url=f"http://127.0.0.1:{planner_port}",
                )
                result = run_pipeline(
                    prompt="full pipeline",
                    mode="private",
                    workspace=workspace,
                    audit=audit,
                    inference=inference,
                )
                self.assertEqual(result["status"], "success")
                self.assertGreaterEqual(len(research_handler.requests), 1)
                self.assertGreaterEqual(len(planner_handler.requests), 1)
                report = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "research_report")
                self.assertEqual(report["payload"]["summary"], "Research complete")
                plan = next(e["message"] for e in audit.read(session_id=result["session_id"], last=50) if e["kind"] == "plan")
                self.assertIn("Full pipeline constraint", plan["payload"]["constraints"])
        finally:
            research_server.shutdown()
            research_server.server_close()
            planner_server.shutdown()
            planner_server.server_close()

    def test_parse_plan_json_strips_markdown_fences(self) -> None:
        from opencac.runtime import _parse_plan_json

        raw = '```json\n{"steps": [{"id": 1}]}\n```'
        result = _parse_plan_json(raw)
        self.assertEqual(result["steps"], [{"id": 1}])

    def test_parse_plan_json_handles_plain_json(self) -> None:
        from opencac.runtime import _parse_plan_json

        raw = '{"steps": [{"id": 1}]}'
        result = _parse_plan_json(raw)
        self.assertEqual(result["steps"], [{"id": 1}])

    def test_service_url_resolves_from_config(self) -> None:
        inference = InferenceConfig(research_service_url="http://localhost:18791")
        self.assertEqual(inference.service_url("antigravity"), "http://localhost:18791")
        self.assertIsNone(inference.service_url("claude-code"))

    def test_service_url_resolves_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCAC_RESEARCH_URL": "http://env:18791"}, clear=False):
            inference = InferenceConfig()
            self.assertEqual(inference.service_url("antigravity"), "http://env:18791")

    def test_codex_bin_resolves_from_config(self) -> None:
        inference = InferenceConfig(codex_binary="/usr/bin/codex")
        self.assertEqual(inference.codex_bin(), "/usr/bin/codex")

    def test_codex_bin_resolves_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"OPENCAC_CODEX_BINARY": "/opt/codex"}, clear=False):
            inference = InferenceConfig()
            self.assertEqual(inference.codex_bin(), "/opt/codex")

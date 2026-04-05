from __future__ import annotations

from .test_support import *


class ServiceTests(BasePipelineTestCase):
    def test_http_run_returns_structured_error_when_private_guard_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18780, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18780)

            payload = json.dumps({"prompt": "guard failure", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18780/run", data=payload, headers={"Content-Type": "application/json"})
            with mock.patch("a2a_fabric.runtime.subprocess.run") as run_mock:
                run_mock.return_value = subprocess.CompletedProcess(["a2a-private-guard", "status"], 0, stdout="disabled\n", stderr="")
                with self.assertRaises(Exception) as ctx:
                    urlopen(request, timeout=5)
                body = ctx.exception.read().decode("utf-8")
                data = json.loads(body)
                self.assertEqual(data["error_type"], "RuntimeError")
                self.assertIn("a2a-private-guard guard", data["error"])

    def test_rejection_posts_callback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            callback_server = start_callback_server(18772)
            try:
                runtime = FabricRuntime(workspace=workspace, audit=audit, host="127.0.0.1", port=8000)
                message = {
                    "msg_id": "plan-1",
                    "timestamp": "2026-04-04T00:00:00+00:00",
                    "from_agent": "claude-code",
                    "to_agent": "codex",
                    "msg_type": "plan",
                    "session_id": "callback-reject",
                    "payload": {
                        "goal": "reject dangerous plan",
                        "context": "blocked command",
                        "mode": "private",
                        "callback_url": "http://127.0.0.1:18772/callback",
                        "task": {
                            "protocol": "task/v1",
                            "task_id": "task-callback-reject",
                            "goal": "reject dangerous plan",
                            "steps": [{"id": 1, "action": "run", "description": "danger", "command": "shutdown now"}],
                        },
                        "steps": [{"id": 1, "action": "run", "description": "danger", "command": "shutdown now"}],
                    },
                }
                result = runtime.process_agent_message("codex", message, execute=False)
                self.assertEqual(result["msg_type"], "rejection")
                self.assertTrue(CallbackRecorder.event.wait(2))
                self.assertEqual(CallbackRecorder.records[-1]["msg_type"], "rejection")
                self.assertEqual(CallbackRecorder.records[-1]["payload"]["verdict"], "reject")
            finally:
                callback_server.shutdown()
                callback_server.server_close()

    def test_http_service_distributed_async_run_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18771, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18771)

            payload = json.dumps({"prompt": "distributed async roundtrip", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18771/run?distributed=1&async=1", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(data["status"], "accepted")
            status = None
            for _ in range(40):
                with urlopen(f"http://127.0.0.1:18771/tasks/{data['session_id']}", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                if status["status"] == "success":
                    break
                time.sleep(0.05)

            self.assertIsNotNone(status)
            self.assertEqual(status["status"], "success")
            self.assertTrue(any(entry["kind"] == "distributed_async_started" for entry in audit.read(session_id=data["session_id"], last=100)))

    def test_http_service_run_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18777, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18777)

            payload = json.dumps({"prompt": "http roundtrip", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18777/run", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(data["status"], "success")
            self.assertEqual(data["result"]["payload"]["runtime"]["speculative"], True)

    def test_http_service_distributed_run_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18776, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18776)

            payload = json.dumps({"prompt": "distributed http roundtrip", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18776/run?distributed=1", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(data["status"], "success")
            entries = audit.read(session_id=data["session_id"], last=100)
            kinds = [entry["kind"] for entry in entries]
            self.assertIn("distributed_dispatch", kinds)
            self.assertIn("agent_http_reply", kinds)

    def test_http_service_agent_card_and_task_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18775, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18775)

            with urlopen("http://127.0.0.1:18775/.well-known/agent.json", timeout=5) as response:
                card = json.loads(response.read().decode("utf-8"))
            self.assertEqual(card["name"], "opencac")
            self.assertTrue(card["capabilities"]["speculative_decoding_required"])

            payload = json.dumps({"prompt": "task status endpoint", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18775/run?distributed=1", data=payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                run_result = json.loads(response.read().decode("utf-8"))

            status = None
            for _ in range(20):
                with urlopen(f"http://127.0.0.1:18775/tasks/{run_result['session_id']}", timeout=5) as response:
                    status = json.loads(response.read().decode("utf-8"))
                if status["status"] == "success":
                    break
                time.sleep(0.05)

            self.assertIsNotNone(status)
            self.assertEqual(status["status"], "success")
            self.assertTrue(status["runtime"]["speculative"])
            self.assertTrue(any(entry["kind"] == "private_runtime_validated" for entry in audit.read(session_id=run_result["session_id"], last=100)))

    def test_private_mode_rejects_non_loopback_distributed_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            runtime = FabricRuntime(workspace=workspace, audit=audit, host="100.67.207.51", port=8000)
            with self.assertRaises(ValueError):
                runtime.run_distributed("must stay local", "private", InferenceConfig())

    def test_http_service_agent_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18779, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18779)

            message = {
                "msg_id": "1",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "dispatcher",
                "to_agent": "antigravity",
                "msg_type": "research_request",
                "session_id": "http-agent-test",
                "payload": {
                    "query": "service endpoint test",
                    "mode": "private",
                    "inference": {
                        "engine": "llama.cpp",
                        "model": "gpt-oss:20b",
                        "speculative": True,
                        "speculative_mode": "auto",
                        "draft_model": None,
                        "spec_type": "ngram-simple",
                        "draft_max": 64,
                        "draft_min": 16,
                        "spec_ngram_size_n": 12,
                        "spec_ngram_size_m": 48,
                        "spec_ngram_min_hits": 1,
                    },
                },
            }
            payload = json.dumps({"message": message}).encode("utf-8")
            request = Request(
                "http://127.0.0.1:18779/agents/antigravity/message/send",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))

            self.assertEqual(data["result"]["msg_type"], "research_report")
            self.assertEqual(data["result"]["from_agent"], "antigravity")

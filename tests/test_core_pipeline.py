from __future__ import annotations

from .test_support import *


class CorePipelineTests(BasePipelineTestCase):
    def test_plan_rejects_shell_control_operators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            plan = {
                "msg_id": "plan-shell-reject",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "claude-code",
                "to_agent": "codex",
                "msg_type": "plan",
                "session_id": "shell-reject",
                "payload": {
                    "goal": "reject shell chaining",
                    "context": "unsafe shell operator",
                    "steps": [
                        {"id": 1, "action": "run", "description": "unsafe", "command": "echo ok && whoami"},
                    ],
                },
            }
            from opencac.agents import CodexExecutor, RoutingConfig

            executor = CodexExecutor(RoutingConfig(mode="private"), InferenceConfig(), workspace, audit)
            assessment = executor.assess_plan(plan)
            self.assertEqual(assessment["verdict"], "reject")
            self.assertTrue(any("shell control operators" in issue["description"] for issue in assessment["issues"]))

    def test_cloud_mode_falls_back_to_local_shards_when_cloud_tokens_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            servers = []
            try:
                antigravity_server, antigravity_handler = start_local_llm_server(18891, "antigravity")
                claude_server, claude_handler = start_local_llm_server(18892, "claude-code")
                codex_server, codex_handler = start_local_llm_server(18893, "codex")
                servers = [antigravity_server, claude_server, codex_server]
                inference = InferenceConfig()
                mapping = {
                    "antigravity": "http://127.0.0.1:18891",
                    "claude-code": "http://127.0.0.1:18892",
                    "codex": "http://127.0.0.1:18893",
                }
                with mock.patch.dict(os.environ, {
                    "A2A_CLOUD_FALLBACK_LOCAL": "1",
                    "A2A_ANTIGRAVITY_TOKEN": "",
                    "A2A_CLAUDE_CODE_TOKEN": "",
                    "A2A_CODEX_TOKEN": "",
                    "A2A_ANTIGRAVITY_URL": mapping["antigravity"],
                    "A2A_CLAUDE_CODE_URL": mapping["claude-code"],
                    "A2A_CODEX_URL": mapping["codex"],
                }, clear=False), mock.patch("opencac.runtime._default_role_url", side_effect=lambda role: mapping[role]):
                    result = run_pipeline(
                        prompt="cloud fallback run",
                        mode="cloud",
                        workspace=workspace,
                        audit=audit,
                        inference=inference,
                    )
                self.assertEqual(result["status"], "success")
                self.assertGreaterEqual(len(antigravity_handler.requests), 1)
                self.assertGreaterEqual(len(claude_handler.requests), 1)
                self.assertGreaterEqual(len(codex_handler.requests), 2)
                report = next(entry["message"] for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "research_report")
                self.assertEqual(report["payload"]["model_used"], "local-fallback-rag")
                plan = next(entry["message"] for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "plan")
                self.assertEqual(plan["payload"]["planner_backend"]["probe"], "claude_code_ok")
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()

    def test_pipeline_writes_audit_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            result = run_pipeline(
                prompt="build a private-safe opencac execution chain",
                mode="private",
                workspace=workspace,
                audit=audit,
            )

            self.assertEqual(result["status"], "success")
            self.assertTrue(Path(result["audit_path"]).exists())

            entries = audit.read(session_id=result["session_id"], last=50)
            kinds = [entry["kind"] for entry in entries]
            self.assertIn("instruction_created", kinds)
            self.assertIn("private_runtime_validated", kinds)
            self.assertIn("research_report", kinds)
            self.assertIn("plan", kinds)
            self.assertIn("research_assessment", kinds)
            self.assertIn("plan_assessment", kinds)
            self.assertIn("implementation_assessment", kinds)
            self.assertIn("exec_result", kinds)

            session_dir = workspace / "artifacts" / result["session_id"]
            self.assertTrue((session_dir / "plan.json").exists())
            self.assertTrue((session_dir / "result.md").exists())

            plan = json.loads((session_dir / "plan.json").read_text(encoding="utf-8"))
            summary = (session_dir / "result.md").read_text(encoding="utf-8")
            self.assertEqual(plan["goal"], "build a private-safe opencac execution chain")
            self.assertEqual(result["result"]["payload"]["strategy"], "self-speculative")
            self.assertIn("--spec-type ngram-simple", result["result"]["payload"]["runtime_command"])
            self.assertIn("- Step 3: done", summary)

    def test_private_research_scans_docs_and_source_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "docs").mkdir()
            (workspace / "src").mkdir()
            (workspace / "docs" / "notes.md").write_text("speculative decoding keeps token throughput high\n", encoding="utf-8")
            (workspace / "src" / "worker.py").write_text("SPECULATIVE_MODE = 'enabled'\n", encoding="utf-8")
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")

            result = run_pipeline(
                prompt="speculative decoding",
                mode="private",
                workspace=workspace,
                audit=audit,
            )

            self.assertEqual(result["status"], "success")
            report = next(entry["message"] for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "research_report")
            titles = [finding["title"] for finding in report["payload"]["findings"]]
            self.assertTrue(any(title.startswith("Local docs: ") for title in titles))
            self.assertTrue(any(title.startswith("Repository code: ") for title in titles))
            self.assertGreater(report["payload"]["stats"]["docs_scanned"], 0)
            self.assertGreater(report["payload"]["stats"]["code_scanned"], 0)

    def test_private_pipeline_uses_local_llm_shards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            servers = []
            try:
                antigravity_server, antigravity_handler = start_local_llm_server(18761, "antigravity")
                claude_server, claude_handler = start_local_llm_server(18762, "claude-code")
                codex_server, codex_handler = start_local_llm_server(18763, "codex")
                servers = [antigravity_server, claude_server, codex_server]
                inference = InferenceConfig(
                    antigravity_url="http://127.0.0.1:18761",
                    claude_code_url="http://127.0.0.1:18762",
                    codex_url="http://127.0.0.1:18763",
                )
                result = run_pipeline(
                    prompt="use local llm shards",
                    mode="private",
                    workspace=workspace,
                    audit=audit,
                    inference=inference,
                )
                self.assertEqual(result["status"], "success")
                self.assertGreaterEqual(len(antigravity_handler.requests), 1)
                self.assertGreaterEqual(len(claude_handler.requests), 1)
                self.assertGreaterEqual(len(codex_handler.requests), 2)
                report = next(entry["message"] for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "research_report")
                self.assertEqual(report["payload"]["stats"]["local_llm_probe"], "antigravity_ok")
                plan = next(entry["message"] for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "plan")
                self.assertEqual(plan["payload"]["planner_backend"]["probe"], "claude_code_ok")
                exec_result = result["result"]
                self.assertEqual(exec_result["payload"]["backend_probe"]["probe"], "codex_ok")
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()

    def test_private_pipeline_fails_when_local_llm_is_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(
                antigravity_url="http://127.0.0.1:18764",
                claude_code_url="http://127.0.0.1:18765",
                codex_url="http://127.0.0.1:18766",
            )
            with self.assertRaises(RuntimeError):
                run_pipeline(
                    prompt="local llm must be reachable",
                    mode="private",
                    workspace=workspace,
                    audit=audit,
                    inference=inference,
                )

    def test_private_runtime_validation_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            result = run_pipeline(
                prompt="validate private runtime",
                mode="private",
                workspace=workspace,
                audit=audit,
            )
            self.assertEqual(result["status"], "success")
            entry = next(entry for entry in audit.read(session_id=result["session_id"], last=50) if entry["kind"] == "private_runtime_validated")
            self.assertEqual(entry["details"]["private_guard"], "enabled")
            self.assertEqual(entry["details"]["role_urls"]["codex"], "http://127.0.0.1:18103")

    def test_private_runtime_requires_private_guard(self) -> None:
        from opencac.agents import ensure_private_runtime

        inference = InferenceConfig()
        with mock.patch("opencac.runtime.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(["opencac-private-guard", "status"], 0, stdout="disabled\n", stderr="")
            with self.assertRaises(RuntimeError):
                ensure_private_runtime(inference)

    def test_run_step_executes_command_and_records_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            plan = {
                "msg_id": "plan-run",
                "timestamp": "2026-04-05T00:00:00+00:00",
                "from_agent": "claude-code",
                "to_agent": "codex",
                "msg_type": "plan",
                "session_id": "run-step",
                "payload": {
                    "goal": "execute a command",
                    "context": "test",
                    "steps": [
                        {"id": 1, "action": "create", "description": "mkdir", "file_path": "artifacts/<session_id>/"},
                        {"id": 2, "action": "run", "description": "write a file", "command": 'python3 -c \'from pathlib import Path; Path("ran.txt").write_text("ok", encoding="utf-8")\'', "depends_on": [1]},
                        {"id": 3, "action": "verify", "description": "summary", "file_path": "artifacts/<session_id>/result.md", "depends_on": [1, 2]},
                    ],
                },
            }
            from opencac.agents import CodexExecutor, RoutingConfig

            executor = CodexExecutor(RoutingConfig(mode="private"), InferenceConfig(), workspace, audit)
            result = executor.execute(plan)
            self.assertEqual(result["payload"]["status"], "success")
            self.assertEqual((workspace / "ran.txt").read_text(encoding="utf-8"), "ok")
            log_path = workspace / "artifacts" / "run-step" / "logs" / "step-2.log"
            self.assertTrue(log_path.exists())
            self.assertIn("python3 -c", log_path.read_text(encoding="utf-8"))

    def test_auto_mode_prefers_quality_and_falls_back_to_self_speculative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(
                engine="llama.cpp",
                model="gpt-oss:20b",
                speculative=True,
                speculative_mode="auto",
            )
            result = run_pipeline(
                prompt="choose the highest quality speculation mode",
                mode="private",
                workspace=workspace,
                audit=audit,
                inference=inference,
            )
            self.assertEqual(result["result"]["payload"]["strategy"], "self-speculative")
            self.assertNotIn("--draft-model", result["result"]["payload"]["runtime_command"])

    def test_llamacpp_speculative_command_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(
                engine="llama.cpp",
                model="gpt-oss:20b",
                speculative=True,
                spec_type="ngram-simple",
                draft_max=64,
                draft_min=16,
            )
            result = run_pipeline(
                prompt="enable speculative decoding",
                mode="private",
                workspace=workspace,
                audit=audit,
                inference=inference,
            )
            runtime_command = result["result"]["payload"]["runtime_command"]
            self.assertIn("llama-server", runtime_command)
            self.assertIn("-m gpt-oss:20b", runtime_command)
            self.assertIn("--spec-type ngram-simple", runtime_command)
            self.assertIn("--draft-max 64", runtime_command)

    def test_draft_model_switches_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            inference = InferenceConfig(
                engine="llama.cpp",
                model="gpt-oss:20b",
                speculative=True,
                speculative_mode="draft-model",
                draft_model="gpt-oss:small-draft",
            )
            result = run_pipeline(
                prompt="use explicit draft model",
                mode="private",
                workspace=workspace,
                audit=audit,
                inference=inference,
            )
            self.assertEqual(result["result"]["payload"]["strategy"], "draft-model")
            self.assertIn("--draft-model gpt-oss:small-draft", result["result"]["payload"]["runtime_command"])

    def test_speculative_is_forced_even_if_disabled_in_config(self) -> None:
        config = InferenceConfig(speculative=False)
        self.assertTrue(config.speculative)

    def test_resume_replays_from_logged_plan(self) -> None:
        from opencac.agents import resume_pipeline

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".opencac" / "audit.jsonl")
            first = run_pipeline(
                prompt="resume capable session",
                mode="cloud",
                workspace=workspace,
                audit=audit,
            )

            resumed = resume_pipeline(
                session_id=first["session_id"],
                workspace=workspace,
                audit=audit,
            )
            self.assertTrue(resumed["resumed"])
            self.assertEqual(resumed["status"], "success")

    def test_sidecar_rejects_non_protocol_text(self) -> None:
        from opencac.agents import Sidecar

        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(Path(tmp) / ".opencac" / "audit.jsonl")
            sidecar = Sidecar(audit)
            rejection = sidecar.reject(
                "plain text that is not json",
                from_agent="dispatcher",
                to_agent="sidecar",
                session_id="bad-input",
                reason="Expecting value",
            )
            self.assertEqual(rejection["status_code"], 400)
            self.assertEqual(audit.read(session_id="bad-input", last=1)[0]["kind"], "sidecar_reject")

    def test_audit_log_append_is_thread_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = AuditLog(Path(tmp) / ".opencac" / "audit.jsonl")

            def writer(start: int) -> None:
                for offset in range(50):
                    audit.append({"kind": "thread-write", "session_id": "thread-safe", "seq": start + offset})

            threads = [threading.Thread(target=writer, args=(index * 50,)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            entries = audit.read(session_id="thread-safe", last=500)
            self.assertEqual(len(entries), 200)
            self.assertEqual({entry["seq"] for entry in entries}, set(range(200)))

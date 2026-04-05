from __future__ import annotations

from .test_support import *


class CLITests(BasePipelineTestCase):
    def test_cli_discover_and_task_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18774, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18774)

            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC)
            discover = subprocess.run(
                ["python3", "-m", "a2a_fabric.cli", "discover", "--base-url", "http://127.0.0.1:18774"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            discover_payload = json.loads(discover.stdout)
            self.assertEqual(discover_payload["name"], "opencac")

            run_payload = json.dumps({"prompt": "cli task get", "mode": "private"}).encode("utf-8")
            request = Request("http://127.0.0.1:18774/run?distributed=1", data=run_payload, headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=5) as response:
                run_result = json.loads(response.read().decode("utf-8"))

            task_get = subprocess.run(
                ["python3", "-m", "a2a_fabric.cli", "task-get", run_result["session_id"], "--base-url", "http://127.0.0.1:18774"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            task_payload = json.loads(task_get.stdout)
            self.assertEqual(task_payload["status"], "success")

    def test_cli_run_distributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18773, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18773)

            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC)
            run_cmd = subprocess.run(
                [
                    "python3",
                    "-m",
                    "a2a_fabric.cli",
                    "run",
                    "distributed cli run",
                    "--mode",
                    "private",
                    "--distributed",
                    "--base-url",
                    "http://127.0.0.1:18773",
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
            run_payload = json.loads(run_cmd.stdout)
            self.assertEqual(run_payload["status"], "success")
            self.assertEqual(run_payload["result"]["payload"]["runtime"]["speculative"], True)

    def test_cli_run_distributed_rejects_non_loopback_in_private_mode(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        run_cmd = subprocess.run(
            [
                "python3",
                "-m",
                "a2a_fabric.cli",
                "run",
                "bad target",
                "--mode",
                "private",
                "--distributed",
                "--base-url",
                "http://100.67.207.51:8000",
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(run_cmd.returncode, 0)
        self.assertIn("private mode requires loopback base URL", run_cmd.stderr)


    def test_cli_enters_interactive_mode_without_subcommand(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            ["python3", "-m", "a2a_fabric.cli"],
            input="/exit\n",
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        self.assertIn("OpenCAC interactive mode", proc.stdout)
        self.assertIn("opencac>", proc.stdout)
        self.assertIn("bye", proc.stdout)

    def test_cli_interactive_runs_distributed_private_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            audit = AuditLog(workspace / ".a2a" / "audit.jsonl")
            thread = threading.Thread(
                target=serve,
                kwargs={"host": "127.0.0.1", "port": 18782, "workspace": workspace, "audit": audit},
                daemon=True,
            )
            thread.start()
            wait_for_port("127.0.0.1", 18782)

            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC)
            proc = subprocess.run(
                ["python3", "-m", "a2a_fabric.cli"],
                input="/base-url http://127.0.0.1:18782\ninteractive private run\n/exit\n",
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            self.assertIn("answer:", proc.stdout)
            self.assertIn("process:", proc.stdout)
            self.assertIn("status: success", proc.stdout)
            self.assertIn("artifacts:", proc.stdout)
            self.assertIn("audit:", proc.stdout)

    def test_cli_interactive_answers_question_without_task_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            servers = []
            try:
                codex_server, codex_handler = start_local_llm_server(18783, "codex")
                servers = [codex_server]
                env = os.environ.copy()
                env["PYTHONPATH"] = str(SRC)
                env["A2A_CODEX_URL"] = "http://127.0.0.1:18783"
                proc = subprocess.run(
                    ["python3", "-m", "a2a_fabric.cli"],
                    input=f"/workspace {workspace}\n你是谁？\n/exit\n",
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )
                self.assertIn("answer: codex_ok", proc.stdout)
                self.assertIn("process: answer", proc.stdout)
                self.assertIn("status: success", proc.stdout)
                self.assertIn("audit:", proc.stdout)
                self.assertNotIn("artifacts:", proc.stdout)
                self.assertGreaterEqual(len(codex_handler.requests), 1)
                entries = AuditLog(workspace / ".a2a" / "audit.jsonl").read(last=10)
                kinds = [entry["kind"] for entry in entries]
                self.assertIn("question_received", kinds)
                self.assertIn("question_answered", kinds)
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()

    def test_question_research_heuristic_only_triggers_for_evidence_requests(self) -> None:
        from a2a_fabric.cli import _question_needs_research

        self.assertFalse(_question_needs_research("你是谁？"))
        self.assertFalse(_question_needs_research("how do you think about this architecture?"))
        self.assertTrue(_question_needs_research("看一下当前仓库里的测试报错"))
        self.assertTrue(_question_needs_research("what do the docs say about this API?"))

    def test_cli_interactive_can_toggle_json_output(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            ["python3", "-m", "a2a_fabric.cli"],
            input="/json on\n/exit\n",
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        self.assertIn("json=on", proc.stdout)

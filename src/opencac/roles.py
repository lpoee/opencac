from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .audit import AuditLog
from .runtime import (
    InferenceConfig,
    RoutingConfig,
    _contains_blocked_token,
    _parse_command,
    _post_callback,
    _probe_local_llm,
    _safe_rel_path,
    _search_lines,
    _workspace_test_command,
    make_envelope,
)

class Antigravity:
    def __init__(self, routing: RoutingConfig, inference: InferenceConfig, workspace: Path):
        self.routing = routing
        self.inference = inference
        self.workspace = workspace

    def _local_findings(self, query: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        docs_findings, docs_scanned = _search_lines(self.workspace / "docs", self.workspace, query, "Local docs")
        code_findings, code_scanned = _search_lines(self.workspace / "src", self.workspace, query, "Repository code")
        findings = docs_findings + code_findings
        if not findings:
            findings = [
                {
                    "title": "Local workspace scan",
                    "content": "No direct docs or code hit found; planner should rely on repository structure and execute conservatively.",
                    "confidence": "medium",
                    "source_refs": [0],
                }
            ]
        return findings[:6], {
            "duration_ms": 1,
            "tokens_used": 0,
            "web_searches": 0,
            "docs_scanned": docs_scanned,
            "code_scanned": code_scanned,
            "local_hits": len(findings),
        }

    def _sources(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sources = []
        for finding in findings:
            title = finding["title"]
            if ": " in title and title.split(": ", 1)[0] in {"Local docs", "Repository code"}:
                rel_with_line = title.split(": ", 1)[1]
                rel_path = rel_with_line.rsplit(":", 1)[0]
                sources.append({"url": f"file://{self.workspace / rel_path}", "title": title})
            else:
                sources.append({"url": "opencac://workspace/scan", "title": title})
        return sources

    def handle(self, instruction: Dict[str, Any]) -> Dict[str, Any]:
        payload = instruction["payload"]
        query = payload.get("query") or payload["prompt"]
        provider = self.routing.provider_map["antigravity"]
        callback_url = payload.get("callback_url")
        findings, stats = self._local_findings(query)
        local_llm = None
        role_url = self.inference.role_url("antigravity", self.routing.mode)
        if role_url:
            local_llm = _probe_local_llm(self.routing.mode, role_url, "antigravity")
            stats["local_llm_endpoint"] = local_llm["endpoint"]
            stats["local_llm_probe"] = local_llm["probe"]
        if self.routing.mode == "cloud":
            findings = [
                {
                    "title": "Cloud route placeholder",
                    "content": "Cloud mode is selected; research remains deterministic while preserving the configured cloud routing metadata.",
                    "confidence": "medium",
                    "source_refs": [0],
                },
                *findings,
            ][:6]
            stats["web_searches"] = 1
        sources = self._sources(findings)
        summary = f"Research synthesized for: {query}"
        if local_llm:
            summary = f"{summary} via {local_llm['probe']}"
        report_payload = {
            "query": query,
            "summary": summary,
            "findings": findings,
            "sources": sources,
            "model_used": provider,
            "search_queries": [query],
            "stats": stats,
            "research_assessment": {
                "target": "user",
                "summary": "Research normalized the user request into a search query and surfaced the strongest local evidence available.",
                "gaps": [] if findings else ["No direct local evidence matched the request."],
                "assumptions": ["The latest user input is the authoritative task statement."],
            },
        }
        if callback_url:
            report_payload["callback_url"] = callback_url
        return make_envelope(
            from_agent="antigravity",
            to_agent="claude-code",
            msg_type="research_report",
            session_id=instruction["session_id"],
            ref_msg_id=instruction["msg_id"],
            payload=report_payload,
        )


class ClaudeCodePlanner:
    def __init__(self, routing: RoutingConfig, inference: InferenceConfig, workspace: Path):
        self.routing = routing
        self.inference = inference
        self.workspace = workspace

    def handle(self, report: Dict[str, Any]) -> Dict[str, Any]:
        payload = report["payload"]
        goal = payload["query"]
        context = payload["summary"]
        callback_url = payload.get("callback_url")
        planner_llm = None
        role_url = self.inference.role_url("claude-code", self.routing.mode)
        if role_url:
            planner_llm = _probe_local_llm(self.routing.mode, role_url, "claude-code")
            context = f"{context} | planner={planner_llm['probe']}"
        steps: List[Dict[str, Any]] = [
            {
                "id": 1,
                "action": "create",
                "description": "Create a session artifact directory for outputs",
                "file_path": "artifacts/<session_id>/",
            },
            {
                "id": 2,
                "action": "edit",
                "description": "Write normalized plan payload for replay and inspection",
                "file_path": "artifacts/<session_id>/plan.json",
                "depends_on": [1],
                "content_template": "plan-json",
            },
        ]

        test_cmd = _workspace_test_command(self.workspace)
        next_id = 3
        if test_cmd:
            steps.append(
                {
                    "id": next_id,
                    "action": "test",
                    "description": "Run repository test suite discovered by the planner",
                    "command": test_cmd,
                    "depends_on": [2],
                }
            )
            next_id += 1

        steps.append(
            {
                "id": next_id,
                "action": "verify",
                "description": "Persist an execution summary describing the chosen routing mode and runtime command",
                "file_path": "artifacts/<session_id>/result.md",
                "depends_on": [step["id"] for step in steps],
                "content_template": "result-summary",
            }
        )

        constraints = [
            "Every hop must survive sidecar validation",
            "Audit JSONL is the single source of truth",
            f"Planner provider: {self.routing.provider_map['claude-code']}",
            f"Executor engine: {self.inference.engine}",
        ]
        acceptance_criteria = [
            "instruction is recorded before downstream work",
            "research, research_assessment, plan, plan_assessment, and exec_result are all audited",
            "private mode routes all providers through loopback shard endpoints with the private guard enabled",
            "execution artifacts capture the runtime command for local serving",
            "rejections can trigger reverse POST callbacks when configured",
        ]
        plan_payload = {
            "goal": goal,
            "context": context,
            "constraints": constraints,
            "acceptance_criteria": acceptance_criteria,
            "plan_assessment": {
                "target": "researcher",
                "summary": "Planner converted research into executable steps and compensated for any missing detail with conservative defaults.",
                "gaps": [] if payload.get("findings") else ["No concrete findings were available to plan against."],
                "assumptions": ["The research summary is sufficient to derive a safe first execution plan."],
            },
            "task": {
                "protocol": "task/v1",
                "task_id": f"task-{report['session_id']}",
                "goal": goal,
                "steps": steps,
                "constraints": constraints,
                "acceptance_criteria": acceptance_criteria,
            },
            "steps": steps,
        }
        if planner_llm:
            plan_payload["planner_backend"] = planner_llm
        if callback_url:
            plan_payload["callback_url"] = callback_url
        return make_envelope(
            from_agent="claude-code",
            to_agent="codex",
            msg_type="plan",
            session_id=report["session_id"],
            ref_msg_id=report["msg_id"],
            payload=plan_payload,
        )


class CodexExecutor:
    def __init__(self, routing: RoutingConfig, inference: InferenceConfig, workspace: Path, audit: AuditLog):
        self.routing = routing
        self.inference = inference
        self.workspace = workspace
        self.audit = audit

    def _session_dir(self, session_id: str) -> Path:
        path = self.workspace / "artifacts" / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _resolve_path(self, session_id: str, template: str) -> Path:
        rendered = template.replace("<session_id>", session_id)
        return (self.workspace / rendered).resolve()

    def _completed_step_ids(self, session_id: str) -> set[int]:
        completed = set()
        for entry in self.audit.read(session_id=session_id, last=1000):
            if entry.get("kind") == "step_result" and entry.get("status") == "done":
                completed.add(entry["step_id"])
        return completed

    def assess_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        steps = plan["payload"].get("steps", [])
        issues: List[Dict[str, Any]] = []
        verdict = "approve"
        if not steps:
            verdict = "reject"
            issues.append({"severity": "critical", "description": "plan has no executable steps"})
        for step in steps:
            command = step.get("command")
            if command and _contains_blocked_token(command):
                verdict = "reject"
                issues.append(
                    {
                        "severity": "critical",
                        "step_id": step.get("id"),
                        "description": f"blocked command in step: {command}",
                    }
                )
            if command:
                try:
                    _parse_command(command)
                except ValueError as exc:
                    verdict = "reject"
                    issues.append(
                        {
                            "severity": "critical",
                            "step_id": step.get("id"),
                            "description": str(exc),
                        }
                    )
        role_url = self.inference.role_url("codex", self.routing.mode)
        backend_probe = _probe_local_llm(self.routing.mode, role_url, "codex") if role_url else None
        assessment = {
            "target": "planner",
            "verdict": verdict,
            "issues": issues,
            "summary": "implementation can proceed" if verdict == "approve" else "implementation blocked by plan issues",
            "backend_probe": backend_probe,
        }
        callback_url = plan["payload"].get("callback_url")
        if callback_url and verdict != "approve":
            rejection = make_envelope(
                from_agent="codex",
                to_agent="claude-code",
                msg_type="rejection",
                session_id=plan["session_id"],
                ref_msg_id=plan["msg_id"],
                payload=assessment,
            )
            callback_result = _post_callback(callback_url, rejection, self.routing.mode)
            self.audit.append(
                {
                    "kind": "callback_post",
                    "session_id": plan["session_id"],
                    "target": callback_url,
                    "callback_type": rejection["msg_type"],
                    "status_code": callback_result["status_code"],
                }
            )
        return assessment

    def _write_plan_json(self, plan: Dict[str, Any], path: Path) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(plan["payload"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return str(path)

    def _write_result_summary(self, plan: Dict[str, Any], path: Path, steps_completed: List[Dict[str, Any]]) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Session {plan['session_id']}",
            "",
            f"- Goal: {plan['payload']['goal']}",
            f"- Route mode: {self.routing.mode}",
            f"- Research provider: {self.routing.provider_map['antigravity']}",
            f"- Planner provider: {self.routing.provider_map['claude-code']}",
            f"- Executor provider: {self.routing.provider_map['codex']}",
            f"- Executor engine: {self.inference.engine}",
            f"- Executor model: {self.inference.model}",
            f"- Strategy: {self.inference.strategy_label()}",
            f"- Speculative decoding: {'enabled' if self.inference.speculative else 'disabled'}",
            f"- Runtime command: `{self.inference.build_command() or 'n/a'}`",
            "",
            "## Steps",
        ]
        for step in steps_completed:
            lines.append(f"- Step {step['step_id']}: {step['status']}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def _run_command(self, session_dir: Path, step_id: int, command: str) -> Dict[str, Any]:
        logs_dir = session_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"step-{step_id}.log"
        argv = _parse_command(command)
        proc = subprocess.run(
            argv,
            cwd=self.workspace,
            text=True,
            capture_output=True,
        )
        log_path.write_text(
            f"$ {command}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}\n",
            encoding="utf-8",
        )
        status = "done" if proc.returncode == 0 else "failed"
        return {
            "status": status,
            "output": f"exit={proc.returncode}",
            "files_changed": [str(log_path)],
            "error": proc.stderr.strip() if proc.returncode != 0 else None,
        }

    def execute(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        session_id = plan["session_id"]
        session_dir = self._session_dir(session_id)
        completed_step_ids = self._completed_step_ids(session_id)
        step_results: List[Dict[str, Any]] = []
        step_status_map: Dict[int, str] = {}

        for step in plan["payload"]["steps"]:
            step_id = step["id"]
            depends_on = step.get("depends_on", [])
            if any(step_status_map.get(dep) == "failed" for dep in depends_on):
                result = {
                    "step_id": step_id,
                    "status": "skipped",
                    "output": "dependency failed",
                    "files_changed": [],
                    "error": "dependency failed",
                }
                step_results.append(result)
                step_status_map[step_id] = "failed"
                self.audit.append({"kind": "step_result", "session_id": session_id, "step_id": step_id, "status": "skipped"})
                continue

            if step_id in completed_step_ids:
                result = {
                    "step_id": step_id,
                    "status": "done",
                    "output": "reused from audit log",
                    "files_changed": [],
                }
                step_results.append(result)
                step_status_map[step_id] = "done"
                continue

            self.audit.append({"kind": "step_started", "session_id": session_id, "step_id": step_id, "action": step["action"]})

            if step["action"] == "create":
                target = self._resolve_path(session_id, step["file_path"])
                target.mkdir(parents=True, exist_ok=True)
                result = {"step_id": step_id, "status": "done", "output": "directory created", "files_changed": [str(target)]}
            elif step["action"] == "edit":
                target = self._resolve_path(session_id, step["file_path"])
                changed = self._write_plan_json(plan, target)
                result = {"step_id": step_id, "status": "done", "output": "plan persisted", "files_changed": [changed]}
            elif step["action"] in {"run", "test"}:
                command = step["command"]
                command_result = self._run_command(session_dir, step_id, command)
                result = {"step_id": step_id, **command_result}
            elif step["action"] == "verify":
                target = self._resolve_path(session_id, step["file_path"])
                summary_steps = [*step_results, {"step_id": step_id, "status": "done"}]
                changed = self._write_result_summary(plan, target, summary_steps)
                result = {"step_id": step_id, "status": "done", "output": "summary written", "files_changed": [changed]}
            elif step["action"] == "delete":
                target = self._resolve_path(session_id, step["file_path"])
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                result = {"step_id": step_id, "status": "done", "output": "path deleted", "files_changed": [str(target)]}
            else:
                result = {"step_id": step_id, "status": "failed", "output": "unsupported action", "files_changed": [], "error": step["action"]}

            step_results.append(result)
            step_status_map[step_id] = "done" if result["status"] == "done" else "failed"
            self.audit.append(
                {
                    "kind": "step_result",
                    "session_id": session_id,
                    "step_id": step_id,
                    "status": result["status"],
                    "files_changed": result.get("files_changed", []),
                    "output": result.get("output"),
                    "error": result.get("error"),
                }
            )

        overall_status = "success"
        if any(step["status"] == "failed" for step in step_results):
            overall_status = "failed"
        elif any(step["status"] == "skipped" for step in step_results):
            overall_status = "partial"

        tests_passed = not any(step["status"] == "failed" for step in step_results if step["step_id"] in {s["id"] for s in plan["payload"]["steps"] if s["action"] == "test"})
        role_url = self.inference.role_url("codex", self.routing.mode)
        exec_backend = _probe_local_llm(self.routing.mode, role_url, "codex") if role_url else None
        result = make_envelope(
            from_agent="codex",
            to_agent="jsonl",
            msg_type="exec_result",
            session_id=session_id,
            ref_msg_id=plan["msg_id"],
            payload={
                "status": overall_status,
                "summary": "Plan executed locally and artifacts were written to disk",
                "tests_passed": tests_passed,
                "runtime": asdict(self.inference),
                "strategy": self.inference.strategy_label(),
                "runtime_command": self.inference.build_command(),
                "backend_probe": exec_backend,
                "steps_completed": step_results,
            },
        )
        callback_url = plan["payload"].get("callback_url")
        if callback_url:
            callback_result = _post_callback(callback_url, result, self.routing.mode)
            self.audit.append(
                {
                    "kind": "callback_post",
                    "session_id": session_id,
                    "target": callback_url,
                    "callback_type": result["msg_type"],
                    "status_code": callback_result["status_code"],
                }
            )
        return result

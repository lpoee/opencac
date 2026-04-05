from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Optional

from .audit import AuditLog
from .roles import Antigravity, ClaudeCodePlanner, CodexExecutor
from .runtime import InferenceConfig, LLM_TIMEOUT, RoutingConfig, Sidecar, ensure_private_runtime, make_envelope

def run_pipeline(*, prompt: str, mode: str, workspace: Path, audit: AuditLog, inference: Optional[InferenceConfig] = None, callback_url: Optional[str] = None) -> Dict[str, Any]:
    session_id = str(uuid.uuid4())
    routing = RoutingConfig(mode=mode)
    inference = inference or InferenceConfig()
    if mode == "private":
        audit.append({
            "kind": "private_runtime_validated",
            "session_id": session_id,
            "details": ensure_private_runtime(inference),
        })
    sidecar = Sidecar(audit)
    research = Antigravity(routing, inference, workspace)
    planner = ClaudeCodePlanner(routing, inference, workspace)
    executor = CodexExecutor(routing, inference, workspace, audit)

    instruction_payload = {"prompt": prompt, "mode": mode, "inference": asdict(inference)}
    if callback_url:
        instruction_payload["callback_url"] = callback_url
    instruction = make_envelope(
        from_agent="dispatcher",
        to_agent="antigravity",
        msg_type="instruction",
        session_id=session_id,
        payload=instruction_payload,
    )
    audit.append({"kind": "instruction_created", "session_id": session_id, "message": instruction})

    research_payload = {"query": prompt, "mode": mode, "inference": asdict(inference)}
    if callback_url:
        research_payload["callback_url"] = callback_url
    research_request = make_envelope(
        from_agent="dispatcher",
        to_agent="antigravity",
        msg_type="research_request",
        session_id=session_id,
        ref_msg_id=instruction["msg_id"],
        payload=research_payload,
    )
    audit.append({"kind": "dispatch", "session_id": session_id, "message": sidecar.forward(research_request)})

    with ThreadPoolExecutor(max_workers=1) as pool:
        audit.append({"kind": "research_async_started", "session_id": session_id})
        report = sidecar.forward(pool.submit(research.handle, research_request).result(timeout=LLM_TIMEOUT))
    audit.append({"kind": "research_report", "session_id": session_id, "message": report})

    plan = sidecar.forward(planner.handle(report))
    audit.append({"kind": "plan", "session_id": session_id, "message": plan})

    research_assessment = report["payload"].get("research_assessment", {})
    audit.append({"kind": "research_assessment", "session_id": session_id, "assessment": research_assessment})

    plan_assessment = plan["payload"].get("plan_assessment", {})
    audit.append({"kind": "plan_assessment", "session_id": session_id, "assessment": plan_assessment})

    execution_assessment = executor.assess_plan(plan)
    audit.append({"kind": "implementation_assessment", "session_id": session_id, "assessment": execution_assessment})
    if execution_assessment["verdict"] != "approve":
        return {"session_id": session_id, "status": "rejected", "assessment": execution_assessment, "audit_path": str(audit.path)}

    result = sidecar.forward(executor.execute(plan))
    audit.append({"kind": "exec_result", "session_id": session_id, "message": result})
    return {"session_id": session_id, "status": result["payload"]["status"], "result": result, "audit_path": str(audit.path)}


def resume_pipeline(*, session_id: str, workspace: Path, audit: AuditLog) -> Dict[str, Any]:
    entries = audit.read(session_id=session_id, last=1000)
    if not entries:
        raise ValueError(f"session not found: {session_id}")

    for entry in reversed(entries):
        if entry.get("kind") == "exec_result" and entry["message"]["payload"]["status"] == "success":
            return {
                "session_id": session_id,
                "status": entry["message"]["payload"]["status"],
                "resumed": True,
                "result": entry["message"],
                "audit_path": str(audit.path),
            }

    plan_entry = next((entry for entry in reversed(entries) if entry.get("kind") == "plan"), None)
    if not plan_entry:
        raise ValueError(f"session has no resumable plan: {session_id}")

    mode = "cloud"
    inference = InferenceConfig()
    for entry in entries:
        if entry.get("kind") == "instruction_created":
            mode = entry["message"]["payload"].get("mode", "cloud")
            inference_data = entry["message"]["payload"].get("inference")
            if isinstance(inference_data, dict):
                inference = InferenceConfig(**inference_data)
            break

    routing = RoutingConfig(mode=mode)
    sidecar = Sidecar(audit)
    executor = CodexExecutor(routing, inference, workspace, audit)
    plan = plan_entry["message"]

    execution_assessment = executor.assess_plan(plan)
    audit.append({"kind": "implementation_assessment_resume", "session_id": session_id, "assessment": execution_assessment})
    if execution_assessment["verdict"] != "approve":
        return {"session_id": session_id, "status": "rejected", "resumed": True, "assessment": execution_assessment, "audit_path": str(audit.path)}

    result = sidecar.forward(executor.execute(plan))
    audit.append({"kind": "exec_result", "session_id": session_id, "message": result})
    return {
        "session_id": session_id,
        "status": result["payload"]["status"],
        "resumed": True,
        "result": result,
        "audit_path": str(audit.path),
    }

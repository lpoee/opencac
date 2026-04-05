from __future__ import annotations

ALLOWED_AGENTS = ["dispatcher", "antigravity", "claude-code", "codex", "sidecar", "jsonl"]
ALLOWED_MSG_TYPES = [
    "instruction",
    "research_request",
    "research_report",
    "plan",
    "critique",
    "rejection",
    "approval",
    "exec_result",
]
ALLOWED_ACTIONS = ["create", "edit", "delete", "run", "test", "verify"]
ALLOWED_VERDICTS = ["approve", "reject", "revise"]
ALLOWED_EXEC_STATUS = ["success", "partial", "failed"]
ALLOWED_STEP_STATUS = ["done", "skipped", "failed"]

ENVELOPE_REQUIRED = ["msg_id", "timestamp", "from_agent", "to_agent", "msg_type", "session_id", "payload"]

PAYLOAD_REQUIRED = {
    "instruction": ["prompt", "mode"],
    "research_request": ["query", "mode"],
    "research_report": ["query", "summary", "findings", "sources"],
    "plan": ["goal", "context", "steps"],
    "critique": ["verdict", "issues"],
    "rejection": ["verdict", "issues"],
    "approval": ["verdict", "issues"],
    "exec_result": ["status", "steps_completed"],
}

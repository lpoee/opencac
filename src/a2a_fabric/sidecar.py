from __future__ import annotations

from typing import Any, Dict, Iterable
from urllib.parse import urlparse

from .schemas import (
    ALLOWED_ACTIONS,
    ALLOWED_AGENTS,
    ALLOWED_EXEC_STATUS,
    ALLOWED_MSG_TYPES,
    ALLOWED_STEP_STATUS,
    ALLOWED_VERDICTS,
    ENVELOPE_REQUIRED,
    PAYLOAD_REQUIRED,
)


class SidecarValidationError(ValueError):
    pass


def _require_keys(data: Dict[str, Any], keys: Iterable[str], scope: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise SidecarValidationError(f"{scope} missing required keys: {', '.join(missing)}")


def _validate_callback_url(payload: Dict[str, Any]) -> None:
    callback_url = payload.get("callback_url")
    if callback_url is None:
        return
    if not isinstance(callback_url, str):
        raise SidecarValidationError("callback_url must be a string")
    parsed = urlparse(callback_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SidecarValidationError("callback_url must be an absolute http(s) URL")


def validate_envelope(message: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(message, dict):
        raise SidecarValidationError("sidecar rejected non-object message")

    _require_keys(message, ENVELOPE_REQUIRED, "envelope")

    if message["from_agent"] not in ALLOWED_AGENTS:
        raise SidecarValidationError(f"unknown from_agent: {message['from_agent']}")
    if message["to_agent"] not in ALLOWED_AGENTS:
        raise SidecarValidationError(f"unknown to_agent: {message['to_agent']}")
    if message["msg_type"] not in ALLOWED_MSG_TYPES:
        raise SidecarValidationError(f"unknown msg_type: {message['msg_type']}")
    if not isinstance(message["payload"], dict):
        raise SidecarValidationError("payload must be a JSON object")

    validate_payload(message["msg_type"], message["payload"])
    return message


def validate_payload(msg_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_keys(payload, PAYLOAD_REQUIRED[msg_type], f"{msg_type} payload")
    _validate_callback_url(payload)

    if msg_type in {"critique", "rejection", "approval"}:
        verdict = payload["verdict"]
        if verdict not in ALLOWED_VERDICTS:
            raise SidecarValidationError(f"invalid verdict: {verdict}")
        if not isinstance(payload["issues"], list):
            raise SidecarValidationError("issues must be a list")

    if msg_type == "plan":
        if not isinstance(payload["steps"], list) or not payload["steps"]:
            raise SidecarValidationError("plan must include at least one step")
        task = payload.get("task")
        if task is not None:
            if not isinstance(task, dict):
                raise SidecarValidationError("task must be an object")
            _require_keys(task, ["protocol", "task_id", "goal", "steps"], "plan task")
            if not isinstance(task["steps"], list) or not task["steps"]:
                raise SidecarValidationError("plan task steps must be a non-empty list")
        for step in payload["steps"]:
            if not isinstance(step, dict):
                raise SidecarValidationError("plan step must be an object")
            _require_keys(step, ["id", "action", "description"], "plan step")
            if step["action"] not in ALLOWED_ACTIONS:
                raise SidecarValidationError(f"invalid plan action: {step['action']}")

    if msg_type == "exec_result":
        status = payload["status"]
        if status not in ALLOWED_EXEC_STATUS:
            raise SidecarValidationError(f"invalid exec status: {status}")
        if not isinstance(payload["steps_completed"], list):
            raise SidecarValidationError("steps_completed must be a list")
        for step in payload["steps_completed"]:
            _require_keys(step, ["step_id", "status"], "exec_result step")
            if step["status"] not in ALLOWED_STEP_STATUS:
                raise SidecarValidationError(f"invalid step status: {step['status']}")

    if msg_type == "research_report":
        if not isinstance(payload["findings"], list):
            raise SidecarValidationError("findings must be a list")
        if not isinstance(payload["sources"], list):
            raise SidecarValidationError("sources must be a list")

    if msg_type in {"instruction", "research_request"}:
        mode = payload.get("mode")
        if mode not in {"cloud", "private"}:
            raise SidecarValidationError("mode must be cloud or private")

    return payload

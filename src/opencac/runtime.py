from __future__ import annotations

import ipaddress
import json
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request, urlopen

from .audit import AuditLog, utc_now
from .sidecar import validate_envelope

QUALITY_RANK = {
    "same-family": 3,
    "cross-family": 1,
}

DEFAULT_DRAFT_CANDIDATES = {
    "gpt-oss:20b": [
        {
            "model": "gpt-oss:small-draft",
            "family": "gpt-oss",
            "compatibility": "same-family",
            "quality_score": 0.9,
            "latency_score": 0.8,
            "enabled": False,
        }
    ]
}

TEXT_FILE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".css",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".md",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
BLOCKED_COMMAND_TOKENS = ["rm -rf /", "shutdown", "reboot", "mkfs", ":(){:|:&};:"]
BLOCKED_SHELL_TOKENS = ["|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "$(", "`"]
HTTP_TIMEOUT = 120
LLM_TIMEOUT = 300


CLOUD_TOKEN_ENV = {
    "antigravity": "A2A_ANTIGRAVITY_TOKEN",
    "claude-code": "A2A_CLAUDE_CODE_TOKEN",
    "codex": "A2A_CODEX_TOKEN",
}

ROLE_URL_ENV = {
    "antigravity": "A2A_ANTIGRAVITY_URL",
    "claude-code": "A2A_CLAUDE_CODE_URL",
    "codex": "A2A_CODEX_URL",
}


def _cloud_token_present(role: str) -> bool:
    token_env = CLOUD_TOKEN_ENV[role]
    return bool(os.getenv(token_env, "").strip())


def _cloud_fallback_enabled() -> bool:
    return os.getenv("A2A_CLOUD_FALLBACK_LOCAL", "0").strip().lower() not in {"0", "false", "off", "no"}


def make_envelope(
    *,
    from_agent: str,
    to_agent: str,
    msg_type: str,
    session_id: str,
    payload: Dict[str, Any],
    ref_msg_id: Optional[str] = None,
) -> Dict[str, Any]:
    envelope = {
        "msg_id": str(uuid.uuid4()),
        "timestamp": utc_now(),
        "from_agent": from_agent,
        "to_agent": to_agent,
        "msg_type": msg_type,
        "session_id": session_id,
        "payload": payload,
    }
    if ref_msg_id:
        envelope["ref_msg_id"] = ref_msg_id
    return validate_envelope(envelope)


def _safe_rel_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except Exception:
        return str(path)


def _iter_text_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    return (
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in TEXT_FILE_EXTENSIONS
        and ".git" not in path.parts
        and "node_modules" not in path.parts
        and "__pycache__" not in path.parts
    )


def _workspace_test_command(workspace: Path) -> Optional[str]:
    tests_dir = workspace / "tests"
    if tests_dir.exists() and any(path.name.startswith("test") and path.suffix == ".py" for path in tests_dir.rglob("*.py")):
        return "python3 -m unittest discover -s tests -v"
    if (workspace / "package.json").exists():
        return "npm test -- --runInBand"
    return None


def _contains_blocked_token(command: str) -> bool:
    lowered = command.lower()
    return any(token in lowered for token in BLOCKED_COMMAND_TOKENS)


def _parse_command(command: str) -> List[str]:
    stripped = command.strip()
    if not stripped:
        raise ValueError("empty command is not allowed")
    try:
        argv = shlex.split(stripped)
    except ValueError as exc:
        raise ValueError(f"invalid command syntax: {command}") from exc
    if not argv:
        raise ValueError("empty command is not allowed")
    if any(arg in BLOCKED_SHELL_TOKENS for arg in argv):
        raise ValueError(f"shell control operators are not allowed in commands: {command}")
    return argv


def _loopback_only(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _post_callback(callback_url: str, payload: Dict[str, Any], mode: str) -> Dict[str, Any]:
    if mode == "private" and not _loopback_only(callback_url):
        raise ValueError(f"private mode requires loopback callback URL, got: {callback_url}")
    request = Request(
        callback_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return {
            "status_code": response.status,
            "body": response.read().decode("utf-8"),
        }


def _query_terms(query: str) -> List[str]:
    return [term.lower() for term in query.replace("/", " ").replace("_", " ").split() if term.strip()]


def _search_lines(root: Path, workspace: Path, query: str, label: str, limit: int = 3) -> Tuple[List[Dict[str, Any]], int]:
    terms = _query_terms(query)
    findings: List[Dict[str, Any]] = []
    scanned = 0
    for path in _iter_text_files(root):
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if terms and not any(term in lowered for term in terms):
                continue
            findings.append(
                {
                    "title": f"{label}: {_safe_rel_path(path, workspace)}:{lineno}",
                    "content": line.strip()[:300],
                    "confidence": "high" if label == "Local docs" else "medium",
                    "source_refs": [len(findings)],
                }
            )
            break
        if len(findings) >= limit:
            break
    return findings, scanned


def _default_role_url(role: str) -> str:
    mapping = {
        "antigravity": "http://127.0.0.1:18101",
        "claude-code": "http://127.0.0.1:18102",
        "codex": "http://127.0.0.1:18103",
    }
    return mapping[role]


def _completion_request(base_url: str, *, prompt: str, grammar: str) -> Dict[str, Any]:
    payload = {
        "prompt": prompt,
        "n_predict": 16,
        "temperature": 0,
        "top_k": 1,
        "top_p": 0,
        "min_p": 0,
        "repeat_penalty": 1.0,
        "stop": ["\n", "<|end|>", "<|return|>"],
        "grammar": grammar,
    }
    request = Request(
        f"{base_url.rstrip('/')}/completion",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=LLM_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _probe_local_llm(mode: str, base_url: str, role: str) -> Dict[str, Any]:
    if mode == "private" and not _loopback_only(base_url):
        raise ValueError(f"private mode requires loopback {role} URL, got: {base_url}")
    token = role.replace("-", "_") + "_ok"
    try:
        data = _completion_request(base_url, prompt=f"Output exactly {token}", grammar=f'root ::= "{token}"')
    except URLError as exc:
        raise RuntimeError(f"{role} local llm unavailable at {base_url}: {exc}") from exc
    content = data.get("content", "").strip()
    if content != token:
        raise RuntimeError(f"{role} local llm returned unexpected probe: {content!r}")
    return {"endpoint": base_url, "probe": content}


def ensure_private_runtime(inference: "InferenceConfig") -> Dict[str, Any]:
    guard_script = Path.home() / ".local" / "bin" / "opencac-private-guard"
    if not guard_script.exists():
        raise RuntimeError(f"private guard is missing: {guard_script}")
    proc = subprocess.run([str(guard_script), "status"], text=True, capture_output=True)
    status_line = proc.stdout.splitlines()[0].strip() if proc.stdout.strip() else "unknown"
    if proc.returncode != 0 or status_line != "enabled":
        raise RuntimeError("private mode requires the opencac-private-guard guard to be enabled")
    role_urls = {}
    for role in ("antigravity", "claude-code", "codex"):
        role_url = inference.role_url(role, "private")
        if role_url and not _loopback_only(role_url):
            raise ValueError(f"private mode requires loopback {role} URL, got: {role_url}")
        role_urls[role] = role_url
    return {
        "private_guard": "enabled",
        "guard_script": str(guard_script),
        "role_urls": role_urls,
    }


@dataclass
class RoutingConfig:
    mode: str = "cloud"

    @property
    def provider_map(self) -> Dict[str, str]:
        if self.mode == "private":
            return {
                "antigravity": "local-rag",
                "claude-code": "local-llm",
                "codex": "local-llm",
            }
        return {
            "antigravity": "cloud-search" if _cloud_token_present("antigravity") else "local-fallback-rag",
            "claude-code": "cloud-planner" if _cloud_token_present("claude-code") else "local-fallback-llm",
            "codex": "cloud-exec" if _cloud_token_present("codex") else "local-exec",
        }


@dataclass
class InferenceConfig:
    engine: str = "llama.cpp"
    model: str = "gpt-oss:20b"
    speculative: bool = True
    speculative_mode: str = "auto"
    draft_model: Optional[str] = None
    spec_type: str = "ngram-simple"
    draft_max: int = 64
    draft_min: int = 16
    spec_ngram_size_n: int = 12
    spec_ngram_size_m: int = 48
    spec_ngram_min_hits: int = 1
    antigravity_url: Optional[str] = None
    claude_code_url: Optional[str] = None
    codex_url: Optional[str] = None

    def __post_init__(self) -> None:
        # System policy: every task uses speculative decoding.
        self.speculative = True

    def resolve_draft_model(self) -> Optional[str]:
        if self.draft_model:
            return self.draft_model
        if self.speculative_mode != "auto":
            return None
        candidates = DEFAULT_DRAFT_CANDIDATES.get(self.model, [])
        viable = [
            candidate
            for candidate in candidates
            if candidate.get("enabled") and QUALITY_RANK.get(candidate.get("compatibility", ""), 0) >= QUALITY_RANK["same-family"]
        ]
        if not viable:
            return None
        viable.sort(key=lambda item: (item.get("quality_score", 0), item.get("latency_score", 0)), reverse=True)
        return viable[0]["model"]

    def role_url(self, role: str, mode: str) -> Optional[str]:
        explicit = {
            "antigravity": self.antigravity_url,
            "claude-code": self.claude_code_url,
            "codex": self.codex_url,
        }[role]
        if explicit:
            return explicit
        env_url = os.getenv(ROLE_URL_ENV[role], "").strip()
        if env_url:
            return env_url
        if mode == "private":
            return _default_role_url(role)
        if mode == "cloud" and _cloud_fallback_enabled() and not _cloud_token_present(role):
            return _default_role_url(role)
        return None

    def strategy_label(self) -> str:
        if not self.speculative:
            return "vanilla"
        if self.resolve_draft_model():
            return "draft-model"
        return "self-speculative"

    def build_command(self) -> Optional[str]:
        if self.engine != "llama.cpp":
            return None

        args = ["llama-server", f"-m {self.model}"]
        if self.speculative:
            draft_model = self.resolve_draft_model()
            if draft_model:
                args.append(f"--draft-model {draft_model}")
            else:
                args.append(f"--spec-type {self.spec_type}")
                args.append(f"--spec-ngram-size-n {self.spec_ngram_size_n}")
                args.append(f"--spec-ngram-size-m {self.spec_ngram_size_m}")
                args.append(f"--spec-ngram-min-hits {self.spec_ngram_min_hits}")
            args.append(f"--draft-min {self.draft_min}")
            args.append(f"--draft-max {self.draft_max}")
        return " ".join(args)


class Sidecar:
    def __init__(self, audit: AuditLog):
        self.audit = audit

    def forward(self, message: Dict[str, Any], node: str = "sidecar") -> Dict[str, Any]:
        validated = validate_envelope(message)
        self.audit.append(
            {
                "kind": "sidecar_pass",
                "node": node,
                "session_id": validated["session_id"],
                "msg_id": validated["msg_id"],
                "msg_type": validated["msg_type"],
                "from": validated["from_agent"],
                "to": validated["to_agent"],
            }
        )
        return validated

    def reject(self, raw_message: Any, *, from_agent: str, to_agent: str, session_id: str, reason: str) -> Dict[str, Any]:
        record = {
            "kind": "sidecar_reject",
            "node": "sidecar",
            "session_id": session_id,
            "status_code": 400,
            "from": from_agent,
            "to": to_agent,
            "reason": reason,
            "raw_message": raw_message,
        }
        self.audit.append(record)
        return record

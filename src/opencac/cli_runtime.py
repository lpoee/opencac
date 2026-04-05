from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Optional, TextIO

from .agents import InferenceConfig, Sidecar, run_pipeline
from .audit import AuditLog, utc_now
from .runtime import HTTP_TIMEOUT, LLM_TIMEOUT

QUESTION_PREFIXES = {
    "who",
    "what",
    "when",
    "where",
    "why",
    "how",
    "can",
    "could",
    "would",
    "should",
    "is",
    "are",
    "do",
    "does",
    "did",
    "which",
    "whats",
    "what's",
    "who's",
    "explain",
    "define",
    "tell",
    "你",
    "什么",
    "怎么",
    "为何",
    "为什么",
    "请问",
    "是否",
    "能否",
}

RESEARCH_HINTS = {
    "latest",
    "today",
    "current",
    "docs",
    "documentation",
    "document",
    "source",
    "sources",
    "repo",
    "repository",
    "code",
    "file",
    "files",
    "path",
    "line",
    "lines",
    "stack",
    "trace",
    "error",
    "log",
    "logs",
    "api",
    "test",
    "tests",
    "readme",
    "最新",
    "当前",
    "文档",
    "资料",
    "源码",
    "代码",
    "仓库",
    "文件",
    "路径",
    "报错",
    "日志",
    "接口",
    "测试",
}


class InteractiveState:
    def __init__(self) -> None:
        self.mode = "private"
        self.workspace = str(Path.cwd())
        self.audit = ".opencac/audit.jsonl"
        self.distributed = True
        self.async_run = False
        self.base_url = "http://127.0.0.1:8000"
        self.callback_url: Optional[str] = None
        self.model = "gpt-oss:20b"
        self.speculative_mode = "auto"
        self.draft_model: Optional[str] = None
        self.spec_type = "ngram-simple"
        self.draft_max = 64
        self.draft_min = 16
        self.spec_ngram_size_n = 12
        self.spec_ngram_size_m = 48
        self.spec_ngram_min_hits = 1
        self.json_output = False

    def inference(self) -> InferenceConfig:
        return InferenceConfig(
            engine="llama.cpp",
            model=self.model,
            speculative_mode=self.speculative_mode,
            draft_model=self.draft_model,
            spec_type=self.spec_type,
            draft_max=self.draft_max,
            draft_min=self.draft_min,
            spec_ngram_size_n=self.spec_ngram_size_n,
            spec_ngram_size_m=self.spec_ngram_size_m,
            spec_ngram_min_hits=self.spec_ngram_min_hits,
            antigravity_url=os.getenv("A2A_ANTIGRAVITY_URL"),
            claude_code_url=os.getenv("A2A_CLAUDE_CODE_URL"),
            codex_url=os.getenv("A2A_CODEX_URL"),
        )


def _http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _http_post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _ensure_private_base_url(mode: str, base_url: str) -> None:
    if mode != "private":
        return
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"private mode requires loopback base URL, got: {base_url}")


def _completion_text(base_url: str, prompt: str, *, max_tokens: int = 96) -> str:
    payload = {
        "prompt": prompt,
        "n_predict": max_tokens,
        "temperature": 0.0,
        "top_k": 1,
        "top_p": 0.0,
        "min_p": 0.0,
        "repeat_penalty": 1.0,
        "stop": ["\n", "<|end|>", "<|return|>"],
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/completion",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=LLM_TIMEOUT) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("content", "").strip()


def _answer_base_url(mode: str, inference: InferenceConfig) -> Optional[str]:
    if mode != "private":
        return None
    return inference.codex_url or "http://127.0.0.1:18103"


def _looks_like_question(prompt: str) -> bool:
    stripped = prompt.strip()
    if not stripped:
        return False
    if stripped.endswith(("?", "？")):
        return True
    first = stripped.split()[0].lower() if stripped.split() else ""
    if first in QUESTION_PREFIXES:
        return True
    lowered = stripped.lower()
    return lowered.startswith(("what ", "why ", "how ", "who ", "when ", "where ", "can ", "could ", "would ", "should "))


def _question_needs_research(prompt: str) -> bool:
    stripped = prompt.strip()
    lowered = stripped.lower()
    if "`" in stripped or "/" in stripped:
        return True
    return any(hint in lowered or hint in stripped for hint in RESEARCH_HINTS)


def _synthesize_answer(prompt: str, context: str, inference: InferenceConfig, mode: str) -> str:
    base_url = _answer_base_url(mode, inference)
    if not base_url:
        return context
    synth_prompt = (
        "You are the final response layer for an agent CLI. "
        "Output exactly one short final answer sentence. "
        "No chain-of-thought. No analysis. No bullet points. No JSON. "
        "If the input is a question, answer it directly. "
        "If the input is a task, state the concrete outcome directly.\n\n"
        f"User input: {prompt}\n"
        f"Context: {context}\n"
        "Final answer:"
    )
    try:
        answer = _completion_text(base_url, synth_prompt, max_tokens=64)
    except Exception:
        return context
    return answer or context


def _answer_question(prompt: str, inference: InferenceConfig, mode: str) -> str:
    base_url = _answer_base_url(mode, inference)
    fallback = f"Question received: {prompt}"
    if not base_url:
        return fallback
    answer_prompt = (
        "You are Codex in an interactive CLI. "
        "Answer the user's question directly in one or two short sentences. "
        "No JSON. No bullet points. No process description.\n\n"
        f"Question: {prompt}\n"
        "Answer:"
    )
    try:
        answer = _completion_text(base_url, answer_prompt, max_tokens=96)
    except Exception:
        return fallback
    return answer or fallback


def _run_task_once(prompt: str, *, mode: str, workspace_arg: str, audit_arg: str, inference: InferenceConfig, distributed: bool, async_run: bool, base_url: str, callback_url: Optional[str]) -> dict:
    workspace = Path(workspace_arg).resolve()
    audit = AuditLog((workspace / audit_arg).resolve())
    if distributed:
        _ensure_private_base_url(mode, base_url)
        query = "?distributed=1&async=1" if async_run else "?distributed=1"
        return _http_post(
            f"{base_url.rstrip('/')}" + f"/run{query}",
            {"prompt": prompt, "mode": mode, "inference": inference.__dict__, "callback_url": callback_url},
        )
    return run_pipeline(prompt=prompt, mode=mode, workspace=workspace, audit=audit, inference=inference, callback_url=callback_url)


def _run_question_once(prompt: str, *, mode: str, workspace_arg: str, audit_arg: str, inference: InferenceConfig) -> dict:
    workspace = Path(workspace_arg).resolve()
    audit = AuditLog((workspace / audit_arg).resolve())
    session_id = str(uuid.uuid4())
    audit.append({"kind": "question_received", "session_id": session_id, "prompt": prompt, "mode": mode})
    process = ["answer"]
    if _question_needs_research(prompt):
        audit.append({"kind": "question_researched", "session_id": session_id, "route": "local-codex-answer", "mode": mode})
        process = ["research", "answer"]
    answer = _answer_question(prompt, inference, mode)
    result = {
        "kind": "answer",
        "session_id": session_id,
        "status": "success",
        "answer": answer,
        "process": process,
        "audit_path": str(audit.path),
        "ts": utc_now(),
    }
    audit.append({"kind": "question_answered", "session_id": session_id, "answer": answer})
    return result


def _run_interactive_once(prompt: str, *, mode: str, workspace_arg: str, audit_arg: str, inference: InferenceConfig, distributed: bool, async_run: bool, base_url: str, callback_url: Optional[str]) -> dict:
    if _looks_like_question(prompt):
        return _run_question_once(prompt, mode=mode, workspace_arg=workspace_arg, audit_arg=audit_arg, inference=inference)
    return _run_task_once(
        prompt,
        mode=mode,
        workspace_arg=workspace_arg,
        audit_arg=audit_arg,
        inference=inference,
        distributed=distributed,
        async_run=async_run,
        base_url=base_url,
        callback_url=callback_url,
    )


def _print_interactive_help(stream: TextIO) -> None:
    stream.write("/help, /exit, /mode <private|cloud>, /distributed <on|off>, /base-url <url>, /workspace <path>, /audit <path>, /json <on|off>\n")


def _render_interactive_result(prompt: str, result: dict, inference: InferenceConfig, mode: str) -> str:
    if result.get("kind") == "answer":
        process = " -> ".join(result.get("process", ["research", "answer"]))
        return "\n".join(
            [
                f"answer: {result.get('answer', '')}",
                f"process: {process}",
                f"status: {result.get('status', 'unknown')}",
                f"session: {result.get('session_id', 'n/a')}",
                f"audit: {result.get('audit_path', '')}",
            ]
        )

    session_id = result.get("session_id", "n/a")
    status = result.get("status", "unknown")
    payload = result.get("result", {}).get("payload", {}) if isinstance(result.get("result"), dict) else {}
    steps = payload.get("steps_completed", [])
    step_summary = ", ".join(f"{step['step_id']}={step['status']}" for step in steps) if steps else "n/a"
    artifact_root = None
    for step in steps:
        for changed in step.get("files_changed", []):
            candidate = Path(changed)
            if candidate.name in {"plan.json", "result.md"}:
                artifact_root = str(candidate.parent)
                break
            if candidate.is_dir():
                artifact_root = str(candidate)
                break
        if artifact_root:
            break
    payload_summary = payload.get("summary") or f"Task finished with status {status}."
    answer = _synthesize_answer(prompt, payload_summary, inference, mode)
    lines = [
        f"answer: {answer}",
        f"process: research -> plan -> critique -> execute ({step_summary})",
        f"status: {status}",
        f"session: {session_id}",
    ]
    if artifact_root:
        lines.append(f"artifacts: {artifact_root}")
    audit_path = result.get("audit_path")
    if audit_path:
        lines.append(f"audit: {audit_path}")
    return "\n".join(lines)


def run_interactive(*, stdin: TextIO, stdout: TextIO) -> int:
    state = InteractiveState()
    stdout.write("OpenCAC interactive mode\n")
    stdout.write(f"mode={state.mode} distributed={'on' if state.distributed else 'off'} base_url={state.base_url}\n")
    _print_interactive_help(stdout)
    while True:
        stdout.write("opencac> ")
        stdout.flush()
        line = stdin.readline()
        if not line:
            stdout.write("\n")
            return 0
        line = line.strip()
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            stdout.write("bye\n")
            return 0
        if line == "/help":
            _print_interactive_help(stdout)
            continue
        if line.startswith("/mode "):
            mode = line.split(None, 1)[1].strip()
            if mode not in {"private", "cloud"}:
                stdout.write("error: mode must be private or cloud\n")
                continue
            state.mode = mode
            stdout.write(f"mode={state.mode}\n")
            continue
        if line.startswith("/distributed "):
            value = line.split(None, 1)[1].strip().lower()
            if value not in {"on", "off"}:
                stdout.write("error: distributed must be on or off\n")
                continue
            state.distributed = value == "on"
            stdout.write(f"distributed={'on' if state.distributed else 'off'}\n")
            continue
        if line.startswith("/base-url "):
            state.base_url = line.split(None, 1)[1].strip()
            stdout.write(f"base_url={state.base_url}\n")
            continue
        if line.startswith("/workspace "):
            state.workspace = line.split(None, 1)[1].strip()
            stdout.write(f"workspace={state.workspace}\n")
            continue
        if line.startswith("/audit "):
            state.audit = line.split(None, 1)[1].strip()
            stdout.write(f"audit={state.audit}\n")
            continue
        if line.startswith("/json "):
            value = line.split(None, 1)[1].strip().lower()
            if value not in {"on", "off"}:
                stdout.write("error: json must be on or off\n")
                continue
            state.json_output = value == "on"
            stdout.write(f"json={'on' if state.json_output else 'off'}\n")
            continue
        inference = state.inference()
        try:
            result = _run_interactive_once(
                line,
                mode=state.mode,
                workspace_arg=state.workspace,
                audit_arg=state.audit,
                inference=inference,
                distributed=state.distributed,
                async_run=state.async_run,
                base_url=state.base_url,
                callback_url=state.callback_url,
            )
        except Exception as exc:
            stdout.write(f"error: {exc}\n")
            continue
        if state.json_output:
            stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        else:
            stdout.write(_render_interactive_result(line, result, inference, state.mode) + "\n")
    return 0

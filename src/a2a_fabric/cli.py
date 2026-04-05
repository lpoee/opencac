from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .agents import InferenceConfig, Sidecar, resume_pipeline
from .audit import AuditLog
from .service import serve

from .cli_runtime import InteractiveState, _http_get, _http_post, _question_needs_research, _run_task_once, run_interactive

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opencac", description="OpenCAC CLI")
    sub = parser.add_subparsers(dest="command", required=False)

    run_parser = sub.add_parser("run", help="dispatch a natural-language task into the A2A pipeline")
    run_parser.add_argument("prompt", help="natural language instruction")
    run_parser.add_argument("--mode", choices=["cloud", "private"], default="cloud")
    run_parser.add_argument("--workspace", default=".", help="artifact root")
    run_parser.add_argument("--audit", default=".a2a/audit.jsonl", help="audit JSONL path")
    run_parser.add_argument("--distributed", action="store_true", help="route run through the local HTTP A2A service")
    run_parser.add_argument("--async-run", action="store_true", help="return immediately and continue distributed processing in the background")
    run_parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="A2A service base URL for distributed mode")
    run_parser.add_argument("--callback-url", help="reverse POST endpoint for rejection or execution result callbacks")
    run_parser.add_argument("--model", default="gpt-oss:20b")
    run_parser.add_argument("--speculative-mode", choices=["auto", "draft-model", "self-speculative"], default="auto")
    run_parser.add_argument("--draft-model")
    run_parser.add_argument("--spec-type", choices=["none", "ngram-cache", "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod"], default="ngram-simple")
    run_parser.add_argument("--draft-max", type=int, default=64)
    run_parser.add_argument("--draft-min", type=int, default=16)
    run_parser.add_argument("--spec-ngram-size-n", type=int, default=12)
    run_parser.add_argument("--spec-ngram-size-m", type=int, default=48)
    run_parser.add_argument("--spec-ngram-min-hits", type=int, default=1)

    audit_parser = sub.add_parser("audit", help="show recent audit entries")
    audit_parser.add_argument("--audit", default=".a2a/audit.jsonl", help="audit JSONL path")
    audit_parser.add_argument("--session-id")
    audit_parser.add_argument("--last", type=int, default=20)

    resume_parser = sub.add_parser("resume", help="resume a session from JSONL audit")
    resume_parser.add_argument("session_id")
    resume_parser.add_argument("--workspace", default=".", help="artifact root")
    resume_parser.add_argument("--audit", default=".a2a/audit.jsonl", help="audit JSONL path")

    sidecar_parser = sub.add_parser("sidecar-check", help="validate a JSON message through the sidecar")
    sidecar_parser.add_argument("message", help="raw JSON string")
    sidecar_parser.add_argument("--audit", default=".a2a/audit.jsonl", help="audit JSONL path")

    discover_parser = sub.add_parser("discover", help="fetch the local agent card from an A2A service")
    discover_parser.add_argument("--base-url", default="http://127.0.0.1:8000")

    task_get_parser = sub.add_parser("task-get", help="fetch task status from an A2A service")
    task_get_parser.add_argument("session_id")
    task_get_parser.add_argument("--base-url", default="http://127.0.0.1:8000")

    send_parser = sub.add_parser("send", help="send a protocol message to an agent endpoint")
    send_parser.add_argument("agent_id", choices=["antigravity", "claude-code", "codex"])
    send_parser.add_argument("message", help="raw JSON message envelope")
    send_parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    send_parser.add_argument("--execute", action="store_true")

    serve_parser = sub.add_parser("serve", help="start the HTTP A2A fabric service")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--workspace", default=".", help="artifact root")
    serve_parser.add_argument("--audit", default=".a2a/audit.jsonl", help="audit JSONL path")

    sub.add_parser("interactive", help="start interactive CLI mode")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command in {None, "interactive"}:
        raise SystemExit(run_interactive(stdin=sys.stdin, stdout=sys.stdout))

    if args.command == "run":
        inference = InferenceConfig(
            engine="llama.cpp",
            model=args.model,
            speculative_mode=args.speculative_mode,
            draft_model=args.draft_model,
            spec_type=args.spec_type,
            draft_max=args.draft_max,
            draft_min=args.draft_min,
            spec_ngram_size_n=args.spec_ngram_size_n,
            spec_ngram_size_m=args.spec_ngram_size_m,
            spec_ngram_min_hits=args.spec_ngram_min_hits,
        )
        result = _run_task_once(
            args.prompt,
            mode=args.mode,
            workspace_arg=args.workspace,
            audit_arg=args.audit,
            inference=inference,
            distributed=args.distributed,
            async_run=args.async_run,
            base_url=args.base_url,
            callback_url=args.callback_url,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "audit":
        audit = AuditLog(Path(args.audit).resolve())
        print(json.dumps(audit.read(session_id=args.session_id, last=args.last), ensure_ascii=False, indent=2))
        return

    if args.command == "resume":
        workspace = Path(args.workspace).resolve()
        audit = AuditLog((workspace / args.audit).resolve())
        result = resume_pipeline(session_id=args.session_id, workspace=workspace, audit=audit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "sidecar-check":
        audit = AuditLog(Path(args.audit).resolve())
        sidecar = Sidecar(audit)
        try:
            message = json.loads(args.message)
            print(json.dumps(sidecar.forward(message), ensure_ascii=False, indent=2))
        except Exception as exc:
            session_id = "sidecar-check"
            record = sidecar.reject(
                args.message,
                from_agent="dispatcher",
                to_agent="sidecar",
                session_id=session_id,
                reason=str(exc),
            )
            print(json.dumps(record, ensure_ascii=False, indent=2))
        return

    if args.command == "discover":
        print(json.dumps(_http_get(f"{args.base_url.rstrip('/')}/.well-known/agent.json"), ensure_ascii=False, indent=2))
        return

    if args.command == "task-get":
        print(json.dumps(_http_get(f"{args.base_url.rstrip('/')}/tasks/{args.session_id}"), ensure_ascii=False, indent=2))
        return

    if args.command == "send":
        message = json.loads(args.message)
        suffix = "?execute=1" if args.execute else ""
        print(
            json.dumps(
                _http_post(f"{args.base_url.rstrip('/')}/agents/{args.agent_id}/message/send{suffix}", {"message": message}),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "serve":
        workspace = Path(args.workspace).resolve()
        audit = AuditLog((workspace / args.audit).resolve())
        serve(host=args.host, port=args.port, workspace=workspace, audit=audit)


if __name__ == "__main__":
    main()

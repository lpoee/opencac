# OpenCAC

One pipeline for Claude Code, Antigravity, and Codex. Validated handoffs. Audit log. Spec decoding for local LLMs.

```bash
pip install .
opencac run "refactor the auth module" --mode private
```

## Why

No standard orchestration layer for multi-agent AI coding. Claude Code, Codex, Antigravity each work in isolation — copy-paste between them, or write glue scripts. OpenCAC turns this into one pipeline.

- Cloud tokens are expensive
- Local LLMs are cheap but too weak to handle the full workflow alone

## 1. Four-role pipeline

```
dispatcher → antigravity (research) → claude-code (plan) → codex (execute)
```

- Structured envelopes at every hop, downstream critiques upstream before acting
- Codex runs `assess_plan` before execution — dangerous commands rejected, not run

## 2. Three routing modes

| Mode      |                                                                        |
| --------- | ---------------------------------------------------------------------- |
| `private` | Loopback only, private-guard required, for sensitive code / air-gapped |
| `cloud`   | Cloud API tokens                                                       |
| `hybrid`  | Cloud first, auto fallback to local LLM when tokens missing            |

## 3. Local LLM support

- Each role points to its own llama.cpp server endpoint
- Built-in spec decoding config (n-gram / draft-model), generates `llama-server` launch commands
- Probe: constrained-grammar check verifies each endpoint before pipeline starts

## 4. Sidecar validation

- Schema check on every hop — agent whitelist, message-type whitelist, required payload fields
- Blocked commands (`rm -rf /`, `shutdown`, `mkfs`, fork bomb)
- Private mode enforces loopback-only on all URLs including callbacks

## 5. JSONL audit log

- One JSON line per action — timestamp, session_id, kind
- Filter by session, query last N
- Session resume: rebuild plan from log, skip completed steps

## 6. CLI + HTTP

- **CLI**: `opencac run`, `opencac audit`, `opencac resume <session-id>`, interactive REPL
- **HTTP**: `POST /run`, `GET /tasks/<id>`, per-agent endpoints, agent card at `/.well-known/agent.json`
- **Distributed**: CLI routes through HTTP service, sync and async

## 7. Smart question routing

- Input ends with `?` or starts with `who/what/how/why` → QA path, skips pipeline
- Input is a task → full pipeline, outputs artifacts
- Question contains `docs`, `code`, `error`, `test` → research step before answer

## Quick start

```bash
git clone https://github.com/lpoee/opencac.git && cd opencac
python3 -m venv .venv && . .venv/bin/activate && pip install .
```

```bash
# Private
export A2A_ANTIGRAVITY_URL=http://127.0.0.1:18101
export A2A_CLAUDE_CODE_URL=http://127.0.0.1:18102
export A2A_CODEX_URL=http://127.0.0.1:18103
opencac run "task" --mode private

# Cloud
export A2A_ANTIGRAVITY_TOKEN=...
export A2A_CLAUDE_CODE_TOKEN=...
export A2A_CODEX_TOKEN=...
opencac run "task" --mode cloud

# Hybrid
export A2A_CLOUD_FALLBACK_LOCAL=1
opencac run "task" --mode cloud
```

Docker:

```bash
docker build -t opencac .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/data" opencac
```

## Spec decoding

```bash
opencac run "task" --mode private \
  --spec-type ngram-simple \
  --draft-max 64 --draft-min 16
```

| Strategy                   |                                | VRAM        |
| -------------------------- | ------------------------------ | ----------- |
| Self-speculative (default) | n-gram cache on main model     | Zero        |
| Draft-model                | Smaller model for draft tokens | Extra model |

## HTTP API

| Method | Path                             |                   |
| ------ | -------------------------------- | ----------------- |
| `GET`  | `/.well-known/agent.json`        | Agent card        |
| `GET`  | `/tasks/<id>`                    | Status + steps    |
| `GET`  | `/audit?session_id=<id>&last=20` | Audit log         |
| `POST` | `/run`                           | Run task          |
| `POST` | `/run?distributed=1&async=1`     | Async distributed |
| `POST` | `/agents/<agent>/message/send`   | Message an agent  |

## Output

- `artifacts/<session-id>/plan.json` — execution plan
- `artifacts/<session-id>/result.md` — routing, strategy, step results
- `.opencac/audit.jsonl` — full event log

## Testing

```bash
PYTHONPATH=src pytest -q   # 32 tests
```

## Security

See [SECURITY.md](SECURITY.md).

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

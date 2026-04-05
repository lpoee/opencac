# OpenCAC

Multi-agent orchestration for AI coding tools. Wires Claude Code, Antigravity, and Codex into one pipeline with validated handoffs, audit logging, and speculative decoding for local LLMs.

```bash
pip install .
opencac run "refactor the auth module" --mode private
```

## Why

- **No orchestration layer.** Claude Code, Codex, Antigravity — they all work in isolation. You end up copy-pasting between them or writing glue scripts. OpenCAC turns that into a single pipeline.
- **Cloud tokens are expensive.** Chaining research, planning, and execution across three models adds up fast.
- **Local LLMs are cheap but not good enough on their own.** They can handle parts of the workflow, just not all of it.

OpenCAC brings Claude Code, Codex, and Antigravity (or any local LLM) into one automated pipeline — protocol-validated, fully audited, runs locally or in the cloud — instead of three tools that don't talk to each other.

## 1. Four-role pipeline

```
dispatcher → antigravity (research) → claude-code (plan) → codex (execute)
```

- Each layer outputs a structured envelope; the next layer critiques it before acting
- Codex runs `assess_plan` before execution — dangerous commands get rejected, not blindly run

## 2. Routing modes

| Mode      |                                                                                        |
| --------- | -------------------------------------------------------------------------------------- |
| `private` | Loopback only. Private guard must be enabled. For sensitive code or air-gapped setups. |
| `cloud`   | Cloud API tokens.                                                                      |
| `hybrid`  | Cloud first, falls back to local LLM when tokens are missing.                          |

## 3. Local LLM support

- Each role can point to its own llama.cpp server endpoint
- Built-in speculative decoding config (n-gram / draft-model) — generates ready-to-use `llama-server` launch commands
- Probe mechanism: verifies each LLM endpoint with a constrained-grammar check before the pipeline starts

## 4. Sidecar validation

- Every hop's envelope is schema-checked — agent whitelist, message-type whitelist, required payload fields
- Blocked command list (`rm -rf /`, `shutdown`, `mkfs`, fork bomb)
- Private mode enforces loopback-only on all URLs, including callbacks

## 5. JSONL audit log

- One JSON line per action — timestamp, session_id, kind
- Filter by session, query the last N entries
- Session resume: rebuilds the plan from the log, skips completed steps

## 6. CLI + HTTP

- **CLI**: `opencac run "task"`, `opencac audit`, `opencac resume <session-id>`, interactive REPL
- **HTTP**: `POST /run`, `GET /tasks/<id>`, per-agent endpoints, agent card at `/.well-known/agent.json`
- **Distributed**: CLI routes through the HTTP service, supports sync and async

## 7. Smart question routing

- Input ends with `?` or starts with `who`/`what`/`how`/`why` → answered directly, skips the pipeline
- Input is a task → runs the full pipeline, outputs artifacts
- Question mentions `docs`, `code`, `error`, `test` → research step runs first

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

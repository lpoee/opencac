# OpenCAC

**Multi-agent orchestration for AI coding tools. Wires Claude Code, Antigravity, and Codex into one pipeline with validated handoffs, audit logging, and speculative decoding for local LLMs.**

```bash
pip install .
opencac run "refactor the auth module" --mode private
```
##  Why
```
- No orchestration layer today — AI coding agents work in isolation; stitching them together means copy-paste or throwaway glue scripts
- Cloud tokens add up fast — every round-trip to a hosted model costs real money
- Local LLMs are cheap but limited — a single small model can't carry a full research-plan-execute workflow on its own

OpenCAC solves this by chaining agents into a four-role pipeline where each agent does what it's best at, with structured validation at every hop.
```
## Features

```
1. FOUR-ROLE PIPELINE
   dispatcher → antigravity (research) → claude-code (plan) → codex (execute)
   - Structured envelopes at every hop; downstream critiques upstream before acting
   - Codex runs assess_plan — dangerous commands rejected, not blindly run

2. ROUTING MODES
   private   Loopback only. Private guard required. For sensitive / air-gapped work.
   cloud     Cloud API tokens. No local infra needed.
   hybrid    Cloud first, falls back to local LLM when tokens are missing.

3. LOCAL LLM SUPPORT
   - Each role points to its own llama.cpp server endpoint
   - Built-in spec decoding config (n-gram / draft-model) → generates llama-server commands
   - Probe: constrained-grammar check verifies each endpoint before pipeline starts

4. SIDECAR VALIDATION
   - Schema check on every hop — agent whitelist, message-type whitelist, payload fields
   - Blocked commands: rm -rf /, shutdown, mkfs, fork bomb
   - Private mode: loopback-only on all URLs including callbacks

5. JSONL AUDIT LOG
   - One JSON line per action — timestamp, session_id, kind
   - Filter by session, query last N entries
   - Session resume: rebuild plan from log, skip completed steps

6. CLI + HTTP
   CLI          opencac run, opencac audit, opencac resume, interactive REPL
   HTTP         POST /run, GET /tasks/<id>, per-agent endpoints, /.well-known/agent.json
   Distributed  CLI routes through HTTP service, sync and async

7. SMART QUESTION ROUTING
   - Ends with ? or starts with who/what/how/why → QA path, skips pipeline
   - Task input → full pipeline, outputs artifacts
   - Mentions docs/code/error/test → research step first
```

## Quick Start

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

## Speculative Decoding

```bash
opencac run "task" --mode private \
  --spec-type ngram-simple \
  --draft-max 64 --draft-min 16
```

| Strategy                   | Description                          | VRAM        |
| -------------------------- | ------------------------------------ | ----------- |
| Self-speculative (default) | n-gram cache on main model           | Zero extra  |
| Draft-model                | Smaller model generates draft tokens | Extra model |

## HTTP API

| Method | Path                             | Description       |
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

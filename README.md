# OpenCAC

**OpenCAC is multi-agent orchestration CLI for AI coding tools. Wires Claude Code, Antigravity, and Codex into one pipeline with validated handoffs, audit logging, and speculative decoding for local LLMs.**

```bash
pip install .
opencac run "refactor the auth module" --mode private
```

## Why

For developers who already use multiple AI coding agents and want one CLI to orchestrate them — with cloud models, local LLMs, or both.

```
   - Claude Code, Codex, Antigravity powerful alone, but each runs in its own world
   - Cloud api burns money — every hosted model call costs real tokens.
   - Local LLMs lack quality — small models are cheap but can't reliably produce production-grade code.

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
# Private (local llama.cpp shards)
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

## Agent Integration

Connect real AI agents for production-quality research, planning, and code generation.
When set, the pipeline calls real agents first and falls back to local heuristics on failure.

```bash
# Antigravity — Gemini research via JSON-RPC (A2A protocol)
export OPENCAC_RESEARCH_URL=http://127.0.0.1:18791

# Claude Code — planning via Claude Bridge (Anthropic messages API)
export OPENCAC_PLANNER_URL=http://127.0.0.1:9300

# Codex — AI code generation via CLI
export OPENCAC_CODEX_BINARY=/usr/local/bin/codex

opencac run "refactor the auth module" --mode private
```

| Variable               | Agent         | Protocol                    | Purpose                    |
| ---------------------- | ------------- | --------------------------- | -------------------------- |
| `OPENCAC_RESEARCH_URL` | Antigravity   | JSON-RPC 2.0 `message/send` | Web research via Gemini    |
| `OPENCAC_PLANNER_URL`  | Claude Bridge | `POST /v1/messages`         | Plan generation via Claude |
| `OPENCAC_CODEX_BINARY` | Codex CLI     | JSONL subprocess            | AI code generation         |

The `generate` plan action dispatches work to Codex CLI for AI-assisted code generation.
Without these variables, the pipeline uses deterministic local heuristics (file search, template plans, subprocess execution).

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

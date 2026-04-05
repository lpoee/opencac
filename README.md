# OpenCAC

Multi-agent orchestration for AI coding tools. Wires Claude Code, Antigravity, and Codex into one pipeline with validated handoffs, audit logging, and speculative decoding for local LLMs.

```bash
pip install .
opencac run "refactor the auth module" --mode private
```

## The problem

You're using Claude Code, Codex, and Antigravity as three separate tools. Context gets copy-pasted between them. Nothing validates what one agent hands to the next. When something breaks, there's no log to trace what happened.

Meanwhile: cloud API tokens are expensive when you're chaining research + planning + execution across three models. And local LLMs are cheap to run but can't reliably handle the full workflow alone -- they're fine for parts of it, not all of it.

OpenCAC turns them into one pipeline with a shared protocol. Each stage critiques the one before it. The whole thing runs on cloud, local llama.cpp with speculative decoding, or both -- and every action gets logged.

## How it works

```
dispatcher → antigravity (research) → claude-code (plan) → codex (critique + execute)
     │              │                        │                        │
     └──────────────┴────────────────────────┴────────────────────────┘
                          sidecar validates every hop
                          audit.jsonl records every event
```

Each agent produces a structured protocol envelope. The next agent validates it and pushes back if something looks wrong. Codex runs `assess_plan` before touching anything -- dangerous commands get rejected, not executed.

## Highlights

- **One pipeline, four roles** -- research → plan → critique → execute. Each layer produces a structured envelope. The downstream agent critiques the upstream output before acting on it.
- **Sidecar protocol validation** -- every hop passes through a sidecar that checks agent whitelist, message type whitelist, required payload fields per message type, and a blocked command list. Private mode enforces loopback-only on everything including callbacks.
- **Cloud, local, or both** -- route through API tokens, local llama.cpp endpoints, or hybrid with automatic fallback when tokens are missing.
- **Speculative decoding built in** -- generates `llama-server` commands with n-gram / draft-model flags. The critique layer catches bad local outputs before they hit execution, so local quality issues don't snowball into broken runs.
- **Endpoint probing** -- before the pipeline starts, each local LLM endpoint is verified alive using a constrained-grammar probe. No silent failures mid-run.
- **Full audit trail** -- append-only JSONL, one line per event, timestamped with session ID. Filter by session, query last N entries, or resume a crashed session -- completed steps get skipped automatically.
- **CLI + HTTP + distributed** -- interactive REPL with smart question detection, one-shot `opencac run`, HTTP service with `POST /run` and per-agent message endpoints, async distributed execution with status polling.
- **Zero dependencies** -- stdlib only, single `pip install`

## Quick start

```bash
git clone https://github.com/lpoee/opencac.git
cd opencac
python3 -m venv .venv && . .venv/bin/activate
pip install .
```

```bash
opencac                          # interactive mode
opencac run "task" --mode cloud  # one-shot, cloud APIs
opencac serve                    # start HTTP service on :8000
```

Docker:

```bash
docker build -t opencac .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/data" opencac
```

## Routing modes

| Mode      | What happens                                                                                  |
| --------- | --------------------------------------------------------------------------------------------- |
| `private` | Everything on loopback. Nothing leaves the machine. Requires `opencac-private-guard` enabled. |
| `cloud`   | Routes through `A2A_*_TOKEN` env vars. No local infra needed.                                 |
| `hybrid`  | Cloud first, automatic fallback to local endpoints when tokens are missing.                   |

```bash
# Private -- point each role at a local llama.cpp server
export A2A_ANTIGRAVITY_URL=http://127.0.0.1:18101
export A2A_CLAUDE_CODE_URL=http://127.0.0.1:18102
export A2A_CODEX_URL=http://127.0.0.1:18103
opencac run "task" --mode private

# Cloud
export A2A_ANTIGRAVITY_TOKEN=your-token
export A2A_CLAUDE_CODE_TOKEN=your-token
export A2A_CODEX_TOKEN=your-token
opencac run "task" --mode cloud

# Hybrid -- set both tokens and URLs
export A2A_CLOUD_FALLBACK_LOCAL=1
opencac run "task" --mode cloud
```

## Speculative decoding

When running locally, speculative decoding makes the pipeline practical. OpenCAC generates ready-to-use `llama-server` commands:

```bash
opencac run "task" --mode private \
  --spec-type ngram-simple \
  --draft-max 64 --draft-min 16 \
  --spec-ngram-size-n 12
```

| Strategy                   | How it works                                          | VRAM cost   |
| -------------------------- | ----------------------------------------------------- | ----------- |
| Self-speculative (default) | n-gram cache on the main model                        | Zero        |
| Draft-model                | Pair with a smaller model for higher acceptance rates | Extra model |

Auto-selected based on available candidates, or forced with `--speculative-mode`. The command and strategy are persisted in session artifacts.

## CLI

```
opencac                                     # interactive REPL
opencac run "prompt" --mode private         # one-shot
opencac run "prompt" --distributed          # route through HTTP service
opencac serve --host 0.0.0.0 --port 8000   # start HTTP service
opencac audit --last 20                     # recent audit entries
opencac resume <session-id>                 # pick up where you left off
```

Interactive commands: `/mode`, `/distributed`, `/base-url`, `/workspace`, `/json`, `/help`, `/exit`

The REPL auto-detects questions vs tasks. Questions skip the pipeline and get answered directly. Evidence-related questions (`docs`, `code`, `error`) trigger a research step first.

## HTTP API

| Method | Path                             | Description                |
| ------ | -------------------------------- | -------------------------- |
| `GET`  | `/.well-known/agent.json`        | Agent discovery card       |
| `GET`  | `/tasks/<session_id>`            | Task status + step results |
| `GET`  | `/audit?session_id=<id>&last=20` | Query audit log            |
| `POST` | `/run`                           | Run a task                 |
| `POST` | `/run?distributed=1`             | Distributed pipeline run   |
| `POST` | `/run?distributed=1&async=1`     | Async distributed run      |
| `POST` | `/agents/<agent>/message/send`   | Send message to an agent   |

```bash
# Run a task
curl -s localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "analyze the test suite", "mode": "private"}'

# Poll status
curl -s localhost:8000/tasks/<session-id>
```

## Output

- `artifacts/<session-id>/plan.json` -- the execution plan
- `artifacts/<session-id>/result.md` -- summary with routing, strategy, step results
- `.opencac/audit.jsonl` -- every event, one JSON line each

## Testing

```bash
PYTHONPATH=src pytest -q   # 32 tests
```

All tests run against real HTTP servers (mocked LLM endpoints), not `unittest.mock.patch` on network calls.

## Security

Private mode enforces loopback-only on all endpoints and callback URLs. Sidecar rejects unknown agents, bad message types, and malformed payloads. Blocked command list covers `rm -rf /`, `shutdown`, `mkfs`, and fork bombs. Private guard script must be explicitly enabled.

See [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Branch, test, PR.

## License

MIT

# OpenCAC

**Orchestrate Claude Code, Antigravity, and Codex as a single pipeline** -- across cloud APIs, local LLMs, or both.

OpenCAC is a zero-dependency Python CLI and HTTP service that chains multiple AI coding agents into a structured research-plan-critique-execute workflow with built-in speculative decoding for local LLMs. Run the full pipeline on your own hardware at near-zero token cost, with schema validation on every hop and a complete audit trail -- or fall back to cloud APIs when you need them.

## Why

Cloud API tokens are expensive. A single complex coding task that chains research, planning, and execution across multiple agents can burn through dollars of tokens in minutes. Local LLMs are cheap to run but too weak on their own to handle a full multi-step workflow reliably.

OpenCAC bridges this gap: run local LLMs with speculative decoding for high throughput, wrap them in a structured pipeline with validation and critique at every step, and only fall back to cloud APIs when local inference isn't available. You get cloud-grade orchestration quality at local-inference cost.

Concretely, OpenCAC provides:

- **Local-first with speculative decoding** -- run the entire pipeline on local llama.cpp endpoints with n-gram or draft-model speculation, keeping token costs at zero while maintaining throughput
- **A structured pipeline** with protocol validation between every agent hop -- no unvalidated payloads pass through, each layer critiques the one above it before work moves downstream
- **Cloud / local / hybrid routing** -- use cloud APIs when available, fall back to local endpoints when they're not, or go fully local for air-gapped work
- **A complete audit trail** -- every decision, every agent output, every execution result is logged as append-only JSONL
- **Session resume** -- pick up where a failed or interrupted run left off without re-running completed steps

## Architecture

```
User
 |
 v
Dispatcher ──> Antigravity (research) ──> Claude Code (plan) ──> Codex (execute)
                    |                          |                       |
                    v                          v                       v
              Sidecar validates          Sidecar validates       Sidecar validates
              every envelope             every envelope          every envelope
                                                                      |
                                                                      v
                                                                 audit.jsonl
                                                                 artifacts/
```

**Four roles, each with a specific job:**

| Role       | Agent       | What it does                                                                   |
| ---------- | ----------- | ------------------------------------------------------------------------------ |
| Researcher | Antigravity | Scans local docs and source code, surfaces relevant context                    |
| Planner    | Claude Code | Converts research into a dependency-ordered execution plan                     |
| Critic     | Codex       | Reviews the plan for safety (blocked commands, missing steps) before execution |
| Executor   | Codex       | Runs the approved plan, writes artifacts, logs step results                    |

The **Sidecar** validates every message envelope against a strict schema -- allowed agents, message types, required payload fields, step actions, and verdict values. Invalid messages are rejected and logged before they reach any agent.

## Quick Start

```bash
git clone https://github.com/lpoee/opencac.git
cd opencac
python3 -m venv .venv && . .venv/bin/activate
pip install .
```

Run a task:

```bash
opencac run "analyze this repository and suggest improvements" --mode private
```

Start the interactive CLI:

```bash
opencac
```

Start the HTTP service:

```bash
opencac serve --host 127.0.0.1 --port 8000
```

### Docker

```bash
docker build -t opencac .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/data" opencac
```

## Routing Modes

### Private (local-only)

All agent endpoints must be on loopback. Requires the `a2a-private-guard` script to be enabled. No network calls leave the machine.

```bash
export A2A_ANTIGRAVITY_URL=http://127.0.0.1:18101
export A2A_CLAUDE_CODE_URL=http://127.0.0.1:18102
export A2A_CODEX_URL=http://127.0.0.1:18103
opencac run "task" --mode private
```

### Cloud

Routes through cloud API tokens. No local model infrastructure required.

```bash
export A2A_ANTIGRAVITY_TOKEN=your-token
export A2A_CLAUDE_CODE_TOKEN=your-token
export A2A_CODEX_TOKEN=your-token
opencac run "task" --mode cloud
```

### Hybrid

Cloud-first with automatic local fallback when tokens are missing or endpoints are unreachable.

```bash
# Set both cloud tokens and local endpoints
export A2A_CLOUD_FALLBACK_LOCAL=1
opencac run "task" --mode cloud
```

## CLI Reference

```
opencac                                     # interactive mode
opencac run "prompt" --mode private         # single task
opencac run "prompt" --distributed          # run through HTTP service
opencac run "prompt" --distributed --async-run  # async distributed run
opencac serve --host 127.0.0.1 --port 8000  # start HTTP service
opencac audit --last 20                     # show recent audit entries
opencac resume <session-id>                 # resume interrupted session
opencac discover --base-url http://...      # fetch agent card
opencac sidecar-check '{"msg_id":...}'      # validate a message envelope
```

### Interactive Commands

```
/mode private|cloud       set routing mode
/distributed on|off       toggle distributed execution
/base-url <url>           set HTTP service URL
/workspace <path>         set artifact root
/json on|off              toggle raw JSON output
/help                     show commands
/exit                     quit
```

The interactive CLI auto-detects questions vs tasks. Questions (`who`, `what`, `how`, `?`, Chinese question words) get a direct answer. Tasks run the full pipeline.

## HTTP API

| Method | Path                             | Description                          |
| ------ | -------------------------------- | ------------------------------------ |
| `GET`  | `/.well-known/agent.json`        | Agent discovery card                 |
| `GET`  | `/health`                        | Service health check                 |
| `GET`  | `/tasks/<session_id>`            | Task status and step results         |
| `GET`  | `/audit?session_id=<id>&last=20` | Query audit log                      |
| `POST` | `/run`                           | Run a task synchronously             |
| `POST` | `/run?distributed=1`             | Run through the distributed pipeline |
| `POST` | `/run?distributed=1&async=1`     | Start an async distributed run       |
| `POST` | `/agents/<agent>/message/send`   | Send a protocol message to an agent  |

### Example: Run a task via HTTP

```bash
curl -X POST http://127.0.0.1:8000/run \
  -H "Content-Type: application/json" \
  -d '{"prompt": "analyze the test suite", "mode": "private"}'
```

### Example: Poll task status

```bash
curl http://127.0.0.1:8000/tasks/<session-id>
```

## Speculative Decoding

This is the core of how OpenCAC keeps costs down without sacrificing quality. Instead of sending every token through an expensive cloud API, the pipeline runs on local llama.cpp servers with speculative decoding enabled -- generating tokens 2-4x faster than naive autoregressive inference on the same hardware.

The quality of individual local outputs doesn't need to be perfect. The pipeline's critique layer (Codex reviews every plan before execution) catches bad outputs before they cause damage, so you get the cost savings of local inference with the safety net of structured validation.

OpenCAC generates ready-to-use `llama-server` launch commands with the right speculation flags. This is built into the pipeline config, not a separate tool.

```bash
opencac run "task" --mode private \
  --spec-type ngram-simple \
  --draft-max 64 \
  --draft-min 16 \
  --spec-ngram-size-n 12
```

Two strategies are supported:

- **Self-speculative** (default): n-gram cache on the main model, no draft model needed -- zero additional VRAM cost
- **Draft-model**: pair a small draft model with the main model for higher acceptance rates (`--draft-model gpt-oss:small-draft`)

The strategy is selected automatically based on available draft model candidates, or forced via `--speculative-mode`. Either way, the generated `llama-server` command and strategy choice are persisted in the session artifacts for reproducibility.

## Output

After a run, two things matter:

- **`artifacts/<session-id>/`** -- `plan.json` (the execution plan) and `result.md` (the summary with routing info, strategy, and step results)
- **`.a2a/audit.jsonl`** -- every event from the run, one JSON object per line

## Testing

```bash
. .venv/bin/activate
PYTHONPATH=src pytest -q
```

32 tests covering the full pipeline, HTTP endpoints (sync, distributed, async), CLI interactive mode, sidecar validation, session resume, callback posting, and local LLM probe mocking. All tests run against real (mocked) HTTP servers -- no `unittest.mock.patch` on network calls.

## Security

- **Private mode** enforces loopback-only on all endpoints, callback URLs, and distributed targets
- **Sidecar validation** rejects unknown agents, message types, and malformed payloads before they reach any handler
- **Blocked command list** prevents `rm -rf /`, `shutdown`, `reboot`, `mkfs`, and fork bombs from executing
- **Private guard** script must be explicitly enabled before private mode will run

See [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Short version: branch, test, PR.

## License

MIT. See [LICENSE](LICENSE).

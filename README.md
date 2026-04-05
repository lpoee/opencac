# OpenCAC

**Turn Claude Code, Antigravity, and Codex into one automated pipeline** -- with protocol validation, audit logging, and flexible routing across cloud APIs, local LLMs, or both.

## The Problem

There is no standard orchestration layer for AI coding agents. Today you use Claude Code, Codex, and Gemini/Antigravity as separate tools, manually copying context between them. There is no validation between steps, no audit trail, and no way to resume when something fails.

On top of that:

- **Cloud API tokens are expensive.** A complex multi-agent task that chains research, planning, and execution burns through tokens fast.
- **Local LLMs are cheap but weak on their own.** A single local model can't reliably handle a full multi-step coding workflow end to end.

OpenCAC solves both problems. It wraps multiple agents into a structured pipeline where each layer critiques the one above it before passing work downstream. When running locally, speculative decoding keeps inference fast, and the pipeline's built-in critique layer catches low-quality outputs before they cause damage -- so you save tokens without giving up safety. Cloud mode works just as well when you have the budget and want the strongest models.

## What It Does

### 1. Four-role pipeline

```
dispatcher → antigravity (research) → claude-code (plan) → codex (execute)
```

Every layer produces a structured protocol envelope. The downstream agent critiques and validates the upstream output before acting on it. Codex runs `assess_plan` before execution -- dangerous commands get rejected, not blindly run.

### 2. Three routing modes

| Mode        | When to use                                                                                                         |
| ----------- | ------------------------------------------------------------------------------------------------------------------- |
| **private** | All traffic stays on loopback. Private guard script must be enabled. For sensitive code or air-gapped environments. |
| **cloud**   | Routes through cloud API tokens. No local infrastructure needed.                                                    |
| **hybrid**  | Cloud-first, automatic fallback to local LLM endpoints when tokens are missing.                                     |

### 3. Local LLM with speculative decoding

Each pipeline role can point to its own llama.cpp server endpoint. OpenCAC generates ready-to-use `llama-server` launch commands with the right speculation flags (n-gram cache or draft model). Before the pipeline starts, a probe verifies each endpoint is alive using constrained grammar -- no silent failures.

The critique layer is what makes local LLMs viable for the full pipeline: individual outputs don't need to be perfect because bad plans get caught and rejected before execution.

### 4. Sidecar protocol validation

Every hop between agents passes through the Sidecar, which validates:

- Agent whitelist (only known agents can send/receive)
- Message type whitelist (instruction, research_report, plan, critique, exec_result, etc.)
- Required payload fields per message type
- Blocked command list (`rm -rf /`, `shutdown`, `mkfs`, fork bomb)
- Private mode: loopback-only enforcement on all URLs including callbacks

### 5. JSONL audit trail

One JSON line per action -- timestamped, tagged with session ID and event kind. Filter by session, query the last N entries, and resume interrupted sessions by replaying from the audit log (completed steps are skipped automatically).

### 6. CLI + HTTP, sync + async

- **CLI**: `opencac run "task"`, `opencac audit`, `opencac resume <session-id>`, interactive REPL
- **HTTP**: `POST /run`, `GET /tasks/<id>`, per-agent message endpoints, agent discovery card at `/.well-known/agent.json`
- **Distributed mode**: CLI routes through the HTTP service. Supports both synchronous and async execution with status polling.

### 7. Smart question routing in interactive mode

The interactive CLI detects whether input is a question or a task:

- Questions (`?`, `who/what/how`, Chinese question words) go straight to a QA path -- no pipeline overhead
- Tasks run the full research-plan-critique-execute pipeline
- Questions containing evidence keywords (`docs`, `code`, `error`, `test`) trigger a research step before answering

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

All agent endpoints must be on loopback. Requires the `opencac-private-guard` script to be enabled. No network calls leave the machine.

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

When running in private or hybrid mode with local llama.cpp endpoints, speculative decoding makes local inference practical for the full pipeline. Instead of paying per-token cloud costs, you get 2-4x faster generation on the same hardware -- and the pipeline's critique layer catches low-quality outputs before they reach execution.

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
- **`.opencac/audit.jsonl`** -- every event from the run, one JSON object per line

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

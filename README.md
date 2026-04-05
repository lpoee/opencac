# OpenCAC

A CLI and HTTP service that wires Claude Code, Antigravity, and Codex into a single research → plan → critique → execute pipeline. Runs on cloud APIs, local LLMs, or both.

## Why this exists

If you're using multiple AI coding agents, you already know the drill: copy output from one tool, paste it into the next, hope nothing breaks in between, and when it does, good luck figuring out where things went wrong.

That's the first problem -- **no orchestration layer.** Each agent is its own island.

The second problem is cost. **Cloud tokens add up fast** when you're chaining research, planning, and execution across three different models. But **local LLMs can't do the whole job alone** -- they're good enough for parts of the workflow, not all of it.

OpenCAC tackles both. It gives the agents a shared protocol so they talk to each other through validated, structured messages instead of raw text. Each stage critiques the one before it. And the whole thing can run on local llama.cpp endpoints with speculative decoding -- the pipeline's critique step catches bad outputs before they hit execution, so local quality issues don't snowball. When you want cloud models, just flip the mode.

## What it does

**Pipeline.** Four roles, one after another:

```
dispatcher → antigravity (research) → claude-code (plan) → codex (execute)
```

Each role produces a structured envelope. The next role validates it and pushes back if something looks wrong. Codex won't execute a plan until `assess_plan` signs off -- dangerous commands get rejected, not run.

**Routing.** Three modes depending on what you have available:

| Mode      | What happens                                                              |
| --------- | ------------------------------------------------------------------------- |
| `private` | Everything on loopback. Nothing leaves the machine.                       |
| `cloud`   | Goes through cloud API tokens.                                            |
| `hybrid`  | Tries cloud first, falls back to local endpoints when tokens are missing. |

**Speculative decoding for local LLMs.** Each role can point to its own llama.cpp server. OpenCAC generates the `llama-server` command with the right spec flags (n-gram cache or draft model) and probes each endpoint before the pipeline starts. The critique layer is what makes this practical -- local outputs don't need to be perfect because bad plans get caught before execution.

**Protocol validation.** A sidecar sits between every hop and checks agent names, message types, required payload fields, and a blocked command list. Private mode enforces loopback-only on everything, including callback URLs.

**Audit log.** Append-only JSONL -- one line per event, timestamped, tagged with session ID. You can filter by session, pull the last N entries, or resume a crashed run from the log. Completed steps get skipped automatically.

**CLI + HTTP.** Interactive REPL, one-shot `opencac run`, or a full HTTP service with `POST /run`, task polling, and per-agent message endpoints. The CLI can route through the HTTP service for distributed execution (sync or async).

**Question detection.** In interactive mode, questions (`?`, `who/what/how`, Chinese question words) skip the pipeline and get answered directly. If the question mentions `docs`, `code`, `error`, or `test`, it runs a research step first.

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

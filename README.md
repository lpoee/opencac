# OpenCAC

OpenCAC is a CLI and HTTP app for running a multi-step agent workflow with a clear execution trail.

It is built for a common pain point: you want one command that can research, plan, and execute work, but you also want to know what happened, what was written, and why it failed when it fails.

OpenCAC gives you:

- a single CLI entrypoint: `opencac`
- a local HTTP service for distributed runs
- a JSONL audit log for every atomic state change
- resumable sessions
- local, cloud, and hybrid model routing

## Why People Use It

Most agent tools fail in one of these ways:

- they hide too much of the process
- they are hard to run locally
- they break when cloud credentials are missing
- they do work but leave weak audit trails

OpenCAC is designed to be easier to trust and easier to debug:

- every step is logged
- artifacts are written to disk
- local model endpoints are supported directly
- cloud mode can fall back to local mode when configured

## How It Works

OpenCAC runs four roles:

- `user`
- `researcher`
- `planner`
- `implementer`

Each layer critiques the layer above it before passing work downstream.

Supporting pieces:

- `Sidecar` validates protocol envelopes and payloads
- `audit.jsonl` records one atomic event per line
- the HTTP service exposes runs, task status, audit access, and agent endpoints

## Quick Start

Install in a virtual environment:

```bash
git clone https://github.com/lpoeeo/opencac.git
cd opencac
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

Start the interactive CLI:

```bash
opencac
```

Run one task:

```bash
opencac run "analyze this repository and propose the next change" --mode private
```

Start the local HTTP service:

```bash
opencac serve --host 127.0.0.1 --port 8000
```

Run through the HTTP service:

```bash
opencac run "execute through the HTTP service" --mode private --distributed --base-url http://127.0.0.1:8000
```

## Installation Options

### Option 1: pip

For most users:

```bash
git clone https://github.com/lpoeeo/opencac.git
cd opencac
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

### Option 2: Docker

Build the image:

```bash
docker build -t opencac .
```

Run the service:

```bash
docker run --rm -p 8000:8000 -v "$(pwd)/data:/data" opencac
```

Run the CLI help in the container:

```bash
docker run --rm opencac --help
```

## Model Setup

OpenCAC supports three practical setups.

### 1. Local-only

Use this if you already have local inference endpoints.

```bash
export A2A_ANTIGRAVITY_URL=http://127.0.0.1:18101
export A2A_CLAUDE_CODE_URL=http://127.0.0.1:18102
export A2A_CODEX_URL=http://127.0.0.1:18103
opencac run "start work" --mode private
```

Use this mode when:

- you want local-only execution
- you do not want to rely on cloud APIs
- you already run local model servers

### 2. Cloud-only

Use this if you do not have local models.

```bash
export A2A_ANTIGRAVITY_TOKEN=your-token
export A2A_CLAUDE_CODE_TOKEN=your-token
export A2A_CODEX_TOKEN=your-token
opencac run "start work" --mode cloud
```

Use this mode when:

- you want the simplest onboarding path
- you have cloud credentials
- you do not want to maintain local model infrastructure

### 3. Hybrid cloud with local fallback

Use this if you want cloud-first behavior but do not want the whole run to fail when cloud credentials are unavailable.

```bash
export A2A_ANTIGRAVITY_TOKEN=your-token
export A2A_CLAUDE_CODE_TOKEN=your-token
export A2A_CODEX_TOKEN=your-token
export A2A_ANTIGRAVITY_URL=http://127.0.0.1:18101
export A2A_CLAUDE_CODE_URL=http://127.0.0.1:18102
export A2A_CODEX_URL=http://127.0.0.1:18103
export A2A_CLOUD_FALLBACK_LOCAL=1
opencac run "start work" --mode cloud
```

Use this mode when:

- you want better resilience
- you have both cloud access and local endpoints
- you want cloud reasoning with local backup paths

## If You Do Not Have Local Models

That is fine. Use cloud mode.

```bash
export A2A_ANTIGRAVITY_TOKEN=your-token
export A2A_CLAUDE_CODE_TOKEN=your-token
export A2A_CODEX_TOKEN=your-token
opencac run "start work" --mode cloud
```

Do not use `--mode private` unless local model endpoints are available.

If you have neither local endpoints nor cloud tokens, OpenCAC will fail at runtime. The current implementation requires at least one working execution path.

## Files You Will Care About

After a run, the two most important outputs are:

- `artifacts/<session-id>/`
- `.a2a/audit.jsonl`

Useful commands:

```bash
opencac audit --last 20
```

```bash
opencac resume <session-id>
```

## HTTP API

Start the service:

```bash
opencac serve --host 127.0.0.1 --port 8000
```

Main endpoints:

- `GET /.well-known/agent.json`
- `GET /tasks/<session_id>`
- `GET /audit?session_id=<id>&last=20`
- `POST /run`
- `POST /run?distributed=1`
- `POST /agents/<agent>/message/send`

## Testing

Run the test suite:

```bash
. .venv/bin/activate
PYTHONPATH=src pytest -q
```

Current baseline:

- `32` tests passing

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md).

Short version:

1. Create a branch.
2. Run `PYTHONPATH=src pytest -q`.
3. Make a focused change.
4. Add or update tests.
5. Open a pull request with a clear explanation.

## Security

See [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

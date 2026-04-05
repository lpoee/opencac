# Contributing to A2A Fabric

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
PYTHONPATH=src pytest -q
```

## Contribution Rules

- Keep changes focused.
- Preserve protocol compatibility unless the change is intentional and documented.
- Add or update tests for every behavioral change.
- Do not merge broken docs, broken packaging metadata, or failing tests.

## Pull Requests

Every pull request should include:

- what changed
- why it changed
- how it was tested
- any compatibility impact

## Test Expectations

Before opening a pull request, run:

```bash
PYTHONPATH=src pytest -q
```

If you change CLI behavior, include at least one CLI test.

If you change protocol flow, include audit assertions.

## Design Principles

- `user -> researcher -> planner -> implementer`
- each layer critiques the previous layer
- sidecar validation is mandatory
- `audit.jsonl` is the replay and recovery source of truth

## Documentation

Update `README.md` when you change:

- install steps
- CLI behavior
- public HTTP endpoints
- configuration variables

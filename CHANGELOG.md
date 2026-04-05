# Changelog

## 0.2.0

- **Agent integration**: Antigravity (Gemini) research via JSON-RPC, Claude Bridge planning via Anthropic messages API, Codex CLI code generation
- New env vars: `OPENCAC_RESEARCH_URL`, `OPENCAC_PLANNER_URL`, `OPENCAC_CODEX_BINARY`
- New `generate` plan action for AI-assisted code generation via Codex CLI
- Graceful fallback: real agents tried first, local heuristics on failure
- Fix: distributed session timestamp bug (was literal string, now ISO 8601)
- Fix: CONTRIBUTING.md title (was "A2A Fabric", now "OpenCAC")

## 0.1.0

- initial public packaging metadata
- public repository documentation and contribution files
- GitHub Actions CI workflow
- issue and pull request templates
- deterministic local test shard support for CI and contributor environments

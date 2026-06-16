## ADDED Requirements

### Requirement: CI Gates the Built-Wheel Import

CI SHALL build the `bloommcp` wheel (wheel-only, resolving to exactly one artifact)
with `uv build` and import it from a clean, project-free environment that does not see
the `bloommcp/src/` tree, so a packaging regression that ships an unimportable wheel
fails the PR. The import SHALL cover `bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`,
and `bloom_mcp.server`, SHALL assert the imported package resolves from the installed
wheel and not the source checkout, and SHALL run with `SUPABASE_URL` and `BLOOM_AGENT_KEY`
set empty (no usable Supabase env) so the lazy-validation contract is load-bearing.
CI SHALL retain a regression-guard test asserting the gate's presence so it cannot be
silently deleted. Built artifacts (`bloommcp/dist/`) SHALL NOT be committed.

#### Scenario: Clean-env wheel import is gated by CI

- **WHEN** the `python-audit` job runs `uv build --wheel` in `bloommcp/` and
  imports the resulting wheel via `uv run --no-project --with <wheel>
  python -c "import bloom_mcp, bloom_mcp.tools, bloom_mcp.storage, bloom_mcp.server;
  assert 'site-packages' in bloom_mcp.__file__"`
- **THEN** the import resolves the shipped wheel (the `__file__` assertion proves it is
  not the `src/` checkout) and a packaging regression — a misconfigured `module-name` /
  `module-root`, a dropped `__init__.py`, or a wheel that ships an empty namespace —
  fails the job

#### Scenario: Wheel import gate runs with no Supabase env

- **WHEN** the clean-env import runs with `SUPABASE_URL` and `BLOOM_AGENT_KEY` set empty
- **THEN** `import bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`, and
  `bloom_mcp.server` succeed and raise no `RuntimeError`, proving no import-time Supabase
  dependency

#### Scenario: Gate presence is regression-guarded

- **WHEN** the `tests/unit/` suite parses `.github/workflows/pr-checks.yml`
- **THEN** it asserts the `python-audit` job contains a step that runs `uv build` in
  `bloommcp`, imports `bloom_mcp` from a `--no-project` environment, and pins
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` empty — failing the PR if the gate is removed

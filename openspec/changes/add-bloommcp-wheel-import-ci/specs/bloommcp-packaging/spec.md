## ADDED Requirements

### Requirement: CI Gates the Built-Wheel Import

CI SHALL build the `bloommcp` wheel and import it from a clean environment that cannot
see the `bloommcp/src/` tree, so a packaging regression that ships an unimportable wheel
fails the PR. The import SHALL cover `bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`,
and `bloom_mcp.server`, SHALL verify the imported package resolves from the installed
wheel and not the source checkout, and SHALL run with no usable Supabase environment
(`SUPABASE_URL` / `BLOOM_AGENT_KEY` empty) so the lazy-validation contract is
load-bearing. CI SHALL retain a regression-guard test asserting the gate's presence and
its load-bearing assertions so it cannot be silently deleted or hollowed out. Built
artifacts (`bloommcp/dist/`) SHALL NOT be committed. (The exact `uv` invocation and the
rationale for omitting `--isolated` live in the proposal and tasks, not this contract.)

#### Scenario: Clean-env wheel import is gated by CI

- **WHEN** the `python-audit` job builds the wheel and imports `bloom_mcp` and its
  `tools`, `storage`, and `server` submodules in an environment that does not place
  `bloommcp/src/` on the import path
- **THEN** the import resolves the shipped wheel (verified by checking the imported
  package's file location, not the `src/` checkout) and a packaging regression — a
  misconfigured `module-name` / `module-root`, a dropped `__init__.py`, or a wheel that
  ships an empty namespace — fails the job

#### Scenario: Wheel import gate runs with no Supabase env

- **WHEN** the clean-env import runs with `SUPABASE_URL` and `BLOOM_AGENT_KEY` set empty
- **THEN** `import bloom_mcp`, `bloom_mcp.tools`, `bloom_mcp.storage`, and
  `bloom_mcp.server` succeed and raise no `RuntimeError`, proving no import-time Supabase
  dependency

#### Scenario: Gate presence is regression-guarded

- **WHEN** the `tests/unit/` suite parses `.github/workflows/pr-checks.yml`
- **THEN** it asserts the `python-audit` job contains a step that builds the wheel in
  `bloommcp`, imports all four modules from a project-free environment, installs the
  built wheel, verifies the import resolved from the wheel (not `src/`), and pins
  `SUPABASE_URL` / `BLOOM_AGENT_KEY` empty — failing the PR if the gate is removed or any
  of its load-bearing assertions is dropped

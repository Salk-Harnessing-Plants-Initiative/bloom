## Why

The `bloommcp-result-store` capability (#323) promises that a committed run lands in Supabase Storage with a **v3** manifest whose `seed` / `agent` / `environment` / `output_sha256` / `output_keys` are populated, and that each recorded `output_sha256` equals the SHA-256 of the **bytes actually stored**. **No automated check exercises that real write path.** The entire Tier-2 suite (`60 passed`) runs against in-memory fakes — `FakeResultStore` + `_InMemoryObjectStore` ([bloommcp/tests/conftest.py](bloommcp/tests/conftest.py)) — so CI never confirms the guarantee against a live storage-api + MinIO round-trip: that "hash == uploaded bytes" survives real upload/download, that `get_run("latest")` reads a committed run back, or that v3 provenance round-trips through storage.

The stack to run this on is already green: the `dev-stack-smoke` job ([.github/workflows/pr-checks.yml:758](.github/workflows/pr-checks.yml#L758)) brings up Supabase + MinIO + storage-api via `make init → dev-up → migrate-local → check` cleanly. This change *adds the live-persistence assertions on top of that working stack* — it does not fix the stack. Per the issue's mandate, the smoke is **one reusable `make bloommcp-smoke` target** so local pre-merge and CI run identical assertions and cannot drift. Tracked out of the `/review-pr` of #323 (refs #323, #307).

## What Changes

- **ADD** `bloommcp/scripts/live_persistence_smoke.py` — a maintained smoke driver (promoted from the untracked scratch `bloommcp/_live_smoke.py`, which is deleted). Against the running dev stack, through the **real** `SupabaseReader` + `SupabaseResultStore`, it:
  1. Verifies the **Tier-0 import-clean guarantee** first, in a subprocess with `SUPABASE_URL` / `BLOOM_AGENT_KEY` scrubbed from the environment, *before* configuring the live env.
  2. Drives `run_clustering_workflow("turface.csv", algorithm="kmeans")` (resolves `seed=42`) end-to-end through the ports.
  3. Reads `manifest.json` **back from storage** and asserts schema version **== 3** with the latest `VersionEntry` carrying `seed == 42`, `agent == "bloom_agent"`, a populated `environment`, and non-empty `output_sha256` / `output_keys`.
  4. Downloads each stored object and asserts `sha256(bytes) == output_sha256[logical]`.
  5. Calls `store.get_run(experiment, tool_class, "latest")`, then runs the workflow a **second** time and asserts `latest` advances `v1 → v2`.
  6. Routes every failure (including a workflow error or a read-after-write miss after a bounded retry) through a per-check summary and `sys.exit(1)`.
  Its pure logic (manifest parse, hash-compare loop, version-advance, summary/exit aggregation) is factored into importable helpers so it is unit-testable against the existing `fake_supabase_storage` fixture with no live stack.
- **ADD** a **`make bloommcp-smoke`** target ([Makefile](Makefile)) that bridges the host↔container env gap against an already-up + migrated stack and runs the driver. It derives the host-reachable gateway from `.env.dev`'s `KONG_HTTP_PORT` (`SUPABASE_URL=http://localhost:$KONG_HTTP_PORT`; `.env.dev` itself points the in-container value at `http://kong:8000`), points `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` at host temp dirs seeded with the `turface_19_final_data.csv` fixture, and sources `BLOOM_AGENT_KEY` from `.env.dev` (mirroring `migrate-local`'s `sed`-with-default idiom; the key is never echoed). It fails fast with actionable messages if the stack is not up / `.env.dev` is missing / `uv` is absent. The `bloommcp-data` bucket is **not** provisioned here — it is created by migration `20260605000000_create_bloommcp_data_bucket.sql` (`ON CONFLICT DO NOTHING`), which `make migrate-local` applies. The target gets a `##` doc-comment and a line in the `help` block.
- **ADD** a `make bloommcp-smoke` **step to the existing `dev-stack-smoke` job** in [.github/workflows/pr-checks.yml](.github/workflows/pr-checks.yml), after the `make migrate-local`/`make check` steps and before cleanup — reusing the already-green stack instead of a second `dev-up`. The step name gives failure attribution; the existing `if: failure()` debug-logs dump is broadened to `storage` / `db-dev` / `supabase-minio` / `kong` (the smoke's failure surfaces), and the existing `if: always()` `down -v` cleanup is retained. (See design.md Decision 3 for why a step, not a sibling job.)
- **ADD** a regression-guard unit test (`tests/unit/test_bloommcp_live_smoke_gate.py`) that parses `pr-checks.yml` and asserts — by step presence and relative order, never a fixed index — that the dev-stack job runs `make migrate-local` **before** `make bloommcp-smoke` and retains an `if: always()` teardown. Mirrors the `add-bloommcp-wheel-import-ci` guard; reuses `_iter_steps` (from `tests/unit/test_ci_workflow_uv_conventions.py`) and `_logical_lines` (from `tests/unit/_workflow_helpers.py`).
- **REFERENCE** `make bloommcp-smoke` from [.claude/commands/pre-merge.md](.claude/commands/pre-merge.md) as a bloommcp-specific live-persistence step (a new bullet under the integration/stack phase) so local and CI invoke the identical recipe.
- **EXTEND** the `bloommcp-result-store` spec with two ADDED requirements — "Live Supabase Persistence Smoke" (the behavioral write/read guarantees) and "Persistence Smoke CI Gate" (the shared-target delivery + regression guard).

## Impact

- **Affected specs**: `bloommcp-result-store` (ADDED only — composes with #323's capability regardless of merge order).
- **Affected code**:
  - `bloommcp/scripts/live_persistence_smoke.py` — new (creates `bloommcp/scripts/`; no `__init__.py`, run as a path). Deletes untracked `bloommcp/_live_smoke.py`.
  - `bloommcp/tests/scripts/test_live_persistence_smoke_logic.py` — new (pure-logic unit tests against `fake_supabase_storage`).
  - `Makefile` — one new `bloommcp-smoke` target + `help`/doc-comment lines.
  - `.github/workflows/pr-checks.yml` — one step added to `dev-stack-smoke` + broadened failure-debug dump.
  - `tests/unit/test_bloommcp_live_smoke_gate.py` — new regression guard.
  - `.claude/commands/pre-merge.md` — one bloommcp step.
  - `bloommcp/README.md` — one-line pointer under Development.
- **Affected CI**: no new job — one step on the existing `dev-stack-smoke` job, plus the host-side bloommcp `uv` resolve that step's `uv run` triggers (first time on the runner host for this path; bounded, no second `dev-up`).
- **Dependency on #323**: `SupabaseResultStore`, `SupabaseReader`, the `_ports` composition root, and the v3 schema exist only on #323's branch (`egao28/bloommcp-tier2-persistence`). This change stacks on that branch (PR base = that branch, not `staging`) and **cannot merge before #323**.
- **Archive order**: the `bloommcp-result-store` capability lives only as the un-archived `add-bloommcp-persistence-ports` delta. Both changes are ADDED-only, so `validate --strict` and merge order are unaffected, but archiving MUST do `add-bloommcp-persistence-ports` first (canonical spec), then this change.

## Non-goals

- Not testing the prod/staging migration runner or the `compose-health-check` (prod compose) path — the storage-schema GRANT repair this relies on is applied by `make migrate-local` for the dev stack; the prod-runner equivalent is tracked separately ([Makefile:261-272](Makefile#L261-L272)).
- Not asserting on every workflow — one stochastic workflow (clustering/kmeans) exercises the seed + multi-artifact + hash path; broader coverage is a follow-up.
- Not editing `bloommcp/pyproject.toml` packaging or the `_ports` composition root — this change only exercises them.
- Not creating a new skill (the issue forbids it) or editing `openspec/project.md` / `CLAUDE.md`.

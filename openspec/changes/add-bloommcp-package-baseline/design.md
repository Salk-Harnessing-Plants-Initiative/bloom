## Context

`bloommcp/` is a flat `uv` **virtual** project (`bloommcp/uv.lock:90` →
`source = { virtual = "." }`): `source/` (vendored sklearn/scipy/statsmodels/umap
analysis), `tools/` (MCP tool wrappers + `workflows/`), `storage/` (the deployed
`AnalysisWriter`/`AnalysisDir`/manifest/versioning layer), and `server.py`. There is no
package boundary and no test stack. `source/supabase_client.py` validates env at
**import** ([supabase_client.py:48-60](../../../bloommcp/source/supabase_client.py)), and
`server.py` imports it specifically for that fail-fast side effect
([server.py:34](../../../bloommcp/server.py)). **34 intra-package import statements
across 14 files** use the bare `source|tools|storage` roots; **13 root `tests/` files**
(6 `unit/`, 7 `integration/`) inject `bloommcp/` onto `sys.path` and import these
modules. The container runs `CMD ["python", "server.py"]` from `WORKDIR /app` with deps
synced *before* `COPY . .` (works today only because the project is virtual). Tier 0
establishes the package + test foundation Tiers 1–4 build on.

## Goals / Non-Goals

- **Goals:** installable `src/bloom_mcp/` uv package (`uv build` + importable wheel);
  Supabase-free importability + unit tests, gated by CI; committed
  `talmolab/sleap-roots-analyze#120` turface_19
  oracle with independent golden values; container/compose entry point updated for the
  `src/` layout; **zero** server behavior regression.
- **Non-Goals:** delegating the vendored analysis to `sleap-roots-analyze` (rewriting
  `tools/*` to call `sleap_roots_analyze.perform_*` and deleting `source/*`) and the
  dependency **pruning** that delegation would enable — explicitly a later tier.
  Changing tool semantics or storage layout; touching the `langchain` agent or web app.

## Decisions

- **Decision: additive only — keep all current deps, add two.** The move is `git mv`,
  not a rewrite: the vendored `source/*` still imports sklearn/scipy/statsmodels/umap/
  seaborn at module scope, and `tools/workflows/clustering.py` imports sklearn directly,
  so **no dependency is droppable** in this change. We add `sleap-roots-analyze` and
  `sleap-roots-contracts[pandas]` as the Phase-2 foundation and the oracle's source of
  `perform_*`. "Necessary-and-sufficient" is satisfied because every retained dep is
  still imported by shipped code. *Alternative considered:* delegate + prune now —
  rejected; it is a behavior-changing refactor that belongs in its own tier with its own
  oracle gate, and folding it in here would make the move un-reviewable.
- **Decision: independent oracle, explicit assertions, on shipped + external code.**
  Golden values come from the `talmolab/sleap-roots-analyze#120` / PR #146 recording,
  **not** re-derived from `sleap-roots-analyze`; otherwise the oracle (library output vs
  golden) would be a tautology. Assert explicit numeric values with tolerances against
  **both** the external `sleap_roots_analyze.pca` (cutover-target check) and the shipped
  `bloom_mcp` PCA / clustering / correlation paths (the numpy-2 regression guard for the
  code the server actually runs). Do **not** use syrupy auto-snapshot for the oracle
  (snapshot-on-first-run records whatever the code emits and can never be RED-first).
- **Decision: real package + entry point + Dockerfile two-stage sync.** Add a
  `[build-system]` (uv_build with `module-root = "src"`, or hatchling
  `packages = ["src/bloom_mcp"]`) and a `[project.scripts]` / `python -m bloom_mcp`
  entry. Flipping virtual→package breaks the cache-first Dockerfile (`uv sync` before
  `COPY . .`), so split it: a deps layer (`uv sync --frozen --no-install-project`) then a
  project install after `COPY . .`. Update `CMD`. *Alternative:* `[tool.uv] package =
  false` to stay virtual — rejected, the issue requires a publication-ready, buildable
  package.
- **Decision: editable install for dev hot-reload.** The dev compose bind-mount
  (`./bloommcp:/app`) must still reflect edits; a `src/` package needs an editable
  install (or `PYTHONPATH=/app/src`) or hot-reload silently breaks. Verify via the
  existing `dev-stack-smoke` job.
- **Decision: tests in `bloommcp/tests/` + a new CI job.** Package-local so
  `cd bloommcp && uv run pytest` is self-contained; ensure repo-root `testpaths=["tests"]`
  never collects it. Add a `bloommcp-tests` job to `pr-checks.yml` — today nothing runs
  the bloommcp suite, so without it the oracle gate is decorative.

## Risks / Trade-offs

- **numpy major-version jump** → `sleap-roots-analyze` requires `numpy>=2.3.2` vs the
  current `>=1.24`; the vendored sklearn/scipy code has never run on numpy 2.x here.
  Mitigation: the oracle pins **numeric** outputs of the shipped PCA / clustering /
  correlation paths (not just "doesn't crash") under the numpy-2 lock; the full suite
  (6.3) runs against that lock. UMAP and the shape-only integration tests remain
  numeric-unverified — called out, not silently claimed as equivalent.
- **Root-suite breakage from the move** → 13 root test files break at collection until
  their `sys.path` target moves to `bloommcp/src` and imports become `bloom_mcp.*`; two
  tests assert the import-time `RuntimeError` being removed and must be rewritten (3.3).
  The move + root-test fixup land adjacently so the CI-gated root suite never stays red.
- **Container boot regression** → Dockerfile/compose changes are covered by the boot
  fail-fast test (1.5), the `/health` check under both compose files (6.2), and
  `dev-stack-smoke`.
- **pip-audit surface grows** (sleap-roots-analyze pulls plotly/networkx/openpyxl/…) →
  the `pip-audit` step (6.4) may need new `--ignore-vuln` entries; treat as a follow-up,
  not a guaranteed break.
- **Dep pruning removes something still imported** → not applicable this tier (Non-Goal);
  `uv build` + clean-env wheel import (4.5) guards against a missing runtime dep anyway.

## Migration Plan

1. Land oracle/acceptance tests RED in `bloommcp/tests/` (not CI-collected at root).
2. Pure `git mv` rename commit, then mechanical import rewrite + root-test fixup
   (adjacent, keep root suite green). 3. Build-system + entry point. 4. Lazy validation +
   explicit boot call + rewrite import-raise tests. 5. Deps (additive) + locks. 6.
   Dockerfile/compose + CI job + docs. 7. Full-suite + boot + `/health` gate. Rollback =
   revert the PR; no schema/runtime data migration.

## Resolved during implementation

- **`server.py` placement** → moved under `src/bloom_mcp/server.py` with a `main()`
  entry + `__main__.py`; Dockerfile `CMD` is `python -m bloom_mcp`.
- **Build backend** → `uv_build` with `module-name = "bloom_mcp"`, `module-root = "src"`.
- **`experiment_utils` import-time dir gate** (raised in review) → made lazy the same way
  as Supabase (tolerant module constants + `validate_env()` called at boot).
- **Branch prefix** → kept `eberrigan/bloommcp-tier0-baseline` (per the issue); approver
  is a non-author, satisfying branch protection.

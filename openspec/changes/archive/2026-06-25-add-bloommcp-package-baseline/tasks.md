## 1. Oracle & acceptance tests (RED first)

- [x] 1.1 Vendor the `talmolab/sleap-roots-analyze#120` turface_19 fixture +
      independently recorded golden values under `bloommcp/tests/fixtures/` (post-QC
      input CSV + recorded PCA metadata; unasserted `top_features` dropped).
- [x] 1.2 Oracle tests with explicit numeric assertions: both the external
      `sleap_roots_analyze.pca` **and** the shipped `bloom_mcp.pca` reproduce the
      `talmolab/sleap-roots-analyze#120` recorded `n_pca_components` (3) and
      `pca_explained_variance` (≈0.95991) within
      1e-6, plus deterministic shipped k-means + correlation numerics as the numpy-2
      regression guard.
- [x] 1.3 Import test: `import bloom_mcp` succeeds with both Supabase vars unset.
- [x] 1.4 Lazy-access test (parametrized over partial + both-unset): accessor raises
      naming exactly the missing variable.
- [x] 1.5 Boot fail-fast unit test: `validate_env()` raises before any server I/O
      (`mcp.run` spy never called).
- [x] 1.6 Static-guard test: AST scan finds no `source|tools|storage`-rooted imports.
- [x] 1.7 FastMCP `Client` smoke: connects in-process and lists registered tools.
- [x] 1.8 `hypothesis` property: `_validate_name` rejects any string containing `/`.

## 2. Package restructure (GREEN)

- [x] 2.1 `git mv` `source/`, `tools/`, `storage/`, `server.py` into
      `bloommcp/src/bloom_mcp/` (source flattened; rename detection preserved).
- [x] 2.2 Rewrote the 34 intra-package imports → `bloom_mcp.*` (verified none remain).
- [x] 2.3 Added `[build-system]` (uv_build, `module-root = "src"`) + `[project.scripts]
      bloom-mcp` + `__init__.py` / `__main__.py`; `uv build` produces an importable wheel.
- [x] 2.4 Updated `server.py` imports + `main()` entry point.
- [x] 2.5 Updated the 9 bloommcp root tests (`sys.path` → `bloommcp/src`, imports →
      `bloom_mcp.*`). (The 4 `cyl_tools` tests are langchain's, not bloommcp — untouched.)

## 3. Lazy Supabase validation

- [x] 3.1 `_require_env()` / `validate_env()`; env read+validated per call, not at import.
- [x] 3.2 `server.main()` calls `validate_env()` before `mcp.run()`.
- [x] 3.3 Rewrote `test_supabase_client.py` import-raise tests → `validate_env()`
      contract; dropped obsolete import-time env placeholders; added autouse env fixture.

## 4. Dependencies & packaging (additive — no pruning)

- [x] 4.1 Added `sleap-roots-analyze>=0.1.0a2` + `sleap-roots-contracts[pandas]>=0.1.0a1`,
      publication metadata, README; bumped numpy floor to `>=2.3.2`; kept existing
      analysis deps (still imported by vendored modules).
- [x] 4.2 Added the test stack as a `test` extra (`pytest`/`hypothesis`/`syrupy`) — extra,
      not dev-group, to match the repo-wide `uv run --extra test` convention.
- [x] 4.3 Root `tests/` resolve unchanged — they import only vendored `bloom_mcp` modules
      (not delegated code), so the root `test` extra needs no new deps. **Deviation from
      the proposal:** sleap-roots-analyze is NOT added to the root extra (unnecessary).
- [x] 4.4 `uv lock` (bloommcp + root); `scripts/check-uv-locks.py` green; `uv lock --check`
      green.
- [x] 4.5 `uv build` + wheel ships `bloom_mcp/`; import smoke green with Supabase unset.

## 5. Deploy infra & docs

- [x] 5.1 `Dockerfile`: split sync into deps layer (`--no-install-project`) + project
      install after `COPY . .`; `CMD ["python", "-m", "bloom_mcp"]`; copy README at deps
      layer. **Not executed in this env:** `docker build` (no Docker in sandbox).
- [x] 5.2 Compose needs no edit — neither file overrides `command:` (both inherit the new
      Dockerfile CMD); editable install + venv at `/opt/venv` preserve dev hot-reload and
      prod `read_only`. **Not executed here:** live `/health` under compose.
- [x] 5.3 Added `bloom_mcp package tests` step to `pr-checks.yml` `python-audit` job
      (`uv run --frozen --extra test pytest`, Supabase unset).
- [x] 5.4 Doc sync: `_WIKI/BLOOMMCP/*` link paths + `from source.*` examples →
      `bloom_mcp.*` / `src/bloom_mcp/`; `openspec/project.md` External Packages.

## 6. Verify / pre-merge (oracle is the north star)

- [x] 6.1 `uv run --extra test pytest tests/` (bloommcp) — 11 passed, Supabase unset.
- [ ] 6.2 Server boot + `/health` under dev/prod compose — **not run** (Docker unavailable
      in this environment); covered by reasoning + the boot fail-fast unit test (1.5).
- [x] 6.3 Root `tests/unit/` — 210 passed, 1 skipped; `tests/integration/` collects (64).
      Full integration run needs the live compose stack (compose-health-check job).
- [x] 6.4 `openspec validate --strict` valid; black + ruff clean over `bloommcp/src` +
      `tests`; `uv lock --check` green. (Pre-existing vendored ruff debt scoped via
      `per-file-ignores`; the one real bug — undefined `Any` — fixed.)

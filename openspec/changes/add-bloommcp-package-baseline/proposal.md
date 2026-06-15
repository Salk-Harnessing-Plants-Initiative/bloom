## Why

`bloommcp/` is a flat prototype (`source/`, `tools/`, `storage/`, `server.py`) that is
not an installable package and has no test stack. It is a `uv` **virtual** project
(`bloommcp/uv.lock:90` → `source = { virtual = "." }`), and
`source/supabase_client.py` raises `RuntimeError` at **import time** if `SUPABASE_URL`
/ `BLOOM_AGENT_KEY` are unset
([supabase_client.py:48-60](../../../bloommcp/source/supabase_client.py)), so the
package cannot even be imported — let alone unit-tested — without a live Supabase
deploy. Phase 2 (the contract / persistence / tool tiers) needs an installable package
with a real, Supabase-free test stack and a committed cross-tier oracle underneath it.
This is **bloom-mcp Phase 2 · Tier 0** (roadmap Tier 0; advances [#33], foundation for
Tiers 1–4) — see issue
[#305](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/305).

## What Changes

- **Restructure to an installable uv package.** Move `bloommcp/{source,tools,storage}/`
  + `supabase_client.py` into `bloommcp/src/bloom_mcp/`, importable as `bloom_mcp.*`.
  Rewrite the **34 intra-package import statements (14 files)** (`source|tools|storage`
  → `bloom_mcp.*`) and the **13 root `tests/` files** (6 `unit/`, 7 `integration/`)
  that inject `bloommcp/` onto `sys.path` and import these modules. **Additive only —
  the booting server must stay green.**
  - Flip from a `uv` **virtual** project to a real package: add `[build-system]` +
    `src/` package discovery so `uv build` and `import bloom_mcp` work.
  - **Update the container entry point.** `bloommcp/Dockerfile` (`uv sync` runs before
    `COPY . .`, and `CMD ["python", "server.py"]`) and the dev/prod compose bloommcp
    service must be updated for the `src/` layout so the image builds, boots, and
    `/health` stays green (dev hot-reload bind-mount preserved).
- **Make Supabase env-validation lazy.** Defer the `SUPABASE_URL` / `BLOOM_AGENT_KEY`
  check from module import to an explicit `validate_env()` (called at server startup +
  lazily inside the client accessors), so `import bloom_mcp` and the fakes-based unit
  tests run with **no Supabase**.
  - **BREAKING** (internal import-time contract only): `server.py` can no longer rely on
    importing `supabase_client` for fail-fast; it MUST call `validate_env()` before
    `mcp.run()` so a misconfigured deploy still fails fast at container boot. The two
    existing tests that assert the *import-time* raise
    (`tests/unit/test_supabase_client.py:108-130`) MUST be rewritten to the
    `validate_env()` contract.
- **Add dependencies (additive).** Add `sleap-roots-analyze>=0.1.0a2` and
  `sleap-roots-contracts[pandas]>=0.1.0a1` to `bloommcp/pyproject.toml` and to the root
  `tests/` `test` extra. These are the foundation Tiers 1–4 delegate to and the source
  of the oracle's `perform_*` functions. **Existing analysis deps stay** — the vendored
  `source/*` modules still import them directly (sklearn/scipy/statsmodels/umap/seaborn);
  delegation + dep-pruning is a Non-Goal, deferred to the delegation tier. Adding
  `sleap-roots-analyze` raises the numpy floor to `>=2.3.2`, so the full suite is the
  gate for numpy-2 compatibility.
- **Add the dev/test stack:** `pytest`, `hypothesis`, `syrupy`, and the FastMCP
  `Client`; a `bloommcp/tests/` layout that collects and runs with fakes (no live
  Supabase), plus a new CI job that runs it (today nothing runs `cd bloommcp && pytest`).
- **Commit the #120 turface_19 fixture + recorded golden values** under
  `bloommcp/tests/fixtures/`. Golden values are the **independent** #120 / PR #146
  recording (not re-derived from `sleap-roots-analyze`), so the oracle is a real
  cross-tier check, asserted with explicit numeric tolerances.
- **Sync docs** left stale by the move: `_WIKI/BLOOMMCP/*` (link paths + `from source.*`
  code examples), `openspec/project.md` (monorepo tree + External Packages), root
  `README.md` tree.

## Impact

- **New capability:** `bloommcp-packaging`.
- **Affected code:** `bloommcp/{source,tools,storage}/` → `bloommcp/src/bloom_mcp/`
  (move + 34 import rewrites); `bloommcp/source/supabase_client.py` (lazy validation);
  `bloommcp/server.py` (explicit boot validation); `bloommcp/pyproject.toml` +
  `bloommcp/uv.lock` (build-system, deps, virtual→package); `bloommcp/Dockerfile` +
  `docker-compose.dev.yml` + `docker-compose.prod.yml` (bloommcp service entry point /
  mounts); 13 root `tests/` import sites + the import-raise tests; root `pyproject.toml`
  `test` extra; new `bloommcp/tests/` + `bloommcp/tests/fixtures/`;
  `.github/workflows/pr-checks.yml` (new bloommcp test job); `scripts/check-uv-locks.py`
  already lists `bloommcp` (lock gate still applies).
- **Affected docs:** `_WIKI/BLOOMMCP/{README,storage-workflow,writing-a-new-tool}.md`,
  `openspec/project.md`, `README.md`, `.claude/commands/pre-merge.md` (smoke imports).
- **No runtime behavior change** for the deployed server: tool surface and `/health` are
  unchanged; fail-fast on missing env is preserved (moved from import-time to an explicit
  boot call).
- **Out of repo:** ticking the roadmap Tier 0 row is a GitHub action, not trackable here.
- **Branch/PR target:** `staging` (staging-first repo); branch off `origin/staging`.

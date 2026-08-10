Commit plan (see design.md/review discussion): commit each numbered task group as ONE commit
(not sub-step-by-sub-step) — within a group, the "test first" sub-step is designed to be red
against pre-implementation code, so splitting a group across commits leaves an intermediate
commit with intentionally-failing tests. Suggested commit messages use this repo's
`type(#642): description` convention.

## 1. `storage_backend.py`: self-serve base URL + local output root accessor

- [x] 1.1 **Test first** (`bloommcp/tests/test_storage_backend.py`): add
      `test_self_serve_base_url_defaults_to_localhost_8811` (no `BLOOMMCP_PUBLIC_URL` ->
      `http://localhost:8811`) and `test_self_serve_base_url_prefers_public_url` (with
      `BLOOMMCP_PUBLIC_URL=https://example.internal/` -> `https://example.internal`, trailing
      slash stripped). Run `uv run pytest bloommcp/tests/test_storage_backend.py -k self_serve`
      and confirm both fail (`self_serve_base_url` does not exist yet).
- [x] 1.2 Implement `self_serve_base_url() -> str` in `storage_backend.py`:
      `(os.environ.get("BLOOMMCP_PUBLIC_URL") or "http://localhost:8811").rstrip("/")`. Confirm
      1.1's tests pass.
- [x] 1.3 **Test first**: add `test_local_output_root_matches_resolve_local_root` asserting
      `sb.local_output_root()` equals `sb._resolve_local_root()` for a representative env
      (`BLOOM_STORAGE_LOCAL_ROOT` set). Confirm it fails (no public wrapper yet). Note: since
      `local_output_root()` is a one-line delegate, this test is a thin regression guard, not a
      behavioral one — that's fine, keep it, but don't expect it to catch future bugs in
      `_resolve_local_root()` itself (that function's own tests already cover its logic).
- [x] 1.4 Implement `local_output_root() -> Path` as a thin public wrapper over the existing
      `_resolve_local_root()`. Confirm 1.3 passes.
- [x] Commit: `feat(#642): default BLOOM_STORAGE_URL to bloommcp's own address` — landed together
      with task group 2 in one commit (`storage_backend.py`, `test_storage_backend.py`); both were
      implemented in the same edit pass since `create_signed_url` immediately consumes
      `self_serve_base_url()`. `uv run pytest tests/` — green (109 passed).

## 2. `storage_backend.py`: `create_signed_url` defaults instead of raising

- [x] 2.1 **Test first**: replaced `test_local_create_signed_url_raises_when_unset_no_path_leak`
      (its asserted behavior is being removed) with
      `test_local_create_signed_url_defaults_to_self_serve_base` and
      `test_local_create_signed_url_default_honors_public_url`. Confirmed both failed against the
      raising implementation before 2.2.
- [x] 2.2 Implemented: `base = os.environ.get("BLOOM_STORAGE_URL") or f"{self_serve_base_url()}/output"`.
      All of `test_storage_backend.py` passes (109 passed), including the untouched
      `test_local_create_signed_url_returns_served_url` / `..._strips_trailing_slash` /
      `..._ignores_expires_in`.
- [x] Commit: landed with task group 1 (see above) — `efb7443`.

## 3. `experiment_utils.py`: `BLOOM_PLOTS_URL` self-serve default under the `BLOOM_LOCAL_ROOT` tier

- [x] 3.1 / 3.1a / 3.1b **Test first**: added `test_plots_url_resolves_under_local_root`,
      `test_plots_url_explicit_override_wins_over_local_root`,
      `test_plots_url_ignores_local_root_on_default_backend`, and
      `test_plots_url_ignores_explicit_plots_dir_without_local_root` (the granular
      explicit-override-tier case) to `test_local_mode.py`. Confirmed all four failed
      (`AttributeError: no attribute '_resolve_plots_url'`) before 3.2.
- [x] 3.2 Implemented `_resolve_plots_url()` next to `_resolve_plots_dir()`, same structure
      (explicit wins, else `_fully_local_root()` gate + `self_serve_base_url() + "/plots"`, else
      `""`). `PLOTS_URL = _resolve_plots_url()`.
- [x] 3.3 **Test first**: added
      `test_is_local_backend_not_consulted_when_local_root_unset_for_plots_url`, mirroring the
      existing `PLOTS_DIR`-side test exactly (direct unit test with a call-counting monkeypatch,
      not an extension of the subprocess-based import-purity test).
- [x] Commit: `feat(#642): default BLOOM_PLOTS_URL under the BLOOM_LOCAL_ROOT tier` — `b8da2c8`.
      `uv run pytest tests/test_local_mode.py` — green (39 passed).

## 4. `experiment_utils.py`: `BLOOM_PLOTS_URL` joins the optional-under-`BLOOM_LOCAL_ROOT` set

- [x] 4.1 **Test first**: updated the shared `_local_root_env` fixture to `delenv` `BLOOM_PLOTS_URL`
      by default and `monkeypatch.setattr(eu, "PLOTS_URL", eu._resolve_plots_url())`. Confirmed
      exactly 9 tests went red with `Missing required environment variables: BLOOM_PLOTS_URL`
      (matching the predicted list) before 4.2 landed.
- [x] 3.5 **Test first**: added `test_fully_local_boot_succeeds_with_only_backend_and_local_root_set`
      — drives the real `main()` entry point through the literal 2-variable
      (`BLOOM_STORAGE_BACKEND` + `BLOOM_LOCAL_ROOT`) quick-start.
- [x] 4.2 Implemented: `optional_when_local` now includes `"BLOOM_PLOTS_URL"`. Docstring updated
      to say "four" variables.
- [x] 4.3 Confirmed `test_fully_local_still_fails_fast_on_missing_data_dir` (granular-tier
      `_local_dirs` helper) still passes unmodified.
- [x] Commit: `fix(#642): make BLOOM_PLOTS_URL optional under BLOOM_LOCAL_ROOT` — `4eeab05`.
      `uv run pytest tests/test_local_mode.py` — green (40 passed, 0 failed).

## 5. `server.py`: mount `/output` and `/plots` `StaticFiles` in local mode

- [x] 5.1 / 5.1b **Test first**: created `bloommcp/tests/test_local_static_mounts.py` with the
      absent-on-default-backend tests, the granular-tier serving tests (with the `PLOTS_DIR`
      frozen-constant `monkeypatch.setattr` called out explicitly), and the
      `BLOOM_LOCAL_ROOT`-tier serving tests. Confirmed the 4 serving tests failed with `404`
      before 5.2 (the 2 absent-mount tests trivially passed either way, as expected).
- [x] 5.2 Implemented in `server.py`: added `StaticFiles` import; `build_app()` now appends the
      `/output` and `/plots` `Mount`s (gated on `is_local_backend()`) before the catch-all
      `Mount("/", ...)`.
- [x] 5.4 **Test first**: extended `test_action_from_path`'s parametrize list with
      `/output`/`/plots` -> `"combined"` in `test_identity_middleware.py`; added
      `test_garbage_identity_header_rejected_on_local_mounts` and (bonus, beyond the reviewed
      plan) `test_missing_file_returns_404_not_500` to `test_local_static_mounts.py`.
- [x] 5.3 Ran the full suite: `1137 passed` (after also removing two pre-existing, untracked,
      gitignored `__pycache__`-only leftover directories — `bloommcp/src/bloom_mcp/tools/workflows`
      and `bloommcp/src/bloom_mcp/storage` — from an earlier local refactor that were making two
      unrelated regression-guard tests fail for reasons that had nothing to do with this change;
      confirmed those 2 failures reproduced identically before this change too).
- [x] Commit: `feat(#642): mount /output and /plots StaticFiles in local mode` — `7cb6ff6`.

## 6. Docs

- [x] 6.1 Updated `bloommcp/docs/storage-backends.md` ("Downloading outputs", the "one difference"
      bullet, "Backend-aware boot", and a new sentence after the "Two ways to use it" snippets,
      scoped accurately per review — the docker-compose snippet's plot URL already resolved via
      langchain-agent's own mount before this change). Updated `bloom_mcp/auth.py`'s
      `BLOOMMCP_PUBLIC_URL` comment and `docker-compose.dev.yml`'s matching `BLOOMMCP_PUBLIC_URL`
      and `BLOOM_STORAGE_URL` comments. **Deviation from plan:** skipped `docker-compose.prod.yml`
      — prod/staging never set `BLOOM_STORAGE_BACKEND=local` (confirmed via `.env.prod.defaults`
      / `.env.staging.defaults`), so a self-serve cross-reference there would describe a feature
      that structurally cannot activate in that file's context.
- [x] 6.2 `openspec validate update-bloommcp-local-url-defaults --strict` — valid.
- [x] Commit: `docs(#642): correct storage-backends.md and compose comments for self-served local
      URLs` — `6ddb4ed`.

## 7. Full verification

- [x] 7.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke" -v --tb=short` — green (1137 passed, 33 deselected).
- [x] 7.2 `bloommcp`'s Black/Ruff/Ruff-format aren't `uv run`-able directly (not project
      dependencies — pre-commit manages its own tool envs); ran the actual configured gate via
      `uvx pre-commit run --files <changed files>` instead. First pass auto-fixed 2 files (Black
      line-wrapping) + 4 markdown files (Prettier); committed those fixes
      (`style(#642): black/prettier auto-fixes from pre-commit`, `479a7db`); second pass — all
      hooks pass.
- [x] 7.3 Manual smoke: `BLOOM_STORAGE_BACKEND=local BLOOM_LOCAL_ROOT=/tmp/bloommcp-smoke uv run
      bloom-mcp` (with `BLOOM_LOCAL_ROOT` pre-created, matching the documented "only the top-level
      folder must pre-exist" contract) passed boot validation cleanly with no missing-variable
      error — confirming the 2-var quick start works. Binding port 8811 itself failed only because
      another bloommcp instance was already running on this dev machine (unrelated pre-existing
      process, confirmed via `lsof`), not a defect in this change; the HTTP-serving half of this
      smoke test is otherwise already covered by task 5's automated `TestClient` tests.

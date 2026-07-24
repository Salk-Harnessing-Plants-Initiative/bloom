## 1. Input root precedence (experiment_utils.py)

- [x] 1.1a Write failing tests: `resolve_experiment_local_root()`'s 3-tier precedence
      (`BLOOM_EXPERIMENT_LOCAL_ROOT` explicit > `<BLOOM_LOCAL_ROOT>/input` > `BLOOM_TRAITS_DIR`
      fallback), each gated on `is_local_backend()`.
- [x] 1.1b Implement: extend `resolve_experiment_local_root()` with the middle tier.

## 2. Output root precedence (storage_backend.py)

- [x] 2.1a Write failing tests: `_resolve_local_root()`'s 3-tier precedence
      (`BLOOM_STORAGE_LOCAL_ROOT` explicit > `<BLOOM_LOCAL_ROOT>/output` > `BLOOM_OUTPUT_DIR`
      bridge fallback).
- [x] 2.1b Implement: extend `_resolve_local_root()` with the middle tier.

## 3. Plots default (experiment_utils.py)

- [x] 3.1a Write failing tests: `PLOTS_DIR`'s `BLOOM_LOCAL_ROOT`-aware default (only when
      `is_local_backend()`, itself only consulted when `BLOOM_LOCAL_ROOT` is set) — include a
      spy-based unit test (monkeypatch `storage_backend.is_local_backend` with a
      raise-if-called stub) proving it is **not called at all** when `BLOOM_LOCAL_ROOT` is
      unset, not just that import "succeeds."
- [x] 3.1b Implement: make the `PLOTS_DIR` module constant `BLOOM_LOCAL_ROOT`-aware.

## 4. BLOOM_LOCAL_ROOT top-level validation + auto-create

- [x] 4.1a Write failing tests for the top-level check: `BLOOM_LOCAL_ROOT` missing → fails
      fast; `BLOOM_LOCAL_ROOT` exists as a **file, not a directory** → fails fast with a
      distinct message; `BLOOM_LOCAL_ROOT` exists as a directory but is **not writable**
      (POSIX-only — guard with the same `try`/`except`/`pytest.skip` pattern
      `test_escape_guard_rejects_symlink` already uses for chmod-based assumptions) → fails
      fast. Note this top-level check RAISES on not-writable, deliberately stricter than the
      legacy per-dir check's warn-only behavior for `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR`/
      `BLOOM_PLOTS_DIR` (see design.md Decision 6).
- [x] 4.1b Implement: the new top-level `BLOOM_LOCAL_ROOT` validation.
- [x] 4.2a Write failing tests: `validate_env()` succeeds when `BLOOM_STORAGE_BACKEND=local`,
      `BLOOM_LOCAL_ROOT` is set to an existing writable dir, and `BLOOM_TRAITS_DIR` /
      `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` are **unset** (not merely pointed at a bad path —
      this must prove the "missing required env var" check itself is skipped, distinct from
      the auto-create tests in 4.3).
- [x] 4.2b Implement: drop the three vars from `validate_env()`'s required-vars check in that
      specific combination.
- [x] 4.3a Write failing tests: the `input/`, `output/`, `plots/` subfolders are created when
      missing under `BLOOM_LOCAL_ROOT`; and a subfolder path that already exists as a
      **non-directory file** (e.g. `<BLOOM_LOCAL_ROOT>/input` is a regular file) raises a
      clear, caller-safe error rather than an uncaught `FileExistsError` from `mkdir`.
- [x] 4.3b Implement: `validate_experiment_local_root()`, `validate_storage_backend()`, and
      `_validate_dirs()` each `mkdir(parents=True, exist_ok=True)` their own
      `BLOOM_LOCAL_ROOT`-derived subfolder, catching/re-raising `FileExistsError` clearly.
- [x] 4.4 Write regression tests: each of the three explicit vars
      (`BLOOM_EXPERIMENT_LOCAL_ROOT`, `BLOOM_STORAGE_LOCAL_ROOT`, `BLOOM_PLOTS_DIR`),
      **independently** (not "or" — all three), set to a path that does not exist while
      `BLOOM_LOCAL_ROOT` is also set to a valid dir → each still fails fast, no auto-create.

## 5. Default-path + import-purity regression coverage

- [x] 5.1 Write test: `BLOOM_STORAGE_BACKEND` unset/`supabase` with `BLOOM_LOCAL_ROOT` set
      anyway → behavior byte-for-byte unchanged (the three legacy vars remain required, all
      three resolvers ignore `BLOOM_LOCAL_ROOT`, `PLOTS_DIR` resolves exactly as today).
- [x] 5.2 Extend the subprocess import-purity tests
      (`test_ports_import_is_pure_without_supabase_env`,
      `test_server_import_is_pure_including_experiment_local_root`): import still succeeds
      with `BLOOM_LOCAL_ROOT` unset (unchanged baseline); add a companion proving import still
      succeeds when `BLOOM_LOCAL_ROOT` **is** set together with an invalid
      `BLOOM_STORAGE_BACKEND` value (mirrors `test_server_import_is_pure_with_invalid_backend`,
      extended for the new opt-in read path — `is_local_backend()` never raises, so this must
      hold).
- [x] 5.3 Confirm the pre-existing `test_fully_local_qc_clean_to_pca_no_supabase`
      (`test_local_mode.py`) is unmodified and stays green — a named regression checkpoint,
      not an assumed side effect.
- [x] 5.4 Extend the fully-local end-to-end test to a variant driven by **only**
      `BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT` (no `BLOOM_TRAITS_DIR` /
      `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` / `BLOOM_EXPERIMENT_LOCAL_ROOT` /
      `BLOOM_STORAGE_LOCAL_ROOT`), asserting the `qc_clean → pca_analysis` round-trip
      succeeds and plots land under `<BLOOM_LOCAL_ROOT>/plots`.
- [x] 5.5 Write a mixed-precedence test: `BLOOM_EXPERIMENT_LOCAL_ROOT` explicitly set (input
      override) while `BLOOM_STORAGE_LOCAL_ROOT` and `BLOOM_PLOTS_DIR` are both unset with
      `BLOOM_LOCAL_ROOT` set → input honors the explicit override; output/plots still resolve
      to and auto-create under `BLOOM_LOCAL_ROOT`, in the same boot.

## 6. Docs + compose

- [x] 6.1 `bloommcp/docs/storage-backends.md`: replace the current prose-per-variable
      local-mode sections with a single 3-tier precedence table covering all three subpaths;
      **correct** (not just supplement) the "Backend-aware boot... the data directories
      (`BLOOM_*_DIR`, `BLOOM_PLOTS_URL`) ... still fail fast in both modes" sentence, which
      becomes false once this ships — state the `BLOOM_LOCAL_ROOT` carve-out explicitly; add
      two clearly-labeled examples (the `docker-compose.dev.yml` container-path form, and the
      bare-host-path form for a native Claude Desktop / Claude Code run), each with its own
      persistence caveat (see 6.3).
- [x] 6.2 `_WIKI/BLOOMMCP/README.md`: its "Storage backend (`local` opt-in)" section
      independently restates the fallback chain (and is already stale re: #390's
      `BLOOM_EXPERIMENT_LOCAL_ROOT`) — replace it with a one-line pointer to
      `storage-backends.md`'s precedence table instead of re-deriving the chain a third time.
- [x] 6.3 `docker-compose.dev.yml`: add `BLOOM_LOCAL_ROOT` to the existing commented
      local-mode block as a **literal path value** (e.g. `BLOOM_LOCAL_ROOT:
      /app/data/LOCAL_ROOT`), matching the existing `BLOOM_STORAGE_LOCAL_ROOT` /
      `BLOOM_EXPERIMENT_LOCAL_ROOT` style — **not** `${BLOOM_LOCAL_ROOT}` interpolation, which
      would trip `tests/unit/test_env_dev_example.py`'s completeness check and force an
      unneeded `.env.dev.example` entry. Also add a matching commented volume-mount line
      (`- ./bloommcp/data/LOCAL_ROOT:/app/data/LOCAL_ROOT`) — none of the three existing bind
      mounts cover the `<root>/{input,output,plots}` layout the code derives, so without this
      the auto-created subfolders would land only in the container's writable layer and be
      wiped on the next rebuild.

## 7. Verification

- [x] 7.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration"` —
      full suite green (560 passed, 4 deselected), including the unmodified
      `test_fully_local_qc_clean_to_pca_no_supabase` and all of `test_local_mode.py` /
      `test_storage_backend.py` (78 passed).
- [x] 7.2 `ruff check` (pinned v0.9.9, matching `.pre-commit-config.yaml`), `ruff format --check`,
      and `black --check` clean on all changed files; `uv lock --check` clean (no dependency
      change); `prettier --check` clean on the two changed docs files.
- [x] 7.3 `openspec validate add-bloommcp-local-root --strict` passes.

## 8. Cross-change coordination (process — no code commit)

- [ ] 8.1 Resolve the `bloommcp-packaging` spec collision with the still-unarchived
      `add-bloommcp-local-experiment-reader` (#390) **before either change archives** — #390
      carries its own independent `MODIFIED`/`ADDED` blocks for `Server Boot Fail-Fast
      Preserved` and `Backend-Aware Boot Gate` with materially different resulting text than
      this change's delta. See design.md Decision 7 for the resolution this proposal expects;
      do not let either change's archive silently overwrite the other's edits.
- [ ] 8.2 Once #390 archives, reconcile its `LocalReader Adapter` requirement (`
      bloommcp-experiment-read`) — whose normative text embeds a now-stale 2-tier resolution
      sentence — against this change's `Local Input Root Resolution` requirement, so the
      archived capability doesn't carry two overlapping, one-of-them-wrong descriptions of the
      same resolution mechanism.

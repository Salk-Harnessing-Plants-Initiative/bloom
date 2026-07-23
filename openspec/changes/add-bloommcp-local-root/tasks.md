## 1. Core resolution + precedence

- [ ] 1.1 `experiment_utils.py`: extend `resolve_experiment_local_root()` with the
      `BLOOM_LOCAL_ROOT` → `<root>/input` middle tier, gated on `is_local_backend()`.
- [ ] 1.2 `storage_backend.py`: extend `_resolve_local_root()` with the `BLOOM_LOCAL_ROOT` →
      `<root>/output` middle tier, gated on `is_local_backend()`.
- [ ] 1.3 `experiment_utils.py`: make the `PLOTS_DIR` module-level default `BLOOM_LOCAL_ROOT`-aware
      (only when `is_local_backend()`, itself only checked when `BLOOM_LOCAL_ROOT` is set, so no
      eager `BLOOM_STORAGE_BACKEND` read when the var is unset).

## 2. Boot validation + auto-create

- [ ] 2.1 New check: `BLOOM_LOCAL_ROOT` itself must exist and be a writable directory when
      `BLOOM_STORAGE_BACKEND=local` and it is set; one clear error naming `BLOOM_LOCAL_ROOT`.
- [ ] 2.2 `validate_env()`: `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` drop out of
      the required-vars check when local + `BLOOM_LOCAL_ROOT`; an explicitly-set one of the three
      keeps today's must-pre-exist contract regardless.
- [ ] 2.3 `validate_experiment_local_root()`: `mkdir(parents=True, exist_ok=True)` the
      `BLOOM_LOCAL_ROOT`-derived input subfolder instead of raising when missing.
- [ ] 2.4 `validate_storage_backend()`: same auto-mkdir treatment for the `BLOOM_LOCAL_ROOT`-derived
      output subfolder.
- [ ] 2.5 `_validate_dirs()`: same auto-mkdir treatment for the `BLOOM_LOCAL_ROOT`-derived plots
      subfolder.

## 3. Docs + compose

- [ ] 3.1 `bloommcp/docs/storage-backends.md`: document `BLOOM_LOCAL_ROOT`, the 3-tier precedence
      per subpath, the auto-create behavior, and the Claude Desktop end-state example.
- [ ] 3.2 `docker-compose.dev.yml`: add `BLOOM_LOCAL_ROOT` alongside the existing commented
      local-mode block (off by default).

## 4. Tests

- [ ] 4.1 Precedence tests per subpath (input / output / plots): explicit var wins even with
      `BLOOM_LOCAL_ROOT` set; `BLOOM_LOCAL_ROOT`-derived default when the explicit var is unset;
      existing fallback/fail-fast when neither is set.
- [ ] 4.2 Auto-create tests: `BLOOM_LOCAL_ROOT` set to an existing empty writable directory → boot
      creates `input/`, `output/`, `plots/` under it; a missing `BLOOM_LOCAL_ROOT` itself still
      fails fast with a clear error.
- [ ] 4.3 Default-path regression test: `BLOOM_STORAGE_BACKEND` unset/`supabase` with
      `BLOOM_LOCAL_ROOT` set anyway → behavior byte-for-byte unchanged (`BLOOM_LOCAL_ROOT` ignored,
      the three legacy vars remain required).
- [ ] 4.4 Explicit-override-still-strict test: `BLOOM_LOCAL_ROOT` set + `BLOOM_PLOTS_DIR` (or
      `BLOOM_EXPERIMENT_LOCAL_ROOT` / `BLOOM_STORAGE_LOCAL_ROOT`) explicitly set to a path that
      does not exist → still fails fast, no auto-create for the explicit override.
- [ ] 4.5 Extend the fully-local end-to-end test (`test_local_mode.py`) to run with **only**
      `BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT` set (no `BLOOM_TRAITS_DIR` /
      `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` / `BLOOM_EXPERIMENT_LOCAL_ROOT` /
      `BLOOM_STORAGE_LOCAL_ROOT`), asserting the `qc_clean → pca_analysis` round-trip still
      succeeds and plots land under `<BLOOM_LOCAL_ROOT>/plots`.
- [ ] 4.6 Import-purity: extend the subprocess import-purity tests to confirm import stays
      side-effect-free when `BLOOM_LOCAL_ROOT` is unset (unchanged), and add a companion asserting
      import still succeeds (no exception) when `BLOOM_LOCAL_ROOT` **is** set together with an
      invalid `BLOOM_STORAGE_BACKEND` value.

## 5. Archive ordering

- [ ] 5.1 Confirm `add-bloommcp-local-experiment-reader` (#390) archives at or before this change,
      per design.md's Migration Plan note.

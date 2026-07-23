## Why

[#479](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/479) — fully-local
mode (`BLOOM_STORAGE_BACKEND=local`, the Claude-Desktop-offline path from #386/PR #389 and
#390/PR #405) requires wiring **three independently-resolved directories**, each with its own
env var and its own fallback chain:

- Input root: `BLOOM_EXPERIMENT_LOCAL_ROOT` → falls back to `BLOOM_TRAITS_DIR`
  ([experiment_utils.py:36-50](../../../bloommcp/src/bloom_mcp/experiment_utils.py#L36-L50),
  `resolve_experiment_local_root`).
- Storage/output root: `BLOOM_STORAGE_LOCAL_ROOT` → falls back to `BLOOM_OUTPUT_DIR`, explicitly
  flagged in-code as a **"bridge-only, deprecated"** default
  ([storage_backend.py:331-351](../../../bloommcp/src/bloom_mcp/storage_backend.py#L331-L351),
  `_resolve_local_root`).
- Plots: `BLOOM_PLOTS_DIR` — always independently required in **every** mode via
  `_REQUIRED_DIRS` / `validate_env()`
  ([experiment_utils.py:24-32,100-113](../../../bloommcp/src/bloom_mcp/experiment_utils.py#L24-L32)),
  and used unconditionally by every plot tool through `_viz_shared.save_plot()`
  ([\_viz_shared.py:24-30](../../../bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/_viz_shared.py#L24-L30)).

This compounds worse than three separate variables: `validate_env()`'s `_REQUIRED_DIRS` check
unconditionally requires `BLOOM_TRAITS_DIR`, `BLOOM_OUTPUT_DIR`, **and** `BLOOM_PLOTS_DIR` to
each be individually set and pre-existing, run by `server.main()`'s `validate_data_env()` call
**before** the fully-local/Supabase branch. So even a user who correctly sets
`BLOOM_EXPERIMENT_LOCAL_ROOT` + `BLOOM_STORAGE_LOCAL_ROOT` still has to separately create and
wire the three original, confusingly-named directories (`SLEAP_OUT_CSV`, `ANALYSIS_OUTPUT`,
`PLOTS_DIR` in the dev compose mounts) before boot succeeds — the exact pain point the issue
describes for a Claude Desktop / offline user.

## What Changes

- **Add a single `BLOOM_LOCAL_ROOT` env var.** Effective only when `BLOOM_STORAGE_BACKEND=local`
  (inert on the default Supabase path, even if left set in the environment):
  - `resolve_experiment_local_root()` gains a middle tier: `BLOOM_EXPERIMENT_LOCAL_ROOT` (explicit,
    unchanged top priority) → `<BLOOM_LOCAL_ROOT>/input` (new) → `BLOOM_TRAITS_DIR` (existing
    fallback, unchanged when `BLOOM_LOCAL_ROOT` is also unset).
  - `_resolve_local_root()` gains the symmetric middle tier: `BLOOM_STORAGE_LOCAL_ROOT` (explicit) →
    `<BLOOM_LOCAL_ROOT>/output` (new) → `BLOOM_OUTPUT_DIR` (existing deprecated bridge, unchanged).
  - `BLOOM_PLOTS_DIR`'s default becomes `<BLOOM_LOCAL_ROOT>/plots` when unset **and**
    `BLOOM_STORAGE_BACKEND=local` **and** `BLOOM_LOCAL_ROOT` is set. The default (Supabase-backed)
    path's `BLOOM_PLOTS_DIR` requirement is **unchanged** — this must not touch dev/staging/prod's
    boot behavior, which never sets `BLOOM_LOCAL_ROOT`.
- **`BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` become conditionally optional.**
  `validate_env()`'s `_REQUIRED_DIRS` check SHALL stop requiring these three individually **only**
  when `BLOOM_STORAGE_BACKEND=local` and `BLOOM_LOCAL_ROOT` is set — in every other combination
  (unset backend, `supabase`, or `local` without `BLOOM_LOCAL_ROOT`) they stay exactly as required
  as today.
- **Only the top-level `BLOOM_LOCAL_ROOT` folder must pre-exist.** Boot-time validation SHALL fail
  fast if `BLOOM_LOCAL_ROOT` itself does not exist or is not a writable directory (one clear error).
  The three derived subfolders (`input/`, `output/`, `plots/`) SHALL auto-create
  (`mkdir(parents=True, exist_ok=True)`) at boot the same way `PLOTS_DIR` already does today at
  first plot-save — extended so nothing else needs pre-creating. An **explicitly-set** granular
  var (`BLOOM_EXPERIMENT_LOCAL_ROOT`, `BLOOM_STORAGE_LOCAL_ROOT`, or `BLOOM_PLOTS_DIR`) keeps
  today's stricter "must already exist" contract unchanged — auto-create applies only to the
  `BLOOM_LOCAL_ROOT`-derived default, so a typo'd explicit override still fails loudly.
- **Docs.** Update `bloommcp/docs/storage-backends.md` to document `BLOOM_LOCAL_ROOT`, its
  3-tier precedence per subpath, and the auto-create behavior; state the Claude Desktop end
  state (`BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT=/Users/you/bloommcp-data`, drop CSVs
  in `input/`, everything else appears under the same one folder).
- **`docker-compose.dev.yml`.** Add `BLOOM_LOCAL_ROOT` alongside the existing commented local-mode
  block (off by default) so it can be a single var in that block instead of the current
  `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT`/`PLOTS_DIR` trio (#478 will decide whether that block itself
  moves to `${VAR}` interpolation; this change is compatible either way).
- **Tests.** Precedence tests per subpath (explicit > `BLOOM_LOCAL_ROOT`-derived > existing
  fallback/fail-fast), auto-create tests, a default-path regression test (`BLOOM_LOCAL_ROOT` set
  but backend unset/`supabase` → byte-for-byte unchanged), an explicit-override-still-strict test,
  an end-to-end fully-local run driven by `BLOOM_STORAGE_BACKEND=local` + `BLOOM_LOCAL_ROOT` alone,
  and an import-purity extension proving the new opt-in env read cannot crash import even with an
  invalid `BLOOM_STORAGE_BACKEND` value.

## Impact

- **Affected specs:**
  - `bloommcp-packaging` — MODIFY `Lazy Environment Validation` (the three directory vars become
    conditionally optional under `BLOOM_LOCAL_ROOT`) and `Server Boot Fail-Fast Preserved` (the
    "any `BLOOM_*_DIR` unset always fails fast" scenario gets the same carve-out).
  - `bloommcp-storage-backend` — MODIFY `Local Root Resolution` (new `BLOOM_LOCAL_ROOT` tier +
    auto-create) and `Backend Selection via BLOOM_STORAGE_BACKEND` (the side-effect-free-import
    guarantee gets a narrow, opt-in exception, gated behind `BLOOM_LOCAL_ROOT` being set).
  - `bloommcp-experiment-read` — ADD `Local Input Root Resolution` (this capability's archived
    spec predates #390/`LocalReader` entirely — see design.md Migration Plan for the archive-
    ordering note).
- **Affected code:**
  - `bloommcp/src/bloom_mcp/experiment_utils.py` — `resolve_experiment_local_root()`,
    `validate_experiment_local_root()`, `_REQUIRED_DIRS`/`validate_env()`/`_validate_dirs()`, the
    `PLOTS_DIR` module-level default.
  - `bloommcp/src/bloom_mcp/storage_backend.py` — `_resolve_local_root()`,
    `validate_storage_backend()`.
  - `docker-compose.dev.yml`, `bloommcp/docs/storage-backends.md`.
  - Tests under `bloommcp/tests/test_local_mode.py`, `bloommcp/tests/test_storage_backend.py`.

## Scope / Non-Goals

- **The default Supabase-backed path (dev/staging/prod) is unaffected** — `BLOOM_LOCAL_ROOT` is
  inert unless `BLOOM_STORAGE_BACKEND=local`, and prod/staging never set either.
- **`BLOOM_PLOTS_URL` is unaffected** — it is a URL string, not a directory, and stays
  unconditionally required in every mode.
- **Does not touch `docker-compose.prod.yml`** — `BLOOM_LOCAL_ROOT` is a local/dev-only opt-in.
- **Does not change the granular vars' own precedence** — `BLOOM_EXPERIMENT_LOCAL_ROOT` /
  `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_PLOTS_DIR` still win outright when set; this only inserts a
  new middle tier beneath them, for users who don't want the split.
- **#478** (whether `docker-compose.dev.yml`'s local-mode toggle itself moves to `${VAR}`
  interpolation) and **#477** (confirming `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` are dead weight in
  staging/prod specifically) are separate, compatible changes — not addressed here.

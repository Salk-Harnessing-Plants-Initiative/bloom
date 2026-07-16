> **Implementation status — DONE (2026-07-08, PR #414, commit `c16506d`).** All boxes below are
> ticked. Deviations from the original plan, and how they resolved:
> - **§3.3 / §3.3b readers:** rather than rewiring each adapter, `detect_columns` became a thin shim
>   over `resolve_columns`, so *every* reader consumer (Fake/Supabase, and any future adapter) gets
>   the new detection for free. `LocalReader` (#390) was not on this branch's base, so §3.3b is N/A
>   here — the shim covers it when #390 lands.
> - **§2.5 (override→nonexistent):** `resolve_columns` stays *pure* (never raises); the
>   nonexistent-override rejection is enforced in `qc_clean` (`invalid_input`), tested in §4.3.
> - **§5.2 (#400 golden):** #400 merged before implementation, so its fixtures ARE on the base — but
>   the `remove_outliers` golden is **verified UNAFFECTED** (its tests inject a fixed cleaned frame
>   disjoint from the detection path, so `Computation.Time.s` never entered them). No re-record
>   needed; its suite passes untouched. (Review finding #4 — the #403 acceptance item is moot, not undone.)
> - **§0 (#338 reconcile):** #338 is still in-flight (unarchived), so the supersede is landed in
>   code + tests here and the `MODIFIED` delta / lockstep edit is **deferred to #338's archive**.
> - **Behavior-change heads-up (review findings #1/#2), called out in the PR body:** required roles
>   use **exact-match** name detection (`geno`/`genotype`/`accession`/`species_name`;
>   `barcode`/`plant_qr_code`/`scan_id`/`plant_id`/`plant_name`), so a header like `Genotype_ID` or
>   `sample_barcode` now needs an override; and because `detect_columns` is a shared shim, the
>   upstream `get_trait_columns` detection now applies to *every* reader consumer (pca `scores.csv`,
>   clustering `labels.csv`, etc.), not just `qc_clean`. Both are intended.

> **TDD note:** the RED steps below are a *local* working-tree rhythm — confirm each fails, then
> make it pass. Do **not** push a RED-only commit: this repo's CI (`pr-checks.yml`,
> `cd bloommcp && uv run --frozen --extra test pytest tests/`) gates the PR head, and a committed
> failing/uncollectable test (importing `resolve_columns` before it exists) is red. Commit
> RED+GREEN together.
>
> **Two counts, never conflate them** (verified against the real delegate on `turface_19_raw_data.csv`):
> `get_trait_columns` **detects 19** traits (was 20 via `detect_columns`; drops `Computation.Time.s`).
> `clean_traits_for_analysis` then drops the two NaN-heavy traits, so the **cleaned** set is **17**
> (was 18). So `n_traits_in == 19`, `n_traits_out == 17`. "20 → 19" is *detected*; "18 → 17" is *cleaned*.
>
> **CI-green-per-commit — the golden re-record rides the detection commit.** Changing trait
> detection breaks the *pre-existing* frozen-golden assertions in `tests/tools/test_qc_clean_tool.py`.
> Re-recording the golden JSON alone does **not** fix the non-golden `n_traits_in == len(raw_traits)`
> assertion. So the golden re-record **and** the test-body literal edits land in the **same commit**
> that changes detection. (In the shipped PR this is squashed into one `feat` commit that is green.)
>
> **Commit plan (as shipped)** (no dependency commit — `validate_entry_input` + `get_trait_columns`
> ship in the pinned `0.1.0a4`; `sleap-roots-contracts 0.1.0a1` is already a dep):
> 1. `docs(#403): openspec proposal — validate + require traceable qc_clean inputs` (`60feb0a`). 🟢
> 2. `feat(#403): validate + require traceable inputs at qc_clean; delegate trait detection to
>    get_trait_columns` (`c16506d`) — `columns.py` + shim + reader behavior, `qc_clean`
>    guards/validation/params/manifest, tests, golden re-record. 🟢

## 0. Reconcile with the in-flight `bloommcp-qc-clean-tool` (#338)

- [x] 0.1 This change **supersedes** two aspects of #338's still-unarchived spec: its scenario
      *"An undetected role column falls back to the delegate default"* (required roles now hard-error)
      and its `turface_19` oracle (`187×20 → n_traits_out == 18` becomes `19` detected / `17` cleaned).
      **Resolved:** #338 is still in-flight (no deployed `openspec/specs/bloommcp-qc-clean-tool/spec.md`),
      so the supersede is landed in code + tests here; the `MODIFIED` delta (or lockstep edit to #338's
      spec + golden) is **deferred to #338's archive**, tracked in the PR. No contradiction merges
      because #338's spec isn't deployed yet.

## 1. Pre-work — pin the delegates and record the baseline

- [x] 1.1 Confirm the delegates import on the pinned lock: `validate_entry_input` + `get_trait_columns`
      resolve against `0.1.0a4` / contracts `0.1.0a1` (no pin change). ✓
- [x] 1.2 Recorded, from the real delegate on `turface_19_raw_data.csv`: (a) `get_trait_columns`
      **detects 19** traits, `Computation.Time.s` excluded; (b) the **cleaned** count is **17** at the
      golden's params; (c) the **`ColumnRoles` Protocol** wants `.genotype` / `.barcode` / `.replicate`
      (not constructible, not top-level exported), and bloommcp's `sample_id` maps onto **`.barcode`**;
      (d) `warn`-mode **raises `ValueError`** on a structural failure (verified); (e) the contracts-absent
      seam is `sleap_roots_analyze.validation.input_contract.CONTRACTS_AVAILABLE`.
- [x] 1.3 Recorded the exact `BloomMCPError` remedy strings the tool emits (missing sample_id / missing
      genotype / both / nonexistent override), asserted by the error-envelope tests.

## 2. `resolve_columns` unit (RED→GREEN)

- [x] 2.1 `tests/data_access/test_columns.py`: `resolve_columns` returns the auto-detected roles
      (`geno`, `Barcode`, `rep`) for the turface_19 raw frame.
- [x] 2.2 Trait detection **excludes numeric metadata**: `Computation.Time.s` ∉ `trait_cols` and ∈
      `excluded_cols`, `len(trait_cols) == 19`.
- [x] 2.3 Overrides: `sample_id_column`/`genotype_column` force the named roles; `exclude_columns`
      removes a column from `trait_cols`.
- [x] 2.4 The pattern lists live in `data_access/columns.py` (moved from `experiment_utils`).
- [x] 2.5 An override naming a nonexistent column is rejected as `invalid_input` — **enforced in
      `qc_clean`** (`resolve_columns` stays pure), tested in §4.3.
- [x] 2.6 Degenerate frames: empty / all-metadata / only-`Computation.Time.s` frames return empty
      `trait_cols` without raising.
- [x] 2.7 The `_Roles` object bloommcp passes satisfies the `ColumnRoles` Protocol (`sample_id` →
      `.barcode`) — exercised by the real contract path in the §4 validation tests (turface passes
      warn mode via `run_input_validation`).

## 3. Implement `resolve_columns`, the reader shim, re-record the golden

- [x] 3.1 `bloommcp/src/bloom_mcp/data_access/columns.py`: `ResolvedColumns` + `resolve_columns(...)`;
      pattern lists moved here; trait detection delegates to `get_trait_columns`.
- [x] 3.2 `experiment_utils.detect_columns` is a **thin shim** over `resolve_columns` (lazy import
      breaks the cycle), so both readers declare roles through it; `load_experiment`'s signature is
      unchanged; `list_experiments` and the other call sites are unaffected.
- [x] 3.3 Reader consumers declare the 19-trait set via the shim (verified: the full suite is green and
      the pca scores-CSV test confirms `Computation.Time.s` is now metadata, not a trait).
- [x] 3.3b N/A — `LocalReader` (#390) is not on this branch's base; the shim covers it when #390 lands.
- [x] 3.4 `load_experiment`'s signature is unchanged (no override params on the read port; overrides
      are a `qc_clean` concern).
- [x] 3.5 Re-recorded `turface_19_qc_golden.json` (detected 20 → 19, cleaned 18 → 17; samples unchanged
      187/158 since `Computation.Time.s` is NaN-free); `_reproduced_by_...` bumped to `0.1.0a4`; the
      `_comment` documents the change; dual `== 17` / `== 187` assertions pin it.
- [x] 3.6 Updated the pre-existing hardcoded literals in `tests/tools/test_qc_clean_tool.py` and one
      `tests/tools/test_pca_analysis_tool.py` layout assertion to the 19/17 world.

## 4. Validate + require traceable inputs at `qc_clean` (RED→GREEN)

- [x] 4.0 Audited the existing `qc_clean` test fixtures; the two roleless ad-hoc frames were
      updated (one supersede-rewrite, one gains a `Barcode` column).
- [x] 4.1 Oracle: contract-valid `turface_19` cleans, persists, and reports `n_traits_in == 19` /
      `n_traits_out == 17`.
- [x] 4.2 Missing `sample_id` / missing `genotype` (no override) → `assumption_violated` listing
      columns + naming the override; no run persisted.
- [x] 4.3 `sample_id_column` override succeeds; `exclude_columns` drops a trait; explicit `trait_columns`
      **wins** over `exclude_columns` (precedence lives in `qc_clean`); nonexistent override →
      `invalid_input` naming it, no run.
- [x] 4.4 `warn`-mode findings land in the result `validation_warnings` **and** the manifest
      `input_validation` block (exact keys; `resolved_roles`; provenance-recorded `contract_version`);
      the run still commits. A warn-mode **structural** failure → `assumption_violated`, no run.
- [x] 4.5 Contracts absent → graceful (monkeypatch `CONTRACTS_AVAILABLE=False`): `validate_entry_input`
      no-ops, bloommcp's guard still refuses an untraceable frame.
- [x] 4.6 `qc_clean` routes column resolution through `resolve_columns` (the tool imports it directly;
      the spy/parity tests confirm the resolved roles are forwarded).
- [x] 4.7 Backward-compat transition: a previously-cleanable roleless frame now →
      `assumption_violated`, no run (the rewritten `test_missing_required_roles_error_with_no_run`).
- [x] 4.8 Both genotype **and** sample_id missing → one structured error naming **both** overrides.
- [x] 4.9 The `qc_clean` `inputSchema` exposes `sample_id_column` / `genotype_column` / `exclude_columns`.

## 5. Goldens

- [x] 5.1 The `qc_clean` golden re-record rode the `feat` commit (§3.5).
- [x] 5.2 **#400 `remove_outliers` golden — verified UNAFFECTED** (not decoupled after all: #400 merged
      before implementation, so its fixtures are on the base). Its tests inject a fixed cleaned frame
      disjoint from the detection path, so `Computation.Time.s` never entered them; the full
      `test_remove_outliers_tool.py` suite passes untouched — no re-record needed (review finding #4).

## 6. Docs + validation gate

- [x] 6.1 Updated the agent-facing surface: the `qc_clean` docstring + `QCCleanParams` field
      descriptions state the required-role contract and the overrides; added a #403 note to the
      `qc_clean` leg in `bloommcp/docs/local-validation.md`.
- [x] 6.2 Gate green: `uv run --frozen --extra test pytest tests/` (349 passed), `ruff check` +
      `ruff format --check` (pinned v0.9.9) clean, `uv lock --check` clean, clean-env import OK, and
      `openspec validate add-bloommcp-qc-input-validation --strict` passes. (Repo `ruff format` is
      authoritative; `black` 26.3.1 disagrees on pre-existing ternary constructs — not chased.)

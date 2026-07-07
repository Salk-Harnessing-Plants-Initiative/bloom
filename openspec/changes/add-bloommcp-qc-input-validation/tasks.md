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
> detection breaks the *pre-existing* frozen-golden assertions in `tests/tools/test_qc_clean_tool.py`
> (L65 `cleaned==18`, L84 `naive==158`, **L178 `n_traits_in==len(raw_traits)==20` — NOT golden-derived**,
> L481 full-pipeline parity). Re-recording the golden JSON alone does **not** fix L178. So the golden
> re-record **and** the test-body literal edits MUST land in the **same commit** that changes
> detection (commit 2), or that commit ships red.
>
> **Commit plan** (no dependency commit — `validate_entry_input` + `get_trait_columns` ship in the
> pinned `0.1.0a4`; `sleap-roots-contracts 0.1.0a1` is already a dep). Every commit ends with
> `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`:
> 1. `docs(#403): openspec proposal — validate + require traceable qc_clean inputs` — the
>    `openspec/changes/…` artifacts (run `openspec validate --strict` locally). 🟢
> 2. `feat(#403): add resolve_columns; delegate trait detection to get_trait_columns` — new
>    `data_access/columns.py` (+ `detect_columns` shim), reader wiring, `test_columns.py` + reader
>    tests, **the qc golden re-record (19/17) + `fixtures/README.md` counts + the L65/L84/L178/L481
>    test-literal fixes** (RED+GREEN atomic; golden rides here so CI stays green). 🟢
> 3. `feat(#403): validate + require traceable inputs at qc_clean` — the `qc_clean` validation call,
>    required-role guards, override params, result + manifest surfacing, docstring/field-description
>    updates, and all §4 tests (RED+GREEN atomic). 🟢
> Expect 1–2 reactive `fix(#403): address review` commits. Single PR → `staging`.
>
> **Decoupled (NOT in this change):** the `remove_outliers` golden also shifts under the 19/17 trait
> set, but its fixture lives on the #378/#400 branch and is **absent from this branch's base**. Its
> re-record is a **follow-up** once #400 merges — do **not** add a commit here that edits files that
> do not exist on the base. (See §5.)

## 0. Reconcile with the in-flight `bloommcp-qc-clean-tool` (#338)

- [ ] 0.1 This change **supersedes** two aspects of #338's still-unarchived spec: its scenario
      *"An undetected role column falls back to the delegate default"* (required roles now hard-error)
      and its `turface_19` oracle (`187×20 → n_traits_out == 18` becomes `19` detected / `17` cleaned).
      Determine #338's state at implementation time:
      - **If #338 has archived** (a deployed `openspec/specs/bloommcp-qc-clean-tool/spec.md` exists):
        add a `MODIFIED` delta file `specs/bloommcp-qc-clean-tool/spec.md` to *this* change correcting
        that scenario (required-role fall-through removed) **and** the 20/18 → 19/17 numbers, pasting
        the full requirement text per OpenSpec rules.
      - **If #338 is still in-flight**: coordinate with the #338 owner to edit that change's spec
        scenario + its `turface_19_qc_golden` numbers in lockstep, so the two changes cannot merge
        into a contradiction. Record the coordination outcome here.

## 1. Pre-work — pin the delegates and record the baseline

- [ ] 1.1 Confirm the delegates import on the pinned lock: `uv run --frozen python -c "from
      sleap_roots_analyze.validation import validate_entry_input; from sleap_roots_analyze import
      get_trait_columns; import sleap_roots_contracts"` resolves against `0.1.0a4` / contracts
      `0.1.0a1` (no pin change).
- [ ] 1.2 Record, from the real delegate on `turface_19_raw_data.csv`: (a) `get_trait_columns`
      **detects 19** traits, `Computation.Time.s` excluded (vs `detect_columns`' 20); (b) the
      **cleaned** count is **17** at the golden's params; (c) the **`ColumnRoles` shape** —
      `validate_entry_input(columns=…)` wants an object satisfying the `ColumnRoles` **Protocol**
      (`sleap_roots_analyze.validation.input_contract.ColumnRoles`, **not** constructible, **not**
      top-level exported) with attributes **`.genotype` / `.barcode` / `.replicate`**; bloommcp's
      resolved **`sample_id`** role maps onto **`.barcode`**; (d) `warn`-mode behaviour (does not fail
      a missing `sample_id`; **raises** on structural errors — missing genotype, no numeric trait,
      bad role dtype); (e) the monkeypatch seam for the contracts-absent path is the upstream flag
      `sleap_roots_analyze.validation.input_contract.CONTRACTS_AVAILABLE` (contracts is a hard dep, so
      a real uninstall is not reachable under `--frozen`).
- [ ] 1.3 Record the exact `BloomMCPError` remedy strings the tool emits for missing sample_id,
      missing genotype, both-missing, and an override naming a nonexistent column, so error-envelope
      tests assert the shipped copy.

## 2. RED — `resolve_columns` unit (write first)

- [ ] 2.1 Add `bloommcp/tests/data_access/test_columns.py`. Assert `resolve_columns` returns
      `ResolvedColumns(genotype, sample_id, replicate, trait_cols, excluded_cols)` for the turface_19
      raw frame with auto-detected roles (`geno`, `Barcode`, `rep`). Confirm RED.
- [ ] 2.2 Assert trait detection **excludes numeric metadata**: `Computation.Time.s` ∉ `trait_cols`
      and ∈ `excluded_cols`, and `len(trait_cols) == 19` (detected). Confirm RED.
- [ ] 2.3 Assert overrides: `sample_id_column`/`genotype_column` force the named roles;
      `exclude_columns=[<name>]` removes a column from `trait_cols`. Confirm RED.
- [ ] 2.4 Assert the pattern lists (`SAMPLE_ID_PATTERNS`/`GENOTYPE_PATTERNS`/`REPLICATE_PATTERNS`)
      now live in `data_access/columns.py` (moved from `experiment_utils`). Confirm RED.
- [ ] 2.5 Assert an override naming a **nonexistent** column (`sample_id_column="NoSuchCol"`) is a
      nameable error (names the column) — mirrors the existing `trait_columns` invalid-input test.
      Confirm RED.
- [ ] 2.6 Assert degenerate frames: empty frame and a zero-trait (all-metadata) frame return empty
      `trait_cols` without raising; a frame whose only numeric column is `Computation.Time.s` yields
      empty `trait_cols` with it in `excluded_cols`. Confirm RED.
- [ ] 2.7 Assert the object bloommcp constructs for `validate_entry_input(columns=…)` satisfies the
      `ColumnRoles` Protocol (`.genotype`/`.barcode`/`.replicate`) with the resolved `sample_id`
      mapped onto **`.barcode`**. Confirm RED.

## 3. GREEN — implement `resolve_columns`, wire the readers, re-record the golden

- [ ] 3.1 Add `bloommcp/src/bloom_mcp/data_access/columns.py`: `ResolvedColumns` dataclass +
      `resolve_columns(...)`. Move the pattern lists here; keep bloommcp role matching; delegate trait
      detection to `get_trait_columns` (pass resolved barcode/genotype/replicate names +
      `additional_exclude=exclude_columns`). Make §2 green.
- [ ] 3.2 Wire the reader adapters (`SupabaseReader` / `FakeReader`) to populate `ExperimentFrame`
      via `resolve_columns` with **no overrides**. `load_experiment`'s signature is **unchanged**.
      Make `experiment_utils.detect_columns` a **thin shim** over `resolve_columns` (no dtype
      heuristic of its own; retirement deferred) so `list_experiments` and the `:362`/`:380` call
      sites are unaffected.
- [ ] 3.3 Update reader adapter tests so `FakeReader` **and** `SupabaseReader` (monkeypatched
      boundary) declare the 19-trait set, asserting explicitly for **each** adapter that
      `Computation.Time.s` ∈ `metadata_cols` and ∉ `trait_cols` (the MODIFIED `bloommcp-experiment-read`
      "Numeric metadata is not declared as a trait role" scenario). Preserve the other reader
      scenarios (roles populated, version order, not-found).
- [ ] 3.3b If `LocalReader` (#390) is present on the base branch, route it through `resolve_columns`
      too and add the same 19-trait / `Computation.Time.s` ∈ metadata parity assertion; else record it
      as a follow-up so it can't silently violate the MODIFIED requirement.
- [ ] 3.4 RED-first: assert the read-port signature is **unchanged** — introspect
      `ExperimentReader.load_experiment` and both adapters' `load_experiment` and assert no
      `sample_id_column`/`genotype_column`/`exclude_columns` params were added.
- [ ] 3.5 **Re-record `turface_19_qc_golden.json`** (`raw/detected 20 → 19`, `cleaned 18 → 17`),
      recorded from the **upstream delegate output** (`get_trait_columns` + `clean_traits_for_analysis`
      per §1.2), **not** from bloommcp's `resolve_columns`. Fix the stale prose counts in
      `fixtures/README.md` (the "20 traits" / "18 traits" lines, not just an appended note), and bump
      `_reproduced_by_sleap_roots_analyze_version` `0.1.0a3 → 0.1.0a4`. Prettier-format the JSON.
- [ ] 3.6 Fix the pre-existing hardcoded literals in `tests/tools/test_qc_clean_tool.py` so they
      track the 19/17 world: L65 (`cleaned 18 → 17`), L84 (re-derive `naive_dropna_samples`), **L178
      (`raw_traits` computed test-side = 20 → exclude `Computation.Time.s` or assert the resolved set;
      NOT covered by the golden**), L481 full-pipeline parity + the `trait_cols` derivations at
      L82/L173/L436. Prefer reading counts from the golden JSON over inline literals. (Commits 3.1–3.6
      land together as commit 2 so CI stays green.)

## 4. RED → GREEN — validate + require traceable inputs at `qc_clean`

- [ ] 4.0 Audit every existing `qc_clean` test fixture (esp. the ad-hoc frames around
      `test_qc_clean_tool.py` L170–180 / L390–480): confirm each carries a resolvable sample_id +
      genotype or gets an override, so the new required-role guard doesn't turn a pre-existing test red.
- [ ] 4.1 Write the **oracle** first: a contract-valid `turface_19` (has `geno` + `Barcode`) cleans,
      persists a run, and the result reports `n_traits_in == 19` (detected) and `n_traits_out == 17`
      (cleaned; `Computation.Time.s` excluded). Confirm RED, then implement the `qc_clean` wiring GREEN.
- [ ] 4.2 RED: missing `sample_id` (no override) → `BloomMCPError(assumption_violated)` listing
      available columns + naming `sample_id_column`, **no run**. Same for missing `genotype`. GREEN:
      add the bloommcp-level required-role guards (resolved role not `None`) before persistence.
- [ ] 4.3 RED: `sample_id_column=<name>` override succeeds; `exclude_columns=[<name>]` removes a trait;
      explicit `trait_columns` **wins** over `exclude_columns`; a `sample_id_column`/`genotype_column`
      naming a nonexistent column → `invalid_input` naming it, no run. GREEN: add the params to
      `QCCleanParams` (with `Field(description=…)`) and pass them into `resolve_columns`. **Pin where
      the `trait_columns`-wins logic executes** (in `qc_clean` after resolution vs in `resolve_columns`)
      so the test reaches it.
- [ ] 4.4 RED: `warn`-mode findings (e.g. NaN in `replicate`) are recorded in the result
      `validation_warnings` **and** the manifest `input_validation` block, and the run **still commits**.
      Assert `QCCleanResult` carries `genotype_column`/`sample_id_column`/`replicate_column`/
      `excluded_columns`; assert the manifest block has exactly `{mode, contract_version, resolved_roles,
      excluded_columns, warnings}` with `resolved_roles == {"genotype","sample_id","replicate"}` and
      `contract_version` == the **provenance-recorded** value from `code_versions.py`. Also RED: a
      `warn`-mode **structural** hard-fail (genotype present, zero numeric traits) → `BloomMCPError`,
      no run. GREEN: call `validate_entry_input(df, columns=…, mode="warn", additional_exclude=…)`
      before persistence, catch its structural raise, collect warnings, write the manifest block + the
      new result fields.
- [ ] 4.5 RED: **contracts absent → graceful** — monkeypatch
      `sleap_roots_analyze.validation.input_contract.CONTRACTS_AVAILABLE = False`; `validate_entry_input`
      no-ops (no `ImportError`) and bloommcp's own guards still enforce traceability (missing sample_id
      still errors, no run). GREEN: confirm the degradation branch is exercised and the guards are
      independent of the contract.
- [ ] 4.6 RED: `qc_clean` routes column resolution through `resolve_columns` (spy/assert), **not**
      `experiment_utils.detect_columns`.
- [ ] 4.7 RED (backward-compat transition): a frame that `detect_columns` would previously have cleaned
      (traits + genotype, **no** sample identifier) now returns `BloomMCPError(assumption_violated)`
      listing columns + naming `sample_id_column`, with no run persisted.
- [ ] 4.8 RED: both genotype **and** sample_id missing → a single structured error listing available
      columns and naming **both** overrides.
- [ ] 4.9 RED: the `qc_clean` `inputSchema` (via a FastMCP `Client`) exposes `sample_id_column`,
      `genotype_column`, and `exclude_columns`; `run_qc_workflow` still listed.

## 5. Goldens — decoupled outlier follow-up

- [ ] 5.1 The **qc_clean** golden re-record is done in §3.5 (rides commit 2 for CI-green). No separate
      golden commit here.
- [ ] 5.2 **Decoupled:** the `remove_outliers` golden (`turface_19_outlier_golden.json`) also shifts
      under the 19/17 trait set, but that fixture + `test_remove_outliers_tool.py` are **not on this
      branch's base** (they live on #378/#400, unmerged). Do **not** edit them here. Track a
      **follow-up** to re-record that golden once #400 merges + this lands, and note the dependency in
      `fixtures/README.md`.

## 6. Docs + validation gate

- [ ] 6.1 Update the **agent-facing surface**: the `qc_clean` function docstring + `QCCleanParams`
      field descriptions state that a genotype + sample identifier are now **required** and name the
      overrides (so `tools/list` clients learn the contract). Update `bloommcp/docs/local-validation.md`
      (and any `qc_clean` dogfood row) accordingly; opportunistically refresh the already-stale
      `pca_analysis` "not in the current MCP surface" finding while editing that file.
- [ ] 6.2 Run the full gate locally (from `bloommcp/`): `uv run --frozen --extra test pytest tests/`,
      `black --check`, `ruff check`, **`ruff format --check`**, `uv lock --check`, **build the wheel
      and `python -c "import bloom_mcp.data_access"` in a clean env** (mirrors `pr-checks.yml`'s
      wheel-import job), and `openspec validate add-bloommcp-qc-input-validation --strict` — all green
      before opening the PR to `staging`.

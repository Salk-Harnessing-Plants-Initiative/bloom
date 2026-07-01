> **TDD note:** the RED steps (§2–§3) are a *local* working-tree rhythm — confirm each fails,
> then make it pass. Do **not** push a RED-only commit (CI gates the PR head). Commit RED+GREEN
> together. The tool module + its `register()` + the `server.py` registration line are **one
> atomic commit** — never commit the server registration before the module exists (boot import
> error → red head). Suggested commits: `chore(#360)` golden + fixture docs (green) →
> `feat(#360)` tool + tests, atomic (green) → optional `refactor(#360)`.
>
> **Branch base & merge order (load-bearing — verified: #356 is OPEN and the fixture/helpers
> exist only on its branch).** Base this change on **#356's branch** (`qc_clean`) while #356 is
> open; **rebase onto `origin/staging` once #356 merges**; **never merge `qc_inspect` before
> #356** — `turface_19_raw_data.csv` and the shared role/validation helpers do not exist on
> `staging` until #356 lands, so a premature rebase reds the `python-audit` job. PR targets
> `staging` (not `main`). Before #356 merges, reviewers review qc_inspect's diff **against the
> #356 branch**, not against staging.

## 1. Pre-work — reuse the raw fixture + record the inspection golden + share the helpers

- [x] 1.1 Confirm the analyze pin already provides the EDA functions: `from sleap_roots_analyze
      import create_trait_eda_plots, create_exploratory_summary_plots, inspect_nan_samples,
      apply_data_cleanup_filters` imports on the pinned `0.1.0a3` (no `pyproject`/`uv.lock`
      change — #356 already bumped it). Acceptance: import succeeds under `uv run --frozen`, and
      `uv lock --check` stays **byte-identical** (the test/golden additions add no transitive, so
      `scripts/check-uv-locks.py` must report no drift).
- [x] 1.2 Reuse #356's raw fixture `bloommcp/tests/fixtures/turface_19_raw_data.csv` (no new
      fixture). Record `bloommcp/tests/fixtures/turface_19_qc_inspect_golden.json` (LF, per
      `.gitattributes`) from the LF-normalized raw fixture, computed **independently of the tool**:
      full-precision per-trait NaN fractions for `Root_Biomass_mg` / `Root_Shoot_Ratio`
      (**0.1551**, i.e. 29/187), and — at the canonical `max_nans_per_trait = 0.2` /
      `max_nans_per_sample = 0.0` defaults — `traits_would_be_removed = []`, `samples_lost = 29`
      (kept NaN-heavy traits force the sample drop), `residual_nan_cells = 0`,
      `recommended_max_nans_per_trait` strictly `< 0.1551`, `would_remove_traits =
      [Root_Biomass_mg, Root_Shoot_Ratio]`, `samples_lost_at_recommendation = 0`. **Do not**
      restate the `naive_dropna = 29` /
      two-trait facts — the existing `turface_19_qc_golden.json` README paragraph already owns
      them; the new entry **cross-references** it (avoids a third drifting copy). Document the new
      golden in `tests/fixtures/README.md` in the existing house style ("independently sourced,
      not re-derived from the code under test").
- [x] 1.3 Shared role/validation helpers. **Preferred:** the pure-move extraction of
      `_role_kwargs` + `_validate_trait_subset` from `qc_clean_tool.py` into
      `tools/_qc_shared.py` lands **in #356** (`refactor(#338)`), where that file is already
      owned and tested — qc_inspect then simply **imports** `_qc_shared`, zero `qc_clean_tool.py`
      conflict. **Fallback only if #356 won't take it:** perform the pure move here as a separate
      `refactor(#360)` commit; acceptance: the existing `qc_clean` suite stays green (no behavior
      change). Either way, qc_inspect depends on `_qc_shared`, not on `qc_clean`'s private symbols.

## 2. RED — the recommendation oracle through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_qc_inspect_tool.py`; wire `_ports.configure(...)` with a
      `FakeReader` serving the **raw** turface_19 fixture and a `FakeResultStore`.
- [x] 2.2 Write the **recommendation oracle** test FIRST: invoke `qc_inspect` at the canonical
      defaults (`max_nans_per_trait = 0.2`, `max_nans_per_sample = 0.0`); assert the per-trait NaN
      fractions report the two traits at 0.1551, `traits_would_be_removed == []`,
      `samples_lost_at_current_params == 29`, the recommendation's `would_remove_traits ==
      {Root_Biomass_mg, Root_Shoot_Ratio}`, `samples_lost_at_recommendation == 0`, and
      `recommended_max_nans_per_trait < 0.1551` (strict-less-than, full precision — guards an
      off-by-epsilon flake). Confirm it fails (no tool yet).
- [x] 2.3 Write the **threshold-tracking** assertion: at `max_nans_per_trait = 0.1` the two traits
      are already in `traits_would_be_removed` and the recommendation reports zero residual sample
      loss. Confirm RED.
- [x] 2.4 **Zero-NaN "no-change" branch** (spec scenario + design Risk, currently untested): seed a
      `FakeReader` frame with no NaNs in any trait; assert the recommendation reports "no change
      recommended" (current thresholds lose zero samples), `would_remove_traits == []`, and
      `samples_lost_at_recommendation == 0` — not a spurious lower threshold. Confirm RED.
- [x] 2.5 **All-NaN / NaN-heavy trait is reportable, not an error** (spec "Raw experiment is loaded
      for inspection"): seed a frame with one all-NaN trait; assert `qc_inspect` returns a report
      (that trait's NaN fraction == 1.0 and it appears in `traits_would_be_removed`) and does
      **not** raise. Confirm RED.

## 3. RED — the remaining contract patterns + read-only + figure round-trip

- [x] 3.1 `tools/list` presence: a FastMCP `Client` (the `asyncio.run(async with Client(server.mcp)
      ...)` idiom from `test_package_baseline.py`) lists `qc_inspect` with an input schema;
      `qc_clean` and `run_qc_workflow` still listed.
- [x] 3.2 Schema round-trip: valid input/output validate; an invalid input (missing experiment, or
      a threshold outside `[0,1]`) → `BloomMCPError`; assert the **code** is the input/validation
      code (e.g. `exc.value.code == "invalid_input"`), not just that an error is raised.
- [x] 3.3 Provenance + links: a successful call stamps `Provenance` with the tool name, the
      threshold + trait-selection params, and `seed = None`; the persisted `StoredRun` for
      `(experiment, "qc_inspect")` carries the same provenance and includes the EDA figure(s),
      the NaN-samples CSV, and `recommendation.json`; the returned result references those via
      links (object keys + manifest path) and embeds **no** figure/table blob inline.
- [x] 3.4 Delegation pinning (spy on the analyze functions): assert `qc_inspect` calls
      `create_trait_eda_plots` and `inspect_nan_samples` **exactly once** each and
      `apply_data_cleanup_filters` **at least once** (it is also called to evaluate the recommended
      threshold), forwards `barcode_col=frame.sample_id_col` / `genotype_col=…` / `replicate_col=…`
      where accepted, and **never** calls the vendored `bloom_mcp.data_cleanup` filters.
- [x] 3.4b **No figure-handle leak + headless backend** (guards the design's "close the rest" and
      "Agg" decisions): record `len(matplotlib.pyplot.get_fignums())` before the call and assert it
      returns to that baseline after `qc_inspect` (every created figure — including the unused
      `create_exploratory_summary_plots` panels — was closed). Plus an import-time guard:
      `assert matplotlib.get_backend().lower() == "agg"` after importing the tool module, so a
      headless CI runner never trips a Tk backend.
- [x] 3.5 Role-column fallback: seed a `FakeReader` frame whose role columns detect as `None`;
      assert `qc_inspect` does **not** forward `None` (omits the kwarg / uses the default) and
      still produces a report run.
- [x] 3.6 Error envelope: an unresolvable experiment → `BloomMCPError` with a remedy and no run; a
      delegate raise → structured error, no leaked traceback/path. **Plus a real-delegate
      degenerate case (no mock):** pathological thresholds passed into `apply_data_cleanup_filters`
      surface as a structured `BloomMCPError`, not the contract's opaque `internal_error` (the
      distinction #356 found load-bearing for `qc_clean`).
- [x] 3.7 `trait_columns` validation: an unknown column → `invalid_input` naming it; a non-numeric
      column (e.g. `geno`) → `invalid_input` (shared `_validate_trait_subset`).
- [x] 3.8 **Read-only structural assertion (fakes):** the committed run is under tool class
      `qc_inspect` and its outputs contain **no** `CLEANED_CSV_NAME`. Necessary but **not
      sufficient** for the resolver guarantee — see 3.8b.
- [x] 3.8b **Read-only over the real resolver (negative composition — the load-bearing one):**
      drive a `qc_inspect` run through `SupabaseResultStore` over the shared `_InMemoryObjectStore`
      (the harness #356 stood up; `tests/conftest.py:41`), then assert a fresh
      `SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)` raises
      `CleanedVersionRequiredError` — the committed `qc_inspect` run is **not** resolved as a
      cleaned version. The fakes' reader/store are disjoint and cannot exercise the resolver, so
      this mirrors #356's *positive* composition test as its negative twin.
- [x] 3.9 **Figure-persistence round-trip (real bytes, via the adapters — NOT the fake):** the
      `FakeResultStore` deletes its staging dir at commit and retains no bytes, so commit a
      `qc_inspect` run through `SupabaseResultStore` over the shared `_InMemoryObjectStore`. For
      each committed PNG, read the stored bytes back by `output_keys[name]` and assert: non-empty,
      begins with the PNG magic `b"\x89PNG\r\n\x1a\n"`, and `hashlib.sha256(bytes).hexdigest() ==
      stored.output_sha256[name]`. Read back `recommendation.json` and assert it deserializes to
      the recommendation returned inline.
- [x] 3.10 Second run increments version: two `qc_inspect` runs → `v1`, `v2`; `latest` → `v2`.

## 4. GREEN — implement the tool

- [x] 4.1 Add `bloommcp/src/bloom_mcp/tools/qc_inspect_tool.py`: `QCInspectParams(BaseModel)`
      (experiment, optional `trait_columns`, the same four cleanup thresholds as `qc_clean` with
      `[0,1]` / positive-int validation, optional `user_label` — **no `seed`**) and a
      `QCInspectResult` output model (raw `n_samples` / `n_traits`, per-trait NaN summary, the
      nested `recommendation`, and links — object keys + `manifest_path` + `run_ref`).
- [x] 4.2 **At the very top of the module, before any `from sleap_roots_analyze import …` and
      before importing pyplot:** `import matplotlib; matplotlib.use("Agg"); import
      matplotlib.pyplot as plt` (the `viz_tools.py:14-17` pattern — analyze's `visualization.py`
      does a bare pyplot import with no backend set, so importing it first resolves to interactive
      `tkagg` and crashes `savefig` headless). Implement `qc_inspect(params, *, provenance)`
      wrapped by `@as_mcp_tool` (declares `provenance`, **not** `random_state`): load the **raw**
      frame via `_ports.reader().load_experiment(name)` (no `require_clean`), mapping
      `ExperimentReadError` → `BloomMCPError`; validate any `trait_columns` subset via the shared
      helper.
- [x] 4.3 Compute the report by delegation: one `apply_data_cleanup_filters(df, trait_cols,
      **thresholds, **_role_kwargs(frame))` → `cleanup_log`; `create_trait_eda_plots(df,
      trait_cols, thresholds={"nan": max_nans_per_trait, "zero": max_zeros_per_trait},
      cleanup_log, min_samples_per_trait)`; the `missing_data_pattern` figure from
      `create_exploratory_summary_plots`; `inspect_nan_samples` → CSV. Derive the recommendation
      (see design). **`plt.close()` every figure, including the unused
      `create_exploratory_summary_plots` panels** (no handle leak). **No EDA logic in the MCP**;
      **no** `bloom_mcp.data_cleanup` call.
- [x] 4.4 Persist via `_ports.store().create_run(experiment=…, tool_class="qc_inspect",
      provenance=provenance, …)` → save the figure PNGs + `nan_samples.csv` + `recommendation.json`
      into the run's staging dir → `commit(...)`; return the inline summary + recommendation +
      links from the `StoredRun`. **Never** write `CLEANED_CSV_NAME` and **never** use tool class
      `qc`.
- [x] 4.5 Add `register(mcp)` using `bloom_mcp.contract.register(mcp, qc_inspect)`.
- [x] 4.6 Register the module in `src/bloom_mcp/server.py` under "Direct tools (granular)" and add
      `qc_inspect` to its module-docstring tool list — **in the same commit as 4.1–4.5** (the
      registration line imports the module; splitting it reds the head).
- [x] 4.7 Run the suite; debug to GREEN **without** weakening the recommendation oracle, the
      read-only guarantee, or the figure round-trip.

## 5. Refactor & verify

- [x] 5.1 Refactor for clarity; keep the delegate → output-model mapping isolated. Confirm
      `qc_clean` (now importing `_qc_shared`), `bloom_mcp.data_cleanup`, and `run_qc_workflow` are
      untouched in behavior and the server still boots headless.
- [x] 5.2 `/pre-merge`: lint (`black --check` + `ruff check`) + the exact CI suite command
      `cd bloommcp && uv run --frozen --extra test pytest tests/` + `uv run --frozen` import +
      `python scripts/check-uv-locks.py` (lock byte-identical) + the clean-env wheel import (the
      new `_qc_shared.py` / `qc_inspect_tool.py` must import from the built wheel) +
      `openspec validate add-bloommcp-qc-inspect-tool --strict` all green.
- [ ] 5.3 Validate on **Claude Desktop** (capable model): `qc_inspect` is selectable, returns the
      missingness recommendation, the figure links resolve, and a follow-up `qc_clean` run uses the
      recommended threshold; sanity-check on the small-model surface that the structured result is
      sane.

## 6. Coordination / follow-ups (out of this change's spec deltas)

- [ ] 6.1 `qc_clean` tie-in (message-only nudge to call `qc_inspect` when `n_samples_dropped > 0`):
      **lands in #356's branch** — it modifies `qc_clean`'s tested output and keeps the spec change
      with its capability. **This is NOT a deliverable of this change** (tracked here only as an
      upstream dependency, so qc_inspect carries no dangling task).
- [ ] 6.2 Roadmap Tier-table edit stays owned by **PR #339** (`eberrigan/bloommcp-tier3-qc`) — do
      **not** edit `bloommcp/docs/roadmap.md` here. **Safeguard:** #339 currently owns the Tier-row
      edit for *both* `qc_clean` and `qc_inspect` and has not landed; confirm `qc_inspect` is in
      #339's scope, and if #339 stalls, file a follow-up so the roadmap is not left without either
      tool.
- [ ] 6.3 Optional live-persistence smoke leg for `qc_inspect` (report run + figure round-trip
      through the real Supabase ports) — deferred unless a reviewer wants it in-scope (see design
      Open Questions). `local-validation.md` correctly does not claim qc_inspect smoke coverage.
</content>

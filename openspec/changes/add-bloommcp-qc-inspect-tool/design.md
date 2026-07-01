## Context

`qc_inspect` is the **read-only** Tier-3 sibling of `qc_clean`. It reuses the same seams
`qc_clean` bound (the contract decorator, the `ExperimentReader` / `ResultStore` ports, the
`_ports` composition root) and the same upstream library — but where `qc_clean` *produces* a
cleaned run, `qc_inspect` *produces a report* that helps the agent choose `qc_clean`'s
thresholds. The constraints are fixed by the shipped code:

- `@as_mcp_tool(input_model=, output_model=, errors=)` validates Pydantic I/O, maps exceptions
  to `BloomMCPError`, resolves the seed, and stamps one `Provenance`. A tool that declares no
  `random_state` records `seed=None`. `contract/wrap.py`
- `ExperimentReader.load_experiment(name, *, version="latest", require_clean=False)` returns an
  `ExperimentFrame` exposing `df`, `trait_cols`, the detected role columns, and a `source`
  label. `qc_inspect` calls it with the defaults (raw, no `require_clean`). `data_access/ports.py`
- The `SupabaseReader` resolves a *cleaned* version only from **`qc` tool-class runs that wrote
  `_cleaned.csv`**. A run under any other tool class is never resolved by `require_clean=True` —
  which is exactly how `qc_inspect`'s read-only guarantee is enforced structurally.
  `data_access/supabase_reader.py`
- `ResultStore.create_run(*, experiment, tool_class, provenance, …) -> RunHandle` then
  `commit(run, outputs) -> StoredRun`. `commit` hashes the exact staged bytes and uploads each
  staged file, so **binary PNGs persist as faithfully as text CSVs** (it is content-agnostic).
  `result_store/ports.py`, `result_store/supabase_store.py`
- The upstream delegates (all public in `sleap_roots_analyze 0.1.0a3`, already pinned by #356):
  `apply_data_cleanup_filters(df, trait_cols, …thresholds…, barcode_col, genotype_col,
  replicate_col) -> (filtered_df, cleanup_log)`; `create_trait_eda_plots(df, trait_cols,
  thresholds: {"nan","zero","outlier"}, cleanup_log, min_samples_per_trait) -> {name: Figure}`;
  `create_exploratory_summary_plots(df, trait_cols, genotype_col) -> {..., "missing_data_pattern":
  Figure}`; `inspect_nan_samples(df, trait_cols, barcode_col, genotype_col, replicate_col) ->
  DataFrame`.
- `qc_clean` already implements `_role_kwargs(frame)` (forward detected roles, omit `None`) and
  `_validate_trait_subset(frame, requested, experiment)` (existence + numeric → `invalid_input`).
  These are identical to what `qc_inspect` needs. `tools/qc_clean_tool.py`

## Goals / Non-Goals

- **Goals:** one contract-wrapped, **read-only** `qc_inspect` tool, registered + discoverable,
  delegating all EDA to the analyze functions, producing a per-trait missingness report + a
  threshold recommendation through the MCP boundary, persisting a **versioned report run** that
  is **not** a cleaned version, with the 5 contract patterns + the turface_19 recommendation
  oracle + a figure round-trip under test.
- **Non-Goals:** any EDA/plotting logic in the MCP; calling `bloom_mcp.data_cleanup`; producing
  a cleaned table or anything resolvable by `require_clean=True`; seed threading (inspection is
  deterministic); changing `qc_clean`'s cleanup behavior or its defaults; the live-persistence
  smoke leg (deferred — see Open Questions); a new dependency or fixture.

## Decisions

- **Decision: persist-and-link the figures (the issue's proposed default), not transient image
  content.** `qc_inspect` writes the EDA figures + NaN-samples CSV + `recommendation.json` as a
  versioned `ResultStore` run and returns `resource_link`s — same reproducibility contract as
  `qc_clean` (small structured results inline + links, **never inline blobs**). A persisted run
  gives the recommendation a citable, versioned artifact the agent (and a human) can revisit,
  and keeps `qc_inspect` symmetric with every other persisting tool.
  - *Alternative considered:* return transient MCP image content for the agent to display
    without persisting a run (lighter, no versioned artifact). Rejected as the default: it
    breaks the links-not-blobs contract and leaves the recommendation un-citable. The **wrap is
    identical either way**, so this is reversible if a transient "preview" mode is later wanted.
    Surfaced as the one decision a reviewer may want to flip before implementation.
- **Decision: persist under a distinct tool class `qc_inspect`, never `qc`.** The reader resolves
  cleaned versions only from `qc`-class runs writing `_cleaned.csv`. Writing under `qc_inspect`
  makes the read-only guarantee **structural** (not a convention): a `qc_inspect` run can never
  be mistaken for a cleaned version, so `require_clean=True` never resolves it.
- **Decision: read raw, do not set `require_clean`.** The point is to show the *raw* missingness
  the agent must reason about. Loading a cleaned frame would hide the very NaNs being inspected.
- **Decision: accept the same thresholds as `qc_clean` and drive both the overlay and the
  recommendation from one `apply_data_cleanup_filters` call.** The cleanup log that
  `create_trait_eda_plots` overlays as "traits actually removed" is the same log the
  recommendation reasons over — computing them from one delegate call keeps the picture and the
  advice consistent, and mirrors how analyze's own `exploratory_analysis` step feeds the plots.
- **Decision: the recommendation is derived, simple, and pins consequences not a formula.** Given
  the supplied thresholds, the recommended `max_nans_per_trait` is the largest value strictly
  below the smallest NaN fraction among traits that still carry NaNs after the current filters
  (so it drops exactly the offending traits) — and the tool reports the *consequence*
  (`would_remove_traits`, `samples_lost_at_recommendation`, `samples_lost_naive_dropna`). The
  spec oracle pins the **consequence** on turface_19 (drops the two 0.155 traits, 0 samples lost
  vs naive 29), not the exact arithmetic, so the formula can be refined without a spec churn.
- **Decision: declare `provenance`, not `random_state`; seed is `None`.** Inspection is
  deterministic (fraction thresholds, no `random_state`). The thresholds + trait selection are
  the determinism-governing params captured in provenance.
- **Decision: reuse `qc_clean`'s role-forwarding + trait-validation via a shared `_qc_shared`
  module, with the extraction landing in #356.** Both tools forward detected roles (omitting
  `None`) and reject unknown / non-numeric `trait_columns` identically; a single `_qc_shared`
  module both import keeps them in lockstep rather than drifting as two copies. The pure move
  **should land in #356** (which already owns and tests `qc_clean_tool.py`), so qc_inspect only
  *imports* `_qc_shared` — avoiding a guaranteed `qc_clean_tool.py` merge conflict between two
  open PRs editing the same hunks. If #356 declines, qc_inspect's branch carries the move as a
  separate `refactor(#360)` commit, keeping `qc_clean`'s suite green.
- **Decision: set the Agg matplotlib backend at module top, before importing the analyze viz
  functions, and close every figure.** analyze's `visualization.py` does a bare
  `import matplotlib.pyplot` with **no** `use("Agg")`, so whichever backend is active at first
  import wins — importing the analyze EDA functions first resolves to interactive `tkagg` and
  crashes `savefig` in a headless container (verified). So `qc_inspect_tool.py` must do
  `import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt` at the very top
  (the `viz_tools.py:14-17` pattern) *before* `from sleap_roots_analyze import …`, and
  `plt.close(fig)` after staging each figure — including the unused
  `create_exploratory_summary_plots` panels — so a server process leaks no figure handles. A
  `get_backend() == "Agg"` import-time assertion guards the headless guarantee from regressing.
  (The server is headless-safe *today* only incidentally, because `viz_tools` runs `use("Agg")`
  during boot; qc_inspect must not depend on that ordering.)
- **Decision: the `qc_clean` tie-in is message-only and lands with #356, not here.** #356's
  no-NaN guard means the *cleaned output* never carries residual NaNs, so the issue's "residual
  NaNs in the cleaned output > 0" trigger is stale; the honest trigger is **sample loss**
  (`n_samples_dropped > 0`). The nudge is one behavior-compatible line in `qc_clean`'s result;
  because `bloommcp-qc-clean-tool` is still in-flight (not in `specs/`), keeping the nudge in
  #356's branch keeps the spec change with its capability and avoids a MODIFIED delta against a
  not-yet-archived capability.

## Risks / Trade-offs

- **Figure determinism / size.** Persisted PNGs vary byte-for-byte across matplotlib/font
  versions, so the round-trip test asserts *non-empty image + sha matches the recorded bytes of
  that run*, not equality to a golden PNG. The recommendation JSON (the agent-facing payload) is
  the value-pinned oracle.
- **Fakes prove the per-port contract; the read-only guarantee and the figure round-trip need
  the adapters.** Drive provenance + links through the `_ports` seam with
  `FakeReader`/`FakeResultStore`. But two claims the fakes **cannot** prove:
  (1) the read-only *resolver* behavior — `FakeReader._cleaned` and `FakeResultStore._runs` are
  disjoint, so a committed `qc_inspect` run is invisible to the fake reader; the structural
  check (tool class `qc_inspect`, no `CLEANED_CSV_NAME`) is necessary but **not sufficient**.
  (2) the figure byte round-trip — `FakeResultStore.commit()` `rmtree`s its staging dir and
  retains no bytes. Both must run through `SupabaseResultStore` over the shared
  `_InMemoryObjectStore` (the harness #356 stood up): a *negative* composition test asserting
  `SupabaseReader.load_experiment(require_clean=True)` raises `CleanedVersionRequiredError`
  (the mirror of #356's *positive* composition), and a real-bytes readback asserting PNG magic +
  `sha256 == output_sha256`. The fakes path asserts the per-port contracts; the
  adapters-over-object-store path asserts the resolver handoff and the persisted bytes.
- **`create_exploratory_summary_plots` builds extra figures** (histograms, correlations) we do
  not need. Take only its `missing_data_pattern` figure and close the rest, or build just the
  heatmap if its surface lets us — avoid persisting unused panels.
- **Recommendation when nothing crosses.** If no trait carries NaN after the current filters,
  the recommendation reports "no change recommended" (current thresholds already lose no
  samples), not a spurious lower threshold.

## Migration Plan

Additive only — a new read-only tool + one registration line + a pure-move refactor of two
helpers. No schema, data, or dependency change; existing manifests are unaffected. Rollback =
unregister the tool (the helper extraction is independently safe).

## Open Questions

- **Persist-and-link vs transient figures** — proposal recommends persist-and-link (above); a
  reviewer may opt for transient/preview content. The wrap is identical; settle before §4.
- **Live-persistence smoke leg.** #356 added a Tier-3 `qc_clean` smoke leg; a parallel
  `qc_inspect` leg (report run persists + figures round-trip through the real Supabase ports) is
  reasonable but heavier and not in the issue's test list. Deferred to a follow-up unless a
  reviewer wants it in-scope.
- **Exact recommended-threshold arithmetic** vs only reporting consequences — settle when the
  inspection golden is recorded (§1), keeping the spec oracle on consequences.
</content>

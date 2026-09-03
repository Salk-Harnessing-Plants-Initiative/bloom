# Tasks — inline `csv_content` across every experiment-reading tool

TDD throughout: failing test first, then code. Each tool's section is self-contained and
independently verifiable — that is what preserves #582's "tested individually" intent.

**PR boundaries** (design.md Decision 11): PR 1 = §0-§2 · PR 2 = §4-§8 · PR 3 = §3, §2.7, §9-§13.
§3 (`qc_inspect`) must land **before** §2.7 (`qc_clean`'s `next_step` lift), which recommends an
inline `qc_inspect` call.

## 0. Result-model widening — first, because every tool depends on it

- [x] 0.1 Test: `RunLinks` still exposes exactly `run_ref`, `version_dir`, `manifest_path`,
      `outputs`, `output_links`; `from bloom_mcp.contract import RunLinks` still works; the
      existing `bloommcp-tool-contract` scenarios pass unchanged.
- [x] 0.2 Test: a `RunLinks` subclass constructed with all three run links `None` and `outputs`
      omitted validates, with empty `outputs`/`output_links`; a **wrong-typed** `run_ref` (e.g.
      `42`) or a non-string-valued `outputs` still raises `ValidationError`.
- [x] 0.3 *(PR 1: satisfied for six consumers by their existing `result.run_ref == stored.run_ref` assertions, which fail on `None`; the seventh, `cross_experiment_correlations`, asserted persistence only through the store's own records and gained the assertion explicitly.)* Test, **per tool** (`pca_analysis`, `umap_analysis`, `clustering`, `descriptive_stats`,
      `remove_outliers`, `cross_experiment_correlations`, `qc_inspect`): a **registered** call
      still returns non-`None` run links and non-empty `outputs`. Widening removes the Pydantic
      guarantee that a persisting tool populated them; this replaces it rather than losing it.
- [~] 0.4 *(PR 1: `RunLinks` widened. The seven per-tool result models' `experiment` / `experiment_N` / `source_N` fields and their `input_sha256` widen with each tool in PR 2 and PR 3 — widening them ahead of a consumer would ship dead surface.)* Implement: widen `RunLinks`'s three run-link fields to `Optional[str]` (default `None`)
      and `outputs` to default `{}`; update `RunLinks`'s docstring, which says the fields are
      "returned by every consumer tool result". Widen the redeclared run-link fields on
      `QCInspectResult` and `ClusteringResult`, the `experiment` output field on all seven result
      models plus `SummarizeTraitResult`, and `experiment_1`/`experiment_2`/`source_1`/`source_2`
      on `CrossExperimentCorrelationsResult`. Add `input_sha256: Optional[str]` to each.

## 1. Shared resolver (`_inline_input`) — built and tested once

- [x] 1.1 Test: `resolve_inline_or_experiment` rejects both-supplied with `invalid_input`, calling
      neither the reader nor `pandas.read_csv` (spies assert zero calls).
- [x] 1.2 Test: rejects neither-supplied with `invalid_input`.
- [x] 1.3 Test: the exactly-one-of check runs **before** the registered-only-parameter check — a
      call that is wrong both ways reports the input conflict. (Without a defined order, §9.4's
      "names `version_2` only" is order-dependent and flaky.)
- [x] 1.4 Test: inline path returns a frame equal to `parse_inline_csv_frame`'s output,
      `input_sha256` equal to `compute_input_sha256`, `is_inline=True`, `label == "csv_content"`.
- [x] 1.5 Test: registered path returns the frame from the supplied `reader_call`,
      `input_sha256 is None`, `is_inline=False`, `label == experiment`.
- [x] 1.6 Test: the resolver takes the registered parameter's *name*, so `load_experiment_data`
      (`filename`) and `cross_experiment_correlations` (per-side) produce the same message text
      modulo that name.
- [x] 1.7 Test: `reject_registered_only_params` raises `invalid_input` naming the offending
      parameter, and is a no-op when the value is `None`/absent.
- [x] 1.8 Test: `serialize_table_csv` round-trips a DataFrame with no index column, emits `\n`
      (no `\r`) regardless of platform — pandas defaults `lineterminator` to `os.linesep`, which
      would make every digest platform-dependent — and raises `invalid_input` naming the size
      when the result exceeds `MAX_INLINE_CSV_BYTES`.
- [x] 1.9 Test: `MAX_INLINE_CSV_ROWS` — a frame above the cap is rejected with `invalid_input`
      naming the row count and the limit; a frame at exactly the cap is accepted.
- [x] 1.10 Test: `BLOOMMCP_INLINE_CSV_ENABLED` set false rejects every `csv_content` call with a
      remedy naming the registered path, and leaves the registered path untouched.
- [x] 1.11 Implement `InlineInput`, `resolve_inline_or_experiment`,
      `reject_registered_only_params`, `serialize_table_csv`, `MAX_INLINE_CSV_ROWS`, and the kill
      switch; rewrite the module docstring, which still says `qc_clean` is the only caller.
- [x] 1.12 Test first, then implement: `_validate_trait_subset`'s `certified_label` presentation
      parameter in `_qc_shared.py`. Assert the **accepted column set is byte-identical** with and
      without it (it is imported by eight modules, so the parameter must default), and that the
      inline wording claims no certification.

## 2. `qc_clean` — refactor onto the resolver, then extend

- [x] 2.1 Refactor `qc_clean` onto `resolve_inline_or_experiment`, in a commit touching **zero
      test files** — that is the refactor's entire claim and it is verifiable. Its existing suite
      passes unmodified. (Exception, deferred to 2.7: `test_inline_result_never_nudges_toward_
      qc_inspect` asserts `next_step is None` and must change — but not in this commit.)
- [x] 2.2 Test: `return_cleaned_csv=true` + `csv_content` returns `cleaned_csv` parsing back to
      the same table the registered path persists, no index column; `cleaned_csv_sha256` matches
      an independent digest.
- [x] 2.3 Test: `cleaned_csv` contains no `\r` and its digest is stable across repeated calls.
- [x] 2.4 Test: `cleaned_csv` re-parsed and run through `resolve_columns` yields a trait set
      **equal to** `kept_trait_columns` and the same genotype/sample-id/replicate roles.
      `qc_clean`'s no-NaN guard is scoped to *kept* columns, so the serialized table can carry NaN
      in removed/metadata columns; this is the invariant that makes client-side chaining sound and
      nothing else pins it.
- [x] 2.5 Test: `return_cleaned_csv=true` + `experiment` raises `invalid_input`; omitted/false
      leaves both fields `None` with the rest of the response unchanged.
- [x] 2.6 Test: an oversized serialized table raises `invalid_input` naming size and limit, with
      no truncated table returned; and `ResultStore` spy records zero `create_run`/`commit` even
      with `return_cleaned_csv=true`.
- [ ] 2.7 **(PR 3, after §3.)** Test + implement: inline cleaning that drops samples populates
      `next_step` naming `qc_inspect`, the same `csv_content`, and the `input_sha256`; no `'None'`
      identifier; no drop → `None`. This commit **rewrites**
      `test_inline_result_never_nudges_toward_qc_inspect` and its docstring, and updates
      `QCCleanResult.next_step`'s field description — say so in the commit body, since it breaks
      2.1's "unmodified" rule deliberately.
- [ ] 2.8 **(PR 3, after §3.)** Test: execute the `qc_inspect(csv_content=...)` call the inline
      `next_step` describes — it succeeds and returns diagnostics. The recommendation is verified
      against the real tool, not merely asserted to name it.
- [x] 2.9 Implement 2.2-2.6; update `QCCleanResult` field descriptions and both docstrings.

## 3. `qc_inspect` — the figure-suppression case (PR 3)

- [ ] 3.1 Test: mutual exclusivity (both / neither), message matching the shared vocabulary.
- [ ] 3.2 Test: equivalence oracle — the same raw fixture as `csv_content` vs. registered gives
      identical missingness summary, per-trait diagnostics, and threshold recommendation.
- [ ] 3.3 Test: `ResultStore` spy zero `create_run`/`commit`; `ExperimentReader` spy zero
      `load_experiment`; run links `None`, `outputs`/`output_links` empty.
- [ ] 3.4 Test: `input_sha256` matches an independent digest; `experiment is None`;
      `source == "inline"`.
- [ ] 3.5 Test: `source_id`, `run_id`, `user_label` each rejected with `invalid_input`.
      **`qc_inspect` has no `include_plots` parameter** — its figures are unconditional and its
      params model does not forbid extras, so a rejection test would fail with "DID NOT RAISE".
      There is nothing to reject here; 3.6 is the real work.
- [ ] 3.6 Test: an inline call makes **zero `Figure.savefig` calls** (patch it to raise) and
      creates no staging directory, while returning the full summary, diagnostics, and
      recommendation.
- [ ] 3.7 Test: the experiment-name validator is not reached on the inline path — an inline call
      does not raise `internal_error` from validating a `None` name.
- [ ] 3.8 Test: log-safety pair (success + forced `internal_error`), capturing loggers **and**
      stdout/stderr, with markers in both a data cell and a column name.
- [ ] 3.9 Implement; update module + function docstrings and `csv_content`'s field description to
      state that the inline path returns no figures.

## 4. `remove_outliers` (PR 2)

- [ ] 4.1 Test: mutual exclusivity; `version` / `user_label` / `include_plots=true` / `plots`
      rejected — **including `version="latest"`**, which the registered path coerces to
      `"latest_qc"`, making it a real pin request rather than a harmless default.
- [ ] 4.2 Test: equivalence oracle. Use `method="isolation_forest"` **and** a second pass with
      `method="mahalanobis"` under the existing `_force_trustworthy_mahalanobis_fit` helper —
      turface_19's default mahalanobis fit is `very_poor` and hits the gate, so a naive oracle
      would test the gate, not the equivalence. Feed the inline side
      `_cleaned_df().to_csv(index=False)` and assert on **flagged barcodes, not row positions**:
      the registered fixture is an in-memory frame with a non-contiguous index and the CSV
      round-trip resets it.
- [ ] 4.3 Test: the fit-quality gate fires on inline content with the same code, remedy, counts
      and barcodes — but with the identifier rendered `'csv_content'`, not `'None'` (the gate
      message interpolates `params.experiment!r`).
- [ ] 4.4 Test: persistence and reader spies both zero.
- [ ] 4.5 Test: `return_trimmed_csv=true` returns the trimmed table with a matching
      `trimmed_csv_sha256`, still zero `create_run`/`commit`; rejected with `experiment`;
      oversized output rejected naming size and limit.
- [ ] 4.6 Test: non-finite selected traits → `invalid_input` naming the columns. This is a **new**
      guard scoped to the inline path — `remove_outliers` has no finiteness check today (its
      docstring records that `require_clean` made the NaN path unreachable), so do **not** assert
      an unchanged registered-path `assumption_violated`; assert instead that the registered path
      still has no such check.
- [ ] 4.7 Test: log-safety pair (loggers + stdout/stderr, both markers).
- [ ] 4.8 Implement; update docstrings — including that the `version="latest_qc"` composition
      guarantee (a trim never taken from a prior trim) is structurally absent inline.

## 5. `pca_analysis` (PR 2)

- [ ] 5.1 Test: mutual exclusivity; `version` / `user_label` / `include_plots=true` / `plots` /
      `plot_font_family` / `plot_font_size` / `plot_alpha` rejected.
- [ ] 5.2 Test: equivalence oracle vs. registered, **and** the inline result reproduces the
      recorded #120 turface_19 golden to the same tolerance. (Convention check: the PCA oracle is
      not `integration`-marked, unlike UMAP's — keep it that way.)
- [ ] 5.3 Test: persistence and reader spies both zero; run links `None`; outputs empty.
- [ ] 5.4 Test: non-finite selected traits → `invalid_input` naming the columns, remedy naming
      `qc_clean(..., return_cleaned_csv=true)`; registered path still `assumption_violated` with
      its original text.
- [ ] 5.5 Test: `trait_columns` empty list / duplicate / outside-the-set each rejected inline,
      with wording claiming no certification.
- [ ] 5.6 Test: a `savefig` spy (patched to raise) records zero calls on every inline call, with
      `include_plots` omitted. Asserting the *shared plots directory* is unchanged is vacuous —
      only `_viz_shared.save_plot` writes there and only the legacy plot tools import it; these
      tools write into `run.staging_dir`. Keep the directory check as a backstop only.
- [ ] 5.7 Test: log-safety pair (loggers + stdout/stderr, both markers).
- [ ] 5.8 Test (chaining): `qc_clean(csv_content=<raw fixture>, return_cleaned_csv=true)` →
      `pca_analysis(csv_content=<that text>)` succeeds end to end.
- [ ] 5.9 Implement; update docstrings.

## 6. `umap_analysis` (PR 2)

- [ ] 6.1 Test: mutual exclusivity; `version` / `user_label` / `include_plots=true` and every
      plot-companion parameter rejected.
- [ ] 6.2 Test: equivalence oracle with an explicit identical `seed`. Mark it `integration` to
      match the existing UMAP oracles — they are marked for runtime, and the CI job has ~50%
      headroom against a 20-minute cap.
- [ ] 6.3 Test: the resolved seed is reported in the inline response **and** a spy confirms the
      delegate received that value, matching the registered path's spy. (`seed` has a concrete
      default, so asserting only that the response carries a number is near-vacuous.)
- [ ] 6.4 Test: persistence and reader spies both zero.
- [ ] 6.5 Test: non-finite selected traits → `invalid_input`; `savefig` spy zero calls.
- [ ] 6.6 Test: log-safety pair (loggers + stdout/stderr, both markers).
- [ ] 6.7 Implement; update docstrings.

## 7. `clustering` (PR 2)

- [ ] 7.1 Test: mutual exclusivity; `version` / `user_label` / `include_plots=true` / `plots`
      rejected.
- [ ] 7.2 Test: equivalence oracle **per supported `method`** (`kmeans`, `gmm`, `hierarchical`),
      with an explicit seed — two of the three are stochastic and the tool dispatches on `method`,
      so one method's pass does not cover the others.
- [ ] 7.3 Test: persistence and reader spies both zero; `savefig` spy zero calls.
- [ ] 7.4 Test: non-finite selected traits → `invalid_input`.
- [ ] 7.5 Test: the inline hierarchical sample cap rejects an oversized frame **before** the
      delegate is called (delegate spy zero calls), the same frame with `method="kmeans"` is
      accepted, and the registered path is unaffected at any size.
- [ ] 7.6 Test: `max_clusters` above its new upper bound is rejected by the schema on both paths.
- [ ] 7.7 Test: log-safety pair (loggers + stdout/stderr, both markers).
- [ ] 7.8 Implement, including the `max_clusters` bound and the hierarchical cap; update
      docstrings — the tool docstring currently says "via k-means / GMM" and omits `hierarchical`.

## 8. `descriptive_stats` (PR 2)

- [ ] 8.1 Test: mutual exclusivity; `version` / `user_label` rejected.
- [ ] 8.2 Test: equivalence oracle — identical per-trait statistics.
- [ ] 8.3 Test: persistence and reader spies both zero.
- [ ] 8.4 Test: a non-finite trait is routed to `failed_traits` on the inline path **exactly as on
      the registered path** — the call does **not** raise. This tool's documented design is
      deliberately per-trait, not all-or-nothing; it gains no finiteness guard.
- [ ] 8.5 Test: log-safety pair (loggers + stdout/stderr, both markers).
- [ ] 8.6 Implement; update docstrings.

## 9. `cross_experiment_correlations` — per-side (PR 3)

- [ ] 9.0 **Use a per-side label, not `InlineInput.label`.** The resolver's `label` reads
      `"csv_content"` for *either* inline side, so an error naming it is ambiguous the moment both
      sides are inline. `_qc_shared._validate_experiment_name` already solved this with its own
      `label=` parameter — follow that pattern and thread a per-side label
      (`csv_content_1` / `csv_content_2`) through this tool's messages. Raised in PR #778's review.
- [ ] 9.1 Test: exactly-one-of enforced **per side** — both-on-side-1, neither-on-side-2, each
      naming the offending side.
- [ ] 9.2 Test: mixed call `experiment_1` + `csv_content_2` succeeds, persists nothing,
      `input_sha256_1 is None`, `input_sha256_2` is the digest.
- [ ] 9.3 Test: the **mirror** direction `csv_content_1` + `experiment_2` — argument order is
      documented as significant for this tool, so 9.2 does not cover it by symmetry.
- [ ] 9.4 Test: `experiment_N` and `source_N` are `None` for an inline side (all four are required
      `str` today).
- [ ] 9.5 Test: two byte-identical inline sides rejected as self-correlation; and a **mixed** pair
      carrying the same table **succeeds** — pinning the documented limit of hash-based detection
      so nobody later "fixes" it inconsistently.
- [ ] 9.6 Test: `version_2` rejected when side 2 is inline while `version_1` on registered side 1
      is honored — error names `version_2` only. `user_label` rejected whenever either side is
      inline.
- [ ] 9.7 Test: composite-key guards (path-unsafe name, reserved encoding chars, dotted stem) fire
      for a registered side and are skipped for an inline side.
- [ ] 9.8 Test: equivalence oracle — both sides inline vs. both registered, identical correlations
      and significance results.
- [ ] 9.9 Test: a non-finite inline side raises `invalid_input` naming that side, not the existing
      `_reject_non_finite`'s `assumption_violated` with `'None'` interpolated.
- [ ] 9.10 Test: the inline trait-pair-product cap rejects before the delegate is called (delegate
      spy zero calls), while the widest existing both-registered oracle still passes.
- [ ] 9.11 Test: log-safety pair with a marker in **each** side's content.
- [ ] 9.12 Implement; update docstrings.

## 10. The two legacy frame readers (PR 3)

- [ ] 10.1 `load_experiment_data`: test mutual exclusivity with `filename`; equivalence of counts
      and trait columns vs. the registered path; the output string carries the `input_sha256` and
      states the content was not registered; `source_id`/`run_id` rejected.
- [ ] 10.2 `load_experiment_data`: test that inline errors are **returned as strings**, not
      raised — it is not `@as_mcp_tool`-wrapped and already reports a conflicting
      `source_id`+`run_id` this way. Test the reader spy records zero `load_experiment` calls, and
      a log-safety **success** leg. There is no `internal_error` envelope for this tool, so the
      error leg is not expressible — record that in design.md rather than leaving a silent gap,
      and guard its inline failures explicitly.
- [ ] 10.3 `load_experiment_data`: implement, including a `csv_content:` entry in its Google-style
      `Args:` docstring — that block **is** the parameter schema the agent reads.
- [ ] 10.4 `summarize_trait`: **create `tests/sections/phenotyping_segmentation/` and its test
      module** — this tool has no test coverage today, so this is standing up a package, a fixture
      harness, and the `_capture_all_logs` scaffold, not adding cases to an existing file.
- [ ] 10.5 `summarize_trait`: test mutual exclusivity; equivalence of the per-accession summary;
      `experiment is None` and `input_sha256` matching an independent digest; an unknown trait and
      a missing genotype column each name `csv_content`, never `'None'`; log-safety pair.
- [ ] 10.6 `summarize_trait`: implement.

## 11. Cross-cutting verification (PR 3)

- [ ] 11.1 Test: for the seven tools whose exclusivity is `experiment` vs `csv_content`
      (`qc_clean`, `qc_inspect`, `remove_outliers`, `pca_analysis`, `umap_analysis`, `clustering`,
      `descriptive_stats`), the both-supplied and neither-supplied messages and remedies are
      identical. `cross_experiment_correlations` (per-side) and `load_experiment_data` (pairs with
      `filename`, returns a string) are asserted separately in 9.1 and 10.1 — a literal
      "byte-identical across all ten" assertion is impossible and would force a meaningless
      lowest-common-denominator test.
- [ ] 11.2 Test (parametrized over all ten tools): after an inline call carrying a marker, no
      record held by `FakeResultStore` — manifest `VersionEntry.params` included — contains it.
      This is the direct assertion on the actual egress path; the `create_run` spies are a proxy.
- [ ] 11.3 Test (parametrized over the eight run-link-carrying tools): the inline response's run
      links are `None` and `outputs`/`output_links` empty.
- [ ] 11.4 Test: snapshot `list_existing_analyses(experiment)` for an experiment with committed
      runs; make one inline call per persisting tool; **clear the module-global
      `_RESPONSE_CACHE`** — a 30-second memo with no invalidation hook, without which the second
      call answers from cache and the assertion is vacuous — and compare. Payloads identical.
- [ ] 11.5 Test (parametrized over all ten tools): content whose header implies more than
      `MAX_INLINE_CSV_COLUMNS` columns is rejected with a spy on `pandas.read_csv` recording zero
      calls; content over `MAX_INLINE_CSV_BYTES` and over `MAX_INLINE_CSV_ROWS` likewise rejected.
      Proves the guards are reachable from every entry point rather than assumed.
- [ ] 11.6 Test: one representative inline call at `logging.DEBUG` leaks no marker — pinning the
      MCP dispatcher's `Received message` path rather than relying on the default level.
- [ ] 11.7 Test: a schema-level rejection of `csv_content` names the field location only, never
      its value.
- [ ] 11.8 Use one shared fixture for the roster tests that restores `_ports` in a `finally`.
      `_ports.configure` and `_RESPONSE_CACHE` are process globals, and tests spanning ten tool
      modules will otherwise leak state into unrelated modules.
- [ ] 11.9 Confirm no tool calls `pandas.read_csv` on caller content outside
      `parse_inline_csv_frame` (grep + review), complementing 11.5's runtime proof.

## 12. Verification — exact commands

- [x] 12.1 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m "not integration and not
      live_smoke" -v --tb=short` (as CI runs it, with `SUPABASE_URL=""`, `BLOOM_AGENT_KEY=""`).
- [ ] 12.2 `cd bloommcp && uv run --frozen --extra test pytest tests/ -m integration -v` — the
      oracles excluded from per-PR CI.
- [x] 12.3 `uv run --extra test pytest tests/unit/ -v --tb=short` from the repo root.
- [x] 12.4 `cd bloommcp && uv run black --check src tests && uv run ruff check src tests` —
      **not covered by CI**; pre-commit is the only gate, so skipping this locally means it is
      never checked.
- [ ] 12.5 `uv run pre-commit run --all-files` from the repo root.
- [x] 12.6 `openspec validate add-bloommcp-inline-csv-all-tools --strict`.
- [x] 12.7 Confirm every existing registered-path test and golden oracle passes **unmodified**.

## 13. Live smoke — the transport is where the size questions live

Follows the existing convention: `bloommcp/tests/smoke/test_<tool>_smoke.py`, `live_smoke`
marker, run in the `dev-stack-smoke` CI job.

- [ ] 13.1 One inline call per tool through the real container's MCP transport, asserting
      `input_sha256` and absence from `list_existing_analyses`.
- [ ] 13.2 A `qc_clean(return_cleaned_csv=true)` round-trip through the transport, confirming the
      response arrives intact — Caddy imposes no response-size limit and the bloommcp route
      streams, so this pins the fact rather than assuming it.
- [ ] 13.3 A payload at the byte cap is accepted and one over it is cleanly rejected **through the
      transport**, confirming no intermediate body limit turns `invalid_input` into a 413/502.
      Include the two-sided `cross_experiment_correlations` case (~10 MiB in one request).
- [ ] 13.4 After the calls above, `docker compose logs bloommcp` contains no marker.

## 14. Docs — the `tools/list` surface is the deliverable

### 14a. Single-source the field text first

- [ ] 14.1 Add canonical description constants and helpers to `_inline_input.py`
      (`CSV_CONTENT_DESCRIPTION`, the mutual-exclusivity suffix, the registered-only rejection
      suffix, `INPUT_SHA256_DESCRIPTION`), each parameterized by field name so
      `csv_content_1`/`_2` reuse them rather than forking. `csv_content`'s text stays the single
      canonical home of the "no history" caveat, as #582 established.
- [ ] 14.2 Test (parametrized over the roster, asserting on each tool's **generated JSON input
      schema**, not its source): every `csv_content` description equals the constant; every
      registered-experiment parameter's description ends with the mutual-exclusivity suffix; every
      registered-only parameter carries the rejection suffix. This is 11.1's documentation
      counterpart and fails on the eleventh hand-written copy.

### 14b. Retract what this change makes false

- [ ] 14.3 `grep -rn "no csv_content support\|only tool\|sole consumer" bloommcp/ langchain/
      _WIKI/ openspec/` returns nothing still asserting `qc_clean`'s exclusivity. Known hits:
      `QCCleanResult.next_step`'s description ("always None on the csv_content path"), the inline
      branch comment in `qc_clean.py`, `_INLINE_EXPERIMENT_LABEL`'s stale spec cross-reference,
      and `_inline_input.py`'s "the first (and only) caller".
- [ ] 14.4 `qc_clean.py` module + `QCCleanResult` docstrings: "never the table inline" is now
      conditional — name `return_cleaned_csv`, its inline-only scope, and the size cap. Same for
      `remove_outliers.py`'s "no table inline" in both its result docstring and its **tool**
      docstring.

### 14c. Per-tool docstrings — each updates (i) the module docstring's "reads via the
### ExperimentReader port" sentence, (ii) the tool docstring `tools/list` shows, (iii) the result
### model docstring where the shape changes. Ship each **inside** that tool's feature commit.

- [ ] 14.5 `qc_inspect` — raw read via the port; persists a report run + figure links; **and** the
      figure-free inline variant.
- [ ] 14.6 `remove_outliers` — the reader and `version="latest_qc"` are both skipped inline; the
      fit gate still fires and why; the absent composition guarantee.
- [ ] 14.7 `pca_analysis` — two-path read; the "certified set" paragraph gains the caller-asserted
      case and the `invalid_input`-not-`assumption_violated` split; persistence is registered-only.
- [ ] 14.8 `umap_analysis` — same, plus: an omitted seed is still resolved and reported because
      the caller has no persisted provenance to recover it from.
- [ ] 14.9 `clustering` — same; and its tool docstring currently says "via k-means / GMM", omitting
      `hierarchical`.
- [ ] 14.10 `descriptive_stats` — two-path read; note the inline response differs only in
      identity/persistence fields, since its table is already inline on both paths.
- [ ] 14.11 `cross_experiment_correlations` — per-side resolution; either side inline ⇒ ephemeral,
      and why a half-resolvable lineage record is worse than none; the composite-key guards apply
      only to registered sides; self-correlation extends to equal `input_sha256`.
- [ ] 14.12 `load_experiment_data` — add a `csv_content:` entry to its Google-style `Args:` block
      (that block *is* the schema), update the module docstring, document the two extra output
      lines.
- [ ] 14.13 `summarize_trait` — its tool docstring instructs "Use after list_available_experiments
      + load_experiment_data"; add the no-registration route.

### 14d. Rosters and markdown

- [ ] 14.14 `server.py`'s tool-roster comment — record which tools accept inline content, and fix
      the staleness this makes conspicuous (`cross_experiment_correlations` missing;
      `phenotyping_segmentation` described as an "empty scaffold" though it holds four tools).
- [ ] 14.15 `sections/sleap_roots/__init__.py` ("the 8 granular consumers") and
      `sections/core/__init__.py` ("thin shims over experiment_utils / the injected ports" — no
      longer the whole story for `load_experiment_data`).
- [ ] 14.16 Rewrite `bloommcp/docs/connecting-claude-code.md`'s one-off-analysis section as a
      cross-tool capability: the roster, the `return_cleaned_csv` → next-tool chaining recipe, the
      plots limitation and why, the DEBUG-log-level caveat, and the "no history" caveat still
      **pointing at** the canonical field description rather than restating it.
- [ ] 14.17 `bloommcp/README.md` — add `_inline_input` to the shared-helpers list and the two
      input modes to the opening summary. `_WIKI/BLOOMMCP/README.md` — same helper addition.
      `_WIKI/BLOOMMCP/adding-a-section-tool.md` uses `summarize_trait` as its canonical example
      and is about to become two-path: note it or repoint at a single-path tool.
- [ ] 14.18 `langchain/tools/context_tools.py`'s `CONTEXT_MCP` system prompt asserts experiments
      are "identified by an experiment identifier (not a table)", which would suppress the inline
      path on Bloom's own web chat — and `load_experiment_data` is foundational there. State the
      second input mode. Docstring updates do not reach this consumer.
- [ ] 14.19 `bloommcp/docs/roadmap.md` — the #388 "upload inputs via chat" row should
      cross-reference #582's ephemeral path as the answer for its no-persistence half.
- [ ] 14.20 `bloommcp/docs/local-validation.md` — add an inline leg to the dogfood checklist
      (at minimum `qc_clean(return_cleaned_csv)` → `pca_analysis` chaining, and a confirmed-absent
      `list_existing_analyses` entry), and carry forward #582's still-unchecked live Claude Code
      validation rather than dropping it.
- [ ] 14.21 `tools/list` against a locally running server: every schema shows `csv_content`, the
      mutual-exclusivity clause, and the rejection clause on each registered-only parameter.

## 15. Follow-ups (not this change)

- [ ] 15.1 `heritability_analysis` (#462) — its `csv_content` path once that branch merges, using
      `resolve_inline_or_experiment`. One-tool follow-up.
- [ ] 15.2 A per-caller ephemeral plot channel — the prerequisite for inline `include_plots` and
      for giving the five legacy plot tools any inline path at all.
- [ ] 15.3 Declare memory limits on bloommcp and langchain-agent in `docker-compose.prod.yml`. No
      service declares one today, so an OOM is resolved by the host killer and may take out the
      database.
- [ ] 15.4 Wire FastMCP's rate-limiting middleware and/or a Caddy request-body cap on the bloommcp
      route. Neither exists.
- [ ] 15.5 Set a per-tool timeout at registration — tools are registered bare, so FastMCP's
      timeout path is never taken.
- [ ] 15.6 Fix `BLOOM_PLOTS_URL`: the configured `/plots` path has no Caddy route and 404s.
- [ ] 15.7 Add auth to `langchain/server.py`'s `/plots` static mount, which is reachable
      unauthenticated from the public ingress.
- [ ] 15.8 Consider whether the opt-in table returns should escape formula-prefixed cells. Low
      severity as scoped — the tools echo a caller's own data back to that same caller, and the
      field description and connect guide now say so — but PR 2's `return_trimmed_csv` adds a
      second echo-back surface, and a third would be the point to decide once rather than
      re-disclose per tool.

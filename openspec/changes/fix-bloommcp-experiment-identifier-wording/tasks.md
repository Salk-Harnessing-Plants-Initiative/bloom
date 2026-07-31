> **Commit plan** — text/docs/param-naming only, no dependency change. Every commit ends
> with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`:
> 1. `docs(#552): openspec proposal — experiment identifier wording` — the
>    `openspec/changes/…` artifacts (run `openspec validate --strict` locally).
> 2. `fix(#552): reword CSV-filename wording to experiment identifier` — all of §1-§4
>    below, plus the test updates in §5 (same commit — the assertions pin the exact text
>    the rest of the commit changes, so RED/GREEN belong together).

## 1. Path-traversal validation messages (behavior unchanged, text only)

- [x] 1.1 `bloommcp/src/bloom_mcp/tools/_qc_shared.py:73-76` —
      `_validate_experiment_name`'s raised message: reword "must be a bare CSV filename
      (no path separators)" → "must be a bare experiment identifier (no path
      separators)"; reword its `remedy` ("Pass a filename from
      list_available_experiments...") to "Pass an experiment identifier from
      list_available_experiments...". Do not touch the guard's condition
      (lines 64-69) — the accept/reject decision is unchanged.
- [x] 1.2 `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/_viz_shared.py` —
      `validate_filename`'s docstring first line (line 72, "Return an error message if
      ``filename`` is not a bare CSV filename") and its returned string (line 98, "filename
      must be a bare CSV filename (no path separators).") → same "experiment identifier"
      wording as 1.1. Do not touch the guard's condition (lines 92-97).

## 2. Tool-schema `Field(description=...)` text (9 sites, 8 files)

- [x] 2.1 `qc_clean.py:97`, `qc_inspect.py:90` — "CSV filename from
      list_available_experiments." → "Experiment identifier from
      list_available_experiments."
- [x] 2.2 `remove_outliers.py:99` — same pattern, keep the trailing "(must be cleaned)."
      qualifier.
- [x] 2.3 `clustering.py:75`, `pca_analysis.py:82`, `descriptive_stats.py:110`,
      `umap_analysis.py:100` — "Experiment (CSV filename) to {cluster,analyze} to.
      Must have a cleaned version..." → "Experiment identifier to {cluster,analyze}.
      Must have a cleaned version..." (keep the rest of each sentence — the cleaned-version
      requirement — unchanged).
- [x] 2.4 `cross_experiment_correlations.py:142,154` — `experiment_1`: "First experiment
      (CSV filename)." → "First experiment identifier."; `experiment_2`: "Second
      experiment (CSV filename)." → "Second experiment identifier." **Keep** the `'@'`/`'|'`
      restriction sentence verbatim. Reword the dotted-stem sentence to: "must not
      contain a '.' except as a single interior extension separator (e.g.
      'my_experiment.csv' is fine) — a leading dot, a trailing dot, or more than one '.'
      anywhere (e.g. '.hidden', 'a.', 'a.b.c') is rejected, since this tool's composite
      storage-key encoding cannot safely represent it." **Corrected post-PR-#571-review**:
      an earlier draft of this task used a simpler "must not contain more than one '.'
      character" framing, which an independent reviewer found is NOT equivalent to
      `_reject_dotted_stem`'s actual check for a leading- or trailing-only dot (e.g.
      `.hidden`, `a.` — both rejected by the guard, both wrongly allowed by the simpler
      framing). Verified the corrected wording above matches `_reject_dotted_stem`
      (lines 280-300) exactly, including that `_reject_path_unsafe_names` (line 460)
      already rejects a bare `.`/`..` before this guard ever runs — see design.md's Risks
      section for the full derivation and the reviewer's confirmation table.

## 3. Docstrings (`Args:` lines and related module/function text, 7 files, 8 sites)

- [x] 3.1 `sections/core/load_experiment_data.py:20` — "filename: CSV filename from
      list_available_experiments" → "filename: experiment identifier from
      list_available_experiments" (param name `filename` is unchanged — out of scope per
      proposal.md's Non-Goals; only the description text changes).
- [x] 3.2 `sections/core/list_available_experiments.py` — module docstring line 1 ("list
      experiment CSV files") and function docstring line 12 ("List all experiment CSV
      files available for analysis.") reworded to "experiments" rather than "CSV files";
      the hardcoded response line 36 ("To analyze an experiment, use its filename (e.g.,
      '{experiments[0].filename}')") reworded to "use its identifier" — **do not** rename
      the underlying `.filename` attribute on the experiment-summary object itself (a
      separate, deeper `ExperimentReader`-port-level rename, out of scope here); only the
      response prose changes.
- [x] 3.3 `experiment_utils.py:459` — "filename: CSV filename (e.g.,
      "alfalfa_gwas_wave2.csv")" → "filename: experiment identifier (e.g.,
      "alfalfa_gwas_wave2.csv" today; a database-backed identifier once
      data-access-roadmap.md Tier 2 lands)".
- [x] 3.4 `sections/phenotyping_segmentation/summarize_trait.py:17` — "CSV filename from
      list_available_experiments." → "Experiment identifier from
      list_available_experiments."
- [x] 3.5 The 5 plot tools' `filename:` docstring line — `plot_correlation_matrix.py:25`,
      `plot_heritability_bar.py:29`, `plot_trait_boxplots.py:32`,
      `plot_trait_histograms.py:31`, `plot_variance_decomposition.py:28` — "filename: CSV
      filename from list_available_experiments" → "filename: experiment identifier from
      list_available_experiments" (param name unchanged, matching 3.1's reasoning).

## 4. `list_existing_analyses.py` param rename

- [x] 4.1 Rename the `experiment_filename` parameter to `experiment` throughout
      `sections/core/list_existing_analyses.py` (signature, docstring `Args:` line — also
      reword "CSV filename" → "experiment identifier" there — cache lookup, the
      not-found branch's error/message strings, `store.list_runs(...)` call, and the
      response dict's key: `"experiment_filename"` → `"experiment"`).
- [x] 4.2 Update `bloommcp/tests/tools/test_qc_tools_discovery.py:102` — the assertion
      `payload["experiment_filename"] == _EXPERIMENT` → `payload["experiment"] ==
      _EXPERIMENT`. Confirm no other test in the suite asserts on the
      `experiment_filename` call kwarg or response key (grep first).

## 5. Update pinned test assertions

- [x] 5.1 `bloommcp/tests/tools/test_viz_tools.py:411` — `assert "bare CSV filename" in
      result` → `assert "bare experiment identifier" in result` (matching 1.2's new
      wording exactly).
- [x] 5.2 `bloommcp/tests/tools/test_viz_tools.py:426` — `assert "bare CSV filename" not
      in result` → `assert "bare experiment identifier" not in result`.

## 6. Verify

- [x] 6.1 `rg -n '"CSV filename"|CSV filename|bare CSV filename' bloommcp/src
      bloommcp/tests bloommcp/docs` — note the widened scope (includes `bloommcp/docs`,
      not just `src`/`tests`). Expect **zero** hits under `bloommcp/src`/`bloommcp/tests`
      (the `experiment_filename` identifier name in `manifest/analysis_dir.py`/
      `result_store/supabase_store.py` does NOT contain the literal string "CSV
      filename" and will not match this pattern at all — if it somehow does show up,
      that's a real miss, not an expected exclusion). Expect exactly two hits under
      `bloommcp/docs/data-access-roadmap.md` (its Tier 3 table row and its
      Reconciliation-log entry) — these are a known, tracked follow-up (§7.3), not a
      failure of this step.
- [x] 6.2 `cd bloommcp && uv run --frozen --extra test pytest tests/` — full suite green,
      including the reworded assertions and the `list_existing_analyses` rename.
      Confirm specifically that the untouched traversal/guard tests
      (`test_qc_inspect_tool.py::test_experiment_path_traversal_is_rejected`,
      `test_viz_tools.py`'s secret-file test) still pass unmodified — that's the actual
      evidence the "no behavior change" claim rests on, not just an incidental green run
      (see design.md's Risks section).
- [x] 6.3 `black --check` + `ruff check` over `bloommcp/`.
- [x] 6.4 `openspec validate fix-bloommcp-experiment-identifier-wording --strict`.
- [x] 6.5 **Immediately before merging this change's PR**, re-check live state:
      `gh issue view 551 --repo Salk-Harnessing-Plants-Initiative/bloom` and
      `gh pr view 557 --repo Salk-Harnessing-Plants-Initiative/bloom`. If #551/PR#557 has
      merged in the meantime, note it in this PR's description and flag §7.1's
      `storage-backends.md` follow-up as ready to pick up immediately (not just
      "eventually") — closes the race-condition gap noted in design.md's Risks section.
      Re-checked during PR #571 review response: #551 still OPEN, PR #557 still
      OPEN/unmerged — no change from the original check.

## 7. Follow-ups (out of this change's scope — tracked, not done here)

- [ ] 7.1 `bloommcp/docs/storage-backends.md`'s `supabase`-mode description update —
      deferred until `data-access-roadmap.md` Tier 2 (#551/#546) actually ships DB-direct
      reads; rewriting it now would describe behavior that doesn't exist yet (see
      design.md's Decisions). Re-scope issue #552 to track this specifically (do not
      close #552 via this change's PR — see proposal.md's Impact section).
- [ ] 7.2 Retiring dead CSV-from-bucket/local-disk raw-tier code and dropping
      `BLOOM_TRAITS_DIR` from boot validation — #476's scope (gated on Tier 2), not this
      change's. #477 (the adjacent `SLEAP_OUT_CSV` bind-mount ask) is already CLOSED via
      PR #495 — nothing further needed there.
- [ ] 7.3 `bloommcp/docs/data-access-roadmap.md`'s own "CSV filename" mentions (Tier 3
      table row, Reconciliation-log entry) will read as describing an already-fixed
      problem once this change ships. Not updated here because the Tier 3 row bundles
      more unfinished work than this change's slice (dead-code retirement,
      `storage-backends.md`, `BLOOM_TRAITS_DIR` boot validation) — a partial edit to just
      the wording clause would misrepresent the row's overall status. Revisit when Tier 3
      as a whole is closer to done, or sooner if it's causing confusion.
- [ ] 7.4 **Found in PR #571 review**: `_WIKI/BLOOMMCP/storage-workflow.md:195` still
      shows `list_existing_analyses(experiment_filename)` and references a stale
      `storage_tools.py` path (the function actually lives in
      `sections/core/list_existing_analyses.py`). Already stale before this change — this
      PR's rename adds one more reason that page needs a refresh, but `_WIKI/` was
      deliberately outside this change's verify-grep scope (`bloommcp/{src,tests,docs}`
      only, per §6.1) and is a separate, pre-existing doc-drift problem, not something
      this PR's diff caused. Left as a tracked follow-up. (A second stale `_WIKI/` doc
      reference, `adding-a-section-tool.md:31`, was also found in eberrigan's PR #571
      review round 2 — that one was a single self-contained code-example string, fixed
      directly rather than deferred: see §8.4.)

## 8. eberrigan's PR #571 review round 2 response (2026-07-31T19:26Z)

- [x] 8.1 `langchain/tools/context_tools.py`'s `CONTEXT_MCP` string — the only LLM-facing
      wording site outside `bloommcp/` (the system-prompt context payload the LangChain
      agent injects). Missed by this change's `bloommcp/{src,tests,docs}`-scoped grep
      (§6.1) since it lives under `langchain/`. Reworded "## CSV Experiment Files (MCP
      Tools)" / "Files ... are CSV files on the filesystem — NOT database tables" /
      "List CSV experiment files" off filename/CSV vocabulary, keeping the actionable
      "never use query_database for these" rule (still correct — Tier 2 does not make
      `query_database` valid for experiment data, it only changes what backs the MCP
      tools' own reads).
- [x] 8.2 `cross_experiment_correlations.py`'s three home-grown guards
      (`_reject_reserved_encoding_characters`, `_reject_self_correlation`,
      `_reject_dotted_stem`) had their `Field(description=...)` reworded by task 2.4 but
      not their own raised `message`/`remedy` strings — still said "filename stem,"
      "Rename the experiment file," and "Pass two distinct experiment filenames." Fixed
      all three to match the schema's "experiment identifier" vocabulary; the dotted-stem
      message now uses the same "single interior extension separator" framing as the
      schema description (2.4) instead of re-deriving a second, inconsistent phrasing.
      Guard conditions themselves untouched — text only, same as every other site in
      this change.
- [x] 8.3 `design.md`'s and this file's §6.2 citation of `test_qc_shared_validator.py` as
      evidence for `_validate_experiment_name`'s unchanged behavior was wrong — that file
      only exercises `_validate_trait_subset` and never calls `_validate_experiment_name`.
      Corrected both citations to point at
      `test_qc_inspect_tool.py::test_experiment_path_traversal_is_rejected` instead (the
      actual traversal-payload test for that guard). The underlying safety claim itself
      was never in question — only the citation.
- [x] 8.4 `_WIKI/BLOOMMCP/adding-a-section-tool.md:31` — a `SummarizeTraitParams` code
      example still showed `description="CSV filename from list_available_experiments."`
      verbatim. Fixed to match the actual reworded `summarize_trait.py:17` description
      (task 3.4). Unlike §7.4's `storage-workflow.md` (a compound staleness — wrong param
      name *and* a wrong module path, a bigger doc revamp than this change's slice), this
      was a single self-contained string with no other drift, so fixed directly rather
      than deferred.
- [x] 8.5 `openspec validate fix-bloommcp-experiment-identifier-wording --strict` was
      reported failing (Requirement 1's SHALL landing past the first physical line of a
      wrapped paragraph). Re-ran directly against openspec CLI v1.7.0 and it passes
      cleanly (`valid: true`, matching the same false-positive pattern already documented
      for PR #569 — v1.7.0 joins a requirement's full multi-line body before scanning for
      SHALL/MUST). Reflowed the paragraph to lead with SHALL anyway — defensive, zero-risk,
      and satisfies the reviewer's literal ask without depending on which openspec version
      a future reader's `npx` resolves.
- [x] 8.6 Added a test asserting the retired `experiment_filename=` kwarg is rejected by
      `list_existing_analyses` (no test previously exercised this — the rename's own
      completeness was unverified from the suite).
- [x] 8.7 Parametrized `test_dotted_stem_rejected` over the leading/trailing-dot edge
      cases this PR's own review round 1 derived (`.hidden`, `a.`) — previously only the
      multi-interior-dot case (`my.experiment.v2.csv`) was covered.
- [x] 8.8 Fixed the stale `payload["experiment_filename"]` assertion in
      `tests/integration/test_versioned_storage_phase_a.py:364` to `payload["experiment"]`.
      The module is still skipped at import (unrelated, pre-existing — it imports a
      function path that no longer exists on this branch) so this doesn't change CI's
      green/red status; fixed so the module isn't doubly stale if anyone re-enables it.

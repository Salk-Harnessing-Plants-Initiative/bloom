## 1. Reader: `latest` (outliers-preferring) vs `latest_qc` (qc-only) resolution

- [ ] 1.1 Write the failing unit tests first (red), directly against
      `bloommcp/src/bloom_mcp/experiment_utils._resolve_versioned_cleaned` using the existing
      hand-built-manifest harness (`fake_supabase_storage` + `write_manifest`/`VersionEntry`,
      precedent: `test_resolve_versioned_cleaned_via_local_list_prefix_fallback` in
      `bloommcp/tests/test_storage_backend.py:624-676`). Six cases, each with a concrete
      expected `(path content / label, error)`:
      (a) only `qc` has a `latest` entry, `version="latest"` → resolves it (today's behavior,
          unchanged);
      (b) only `outliers` has a `latest` entry, `version="latest"` → resolves it;
      (c) both have entries → `version="latest"` resolves **`outliers`**, regardless of which
          manifest's entry has the later `created_at` (this is the actual #420 repro — assert it
          with the `qc` entry's `created_at` deliberately set *later* than the `outliers`
          entry's, to prove this is not a recency comparison);
      (d) both have entries → `version="latest_qc"` resolves **`qc`** specifically, ignoring
          `outliers`;
      (e) only `qc` has a `latest` entry, `version="latest_qc"` → resolves it (same as (a) —
          confirms `latest_qc` isn't a no-op alias that silently means something else when
          `outliers` is absent);
      (f) the `outliers`-class manifest fails schema validation (`ManifestSchemaError`) while the
          `qc`-class manifest is valid, `version="latest"` → the schema error propagates
          immediately (the exact `f"manifest schema error for '{stem}': {e}"` shape), it is
          **not** swallowed and does **not** fall through to the `qc` class.
- [ ] 1.2 In `bloommcp/src/bloom_mcp/experiment_utils.py`, add
      `_CLEANED_TOOL_CLASSES_BY_PRIORITY = ("qc", "outliers")` (lowest to highest priority) next
      to `CLEANED_CSV_NAME`, and rewrite `_resolve_versioned_cleaned` to branch on `version`:
      `"latest"` → iterate `_CLEANED_TOOL_CLASSES_BY_PRIORITY` in *reverse* (highest priority
      first: `outliers`, then `qc`), returning the first class whose `get_version("latest")`
      resolves, with the returned label qualified as `f"{tool_class}_{entry.id}_cleaned"`;
      `"latest_qc"` → resolve the `qc` class only, using **today's exact unqualified**
      `f"{entry.id}_cleaned"` label (byte-for-byte the old `"latest"` behavior); explicit
      `"v<N>"` → unchanged, `qc` class only, unqualified label. A `ManifestSchemaError` on any
      checked class propagates immediately in every branch (task 1.1f).
- [ ] 1.3 Confirm all six cases from 1.1 pass; confirm no other existing test asserting the
      pre-fix single-class `_resolve_versioned_cleaned` behavior for plain `version="latest"`
      (no `outliers` class involved) regressed. Update the `version` docstring on
      `load_experiment_data` (`experiment_utils.py:442-471`, currently documents only
      `"latest"`/`"raw"`/`"v<N>"`) and on the `ExperimentReader.load_experiment` port itself
      (`data_access/ports.py`) to also document `"latest_qc"`. `SupabaseReader` and `LocalReader`
      both forward `version` straight through with no adapter-level change needed (verified:
      `supabase_reader.py:85-87` and `local_reader.py:71-77` both pass `version` unchanged into
      `_resolve_versioned_cleaned` / `load_experiment_data`).
- [ ] 1.4 In `bloommcp/src/bloom_mcp/data_access/fake_reader.py`, treat `version="latest_qc"` as
      an alias for `"latest"` in `FakeReader.load_experiment` (`fake_reader.py:73,81`) — `FakeReader`
      has no tool-class model at all, so this keeps every existing `FakeReader`-seeded
      `remove_outliers` unit test passing once its call site changes (task 2.4).

## 2. `remove_outliers`: dedicated tool class + explicit `latest_qc` read

- [ ] 2.1 In `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/remove_outliers.py`, change
      `_TOOL_CLASS = "qc"` to `_TOOL_CLASS = "outliers"`.
- [ ] 2.2 In the same file, change the input read at (currently) line 274 from
      `reader.load_experiment(params.experiment, require_clean=True)` to
      `reader.load_experiment(params.experiment, require_clean=True, version="latest_qc")` — this
      tool always wants the current plain clean, never a prior trim of its own.
- [ ] 2.3 Rewrite the module docstring's composition/caveat paragraphs (currently lines ~10-37):
      remove the "shares tool class qc" / "order-dependent" language; describe persistence under
      the dedicated `outliers` class, the `version="latest_qc"` input read, and the disclosed
      trade-off that a plain `qc_clean` re-run does not become "latest" for other consumers until
      a fresh `remove_outliers` run is made (design.md Decision 4). Correct the docstring's
      existing `"outliers"` (plural)-as-retired-class claim to the verified `"outlier"`
      (singular).
- [ ] 2.4 Update every `test_remove_outliers_tool.py` assertion that hardcodes `tool_class ==
      "qc"` for a `remove_outliers`-committed run to `"outliers"` (at minimum the golden,
      provenance, plots, and error-path tests — grep the file for `"qc"` string literals
      alongside `store.get_run`/`store.create_run`/`_ports.store()` calls; do not rely on a
      pre-counted line list going stale as the file changes). **Delete**
      `test_qc_class_rerun_reverts_latest_cleaned_order_dependence` — its premise (remove_outliers
      shares the `qc` class) is invalidated by 2.1, and it is documented as a
      characterization-not-a-fix test, not a behavior to preserve. Land 2.1, 2.2, and this task
      in the **same commit** — flipping `_TOOL_CLASS` without also updating these assertions
      leaves the suite red.

## 3. Discovery / registry consistency

- [ ] 3.1 Add `"outliers"` to `TOOL_CLASSES` in
      `bloommcp/src/bloom_mcp/sections/core/list_existing_analyses.py` so trimmed runs remain
      visible in that tool's output.
- [ ] 3.2 Add `"outliers"` to `CANONICAL_TOOL_CLASSES` in `bloommcp/src/bloom_mcp/manifest/__init__.py`
      for consistency with 3.1 (not currently enforced by any runtime check, but the two lists
      are documented elsewhere as a matched "reserved tool classes" pair).

## 4. Regression coverage for the actual hazard

- [ ] 4.1 In `bloommcp/tests/tools/test_remove_outliers_tool.py`, alongside the existing
      `test_trimmed_run_composes_into_require_clean_read`, add a characterization test driving
      the real `SupabaseReader` + `SupabaseResultStore` over the shared in-memory object-store
      double (same harness — `FakeReader`/`FakeResultStore` can't exercise cross-manifest
      composition, per that file's own precedent) through: `qc_clean` (commits `qc` v1) →
      `remove_outliers` (reads via `latest_qc` → trims `qc` v1 → commits `outliers` v1) →
      `qc_clean` again (commits `qc` v2, un-trimmed) →
      `SupabaseReader().load_experiment(..., require_clean=True)`. Assert **concretely**: the
      resolved frame's row count equals the trimmed count (not `qc` v2's un-trimmed count), and
      `resolved.source == "outliers_v1_cleaned"` (or whatever the actual entry id is — assert the
      exact string, not a substring/prose description).
- [ ] 4.2 Add the inverse sanity check: `qc_clean` → `qc_clean` again (no trim ever run) still
      resolves the newer `qc` version via `version="latest"` as today — confirms the fix doesn't
      regress the no-trim path (`resolved.source == "qc_v2_cleaned"`).
- [ ] 4.3 Add a third case proving the fix's own safety property: `qc_clean` (v1) →
      `remove_outliers` (trims v1 → `outliers` v1) → `qc_clean` again (v2) → `remove_outliers`
      **again** — assert its `n_input_samples` matches `qc` v2's row count (proving it read the
      *fresh* clean via `latest_qc`, not its own stale prior trim), and that the resulting
      `outliers` v2 becomes `version="latest"` afterward.
- [ ] 4.4 File a follow-up GitHub issue (not implemented in this change) for a one-time, read-only
      audit script identifying experiments where a `remove_outliers` run was already silently
      superseded under the old shared-`qc` scheme (`VersionEntry.tool == "remove_outliers"` in a
      `qc` manifest that is not that manifest's current `latest`) — link it from this change's
      proposal.md once filed.

## 5. Update affected docs/scripts referencing the old shared-class assumption

- [ ] 5.1 In `bloommcp/tests/smoke/live_persistence_smoke.py`, update `RO_TOOL_CLASS` (currently
      `"qc"`) to `"outliers"` and check every use site (the leg asserting a second
      `remove_outliers` commit "advances latest ... without clobbering the first") still makes
      sense against the dedicated class.
- [ ] 5.2 Update `bloommcp/docs/local-validation.md`'s `remove_outliers` narrative, which
      currently documents "persists under the same qc tool class, so its trimmed `_cleaned.csv`
      becomes the newest cleaned version" — no longer accurate.

## 6. Correct the still-open `add-bloommcp-remove-outliers-tool` proposal

- [x] 6.1 In `openspec/changes/add-bloommcp-remove-outliers-tool/design.md`, annotate Decision 1
      (and its "Alternative considered" sub-bullet) and the Open Questions entry to record that
      the dedicated-class alternative was adopted by this change (#420), with a forward
      reference. Done as an annotation, not a rewrite — historical record of what PR #400
      shipped.
- [x] 6.2 In `openspec/changes/add-bloommcp-remove-outliers-tool/specs/bloommcp-remove-outliers-tool/spec.md`,
      add a superseded-by note on the "Remove Outliers Persists a Versioned Trimmed Cleaned Run
      and Returns Links" requirement pointing to this change's `bloommcp-experiment-read` delta,
      rather than rewriting the requirement text in place.
- [x] 6.3 Spot-check `openspec/changes/add-bloommcp-remove-outliers-tool/proposal.md` for the
      same stale `tool_class="qc"` / order-dependence claims and annotate them; `tasks.md` in
      that change is a completed implementation checklist and is left untouched.

## 7. Validate

- [ ] 7.1 `npx -y -p @fission-ai/openspec openspec validate fix-bloommcp-remove-outliers-tool-class --strict`
      passes.
- [ ] 7.2 Full `bloommcp` unit test suite passes (`uv run --extra test pytest`), including the
      new/updated tests above.

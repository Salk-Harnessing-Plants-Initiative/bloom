## Context

`remove_outliers.py` and `qc_clean.py` both write a trait CSV under the same logical filename
`CLEANED_CSV_NAME = "_cleaned.csv"`. The reader treats "latest cleaned" as a single pointer:

- `experiment_utils._resolve_versioned_cleaned(o_dir, stem, version)` builds exactly one
  `AnalysisDir(OUTPUT_PREFIX, f"{stem}.csv", "qc")`, calls `.get_version(version)`, and — for
  `version="latest"` — returns whatever `manifest.latest` points at in that one manifest.
  `experiment_utils.py:351-439`
- `AnalysisDir.get_version("latest")` resolves `manifest.latest`, a single `str` field bumped by
  every `commit()` for that `(experiment, tool_class)` pair, with no notion of "latest across
  classes." `manifest/analysis_dir.py:56-67`
- `SupabaseReader.load_experiment` and `experiment_utils.load_experiment_data` (used by
  `LocalReader`) both call `_resolve_versioned_cleaned` directly for the cleaned tier — one
  helper, two adapters, so fixing it here fixes both. `data_access/supabase_reader.py:85-87`,
  `experiment_utils.py:478-481`
- `remove_outliers.py` deliberately writes into the `qc` class's manifest (not a class of its
  own) specifically so a trimmed run becomes that manifest's new `latest` — see its own
  docstring and `add-bloommcp-remove-outliers-tool/design.md` Decision 1. This is the root
  cause: a later `qc_clean` run bumps the *same* manifest's `latest` right back to an
  un-trimmed run.
- `manifest.CANONICAL_TOOL_CLASSES` and `list_existing_analyses.TOOL_CLASSES` both already list
  `"outlier"` (**singular**) — not as a class available for new use, but explicitly as a
  **retired** class kept only so `list_existing_analyses` can still read back history from the
  pre-`devendor-bloommcp-analysis` `run_outlier_workflow` tool, which persisted under
  `_TOOL_CLASS = "outlier"` (confirmed by inspecting that tool's last commit before deletion,
  `76b2721^:bloommcp/src/bloom_mcp/tools/workflows/outlier.py:30`). Writing new `remove_outliers`
  runs into that same singular slot would resurrect a supposedly-retired class with
  incompatible-schema history mixed in.
- `remove_outliers.py`'s own read of its trimming **input** goes through the exact same
  `require_clean=True` / `version="latest"` path every other consumer uses
  (`remove_outliers.py:274`). This matters: whatever "latest" means for `pca_analysis` also
  determines what `remove_outliers` re-trims on its *next* invocation.

## An earlier draft of this design was wrong — recorded here because the mistake is instructive

The first draft of this fix proposed comparing `VersionEntry.created_at` (a lexically-sortable
UTC-seconds timestamp, confirmed at `contract/provenance.py:96-100`) across the `qc` and
`outliers` manifests and resolving "latest" as whichever class's `latest` entry has the newer
timestamp. **This does not fix the bug.** Walk the issue's own repro under that rule:

1. `qc_clean` → `qc` v1 @ `T1`
2. `remove_outliers` → `outliers` v1 @ `T2` (`T2 > T1`)
3. `qc_clean` again ("to re-tune thresholds") → `qc` v2 @ `T3` (`T3 > T2`, because it is
   committed *after* the trim, by construction of the scenario)
4. Compare: `qc` v2 (`T3`) vs `outliers` v1 (`T2`) → `T3` is greater → **resolves `qc` v2, the
   un-trimmed frame** — the exact silent revert this change exists to prevent.

Recency-across-classes is not a fix for "whichever class was touched most recently wins"; it is
the same failure mode moved up one layer, and it would have shipped a proposal that does not
solve the problem it names, protected from an obvious in-repo unit test only by an editorial
oversight. A fixed-priority rule ("`outliers` always wins over `qc` whenever an `outliers`
version exists at all, full stop, no timestamp comparison") looks tempting as an alternative and
does fix the four-step repro above — but it introduces a **worse** regression: once any
`outliers` version exists for an experiment, a **subsequent, unrelated `qc_clean` re-run becomes
permanently invisible** to every `require_clean=True` consumer, *including `remove_outliers`
itself* — its own next invocation would re-trim the stale old trim (or the stale old clean)
forever, never picking up the fresh data, with no way to reach the new clean short of manually
pinning an explicit version. That is a more entrenched silent-data problem than the one in the
issue, not a fix for it.

The design below resolves this by recognizing that **two different questions were being
conflated under one `version="latest"` string**: "give me the best analysis-ready data for this
experiment" (should prefer a trim, once one exists) and "give me the current plain-cleaned
table, regardless of any trim" (what `remove_outliers` itself needs as *input*, so a fresh
`qc_clean` is never hidden from the one tool whose job is to trim it).

## Goals / Non-Goals

- **Goal:** `remove_outliers` persists under a tool class that is neither the shared `qc` class
  (removes the revert hazard) nor the retired singular `outlier` class (removes the
  legacy-collision hazard) — `"outliers"`, plural, verified unclaimed.
- **Goal:** a `require_clean=True` consumer other than `remove_outliers` itself (`pca_analysis`,
  `umap_analysis`, `clustering`, `descriptive_stats`, `cross_experiment_correlations`) resolves
  "latest cleaned" preferring an `outliers`-class version over a `qc`-class version whenever the
  `outliers` class has any committed version at all, so a later plain `qc_clean` cannot silently
  revert an existing trim for **those** consumers.
- **Goal:** `remove_outliers`'s own read of its trimming input is **not** subject to that
  preference — it always resolves the current `qc`-class latest specifically, so a fresh
  `qc_clean` re-run is immediately visible to the *next* `remove_outliers` call, and trims never
  compound (running `remove_outliers` twice without an intervening `qc_clean` now re-trims the
  same `qc` clean rather than trimming its own prior trim — see Decision 3).
- **Non-Goal:** changing `version="raw"` or explicit `version="v<N>"` resolution — both stay
  scoped to the `qc` class exactly as today (no shipped caller passes an explicit cleaned-tier
  version pin — verified by grep — so this is currently unreachable, not merely deferred).
- **Non-Goal:** a general N-way "cleaned-producing tool class registry" or a lineage/staleness
  detector comparing `based_on_version` against the current `qc` latest. Two classes with a
  fixed, disclosed priority is the full scope of today's need — see the Open Question below for
  the one place a reviewer may want the heavier alternative instead.
- **Non-Goal:** migrating or relabeling any already-persisted `remove_outliers` run committed
  under the old `qc` class.

## Decisions

- **Decision 1: `_TOOL_CLASS = "outliers"` for `remove_outliers`, replacing `"qc"`.** Plural,
  distinct from the retired singular `"outlier"`. Both `list_existing_analyses.TOOL_CLASSES` and
  `manifest.CANONICAL_TOOL_CLASSES` gain `"outliers"` so a trimmed run stays visible in
  `list_existing_analyses` output and the two "reserved tool class" registries (already
  independently drifted from each other today — `CANONICAL_TOOL_CLASSES` alone carries
  `heritability`/`anova`) don't silently drift further apart on this specific addition.
- **Decision 2: introduce a distinct `version="latest_qc"` value, resolved as "the `qc` class's
  own `latest`, ignoring `outliers` entirely."** `_resolve_versioned_cleaned` now branches on
  four cases instead of two: `"raw"` (handled by the caller, unchanged), `"latest"` (new:
  cross-class, `outliers`-preferring — see Decision 3), `"latest_qc"` (new: `qc`-class only,
  byte-for-byte the *old* `"latest"` behavior), and an explicit `"v<N>"` (unchanged: `qc`-class
  only). `remove_outliers.py` changes its own input read from `version="latest"` (implicit
  default) to the explicit `version="latest_qc"` — it wants "the current clean," never "the
  current trim." Every other consumer keeps calling with the default `"latest"` and gains the
  trim-preferring behavior automatically, with no call-site change.
- **Decision 3: for `version="latest"`, prefer the `outliers` class's `latest` entry over the
  `qc` class's whenever `outliers` has *any* committed version — fixed priority, not a
  timestamp comparison.** No `created_at` comparison, no clock-skew exposure, no same-second
  tie-break to reason about (all three problems the earlier, wrong draft introduced are moot
  once resolution isn't recency-based). Combined with Decision 2, this is what makes the fix
  actually work: `remove_outliers`'s own `"latest_qc"` read is untouched by this preference, so
  a fresh `qc_clean` is never hidden from the tool whose job is to trim it, while every *other*
  consumer sees the trim once one exists.
- **Decision 4 (proposed, flagged for explicit reviewer sign-off — not asserted as settled):
  once any `outliers` version exists for an experiment, a subsequent plain `qc_clean` re-run does
  not become "latest" for `require_clean=True` consumers on its own.** It only becomes reachable
  once a fresh `remove_outliers` run (which — per Decision 2 — always reads the current `qc`
  latest, ignoring the stale trim) commits a new `outliers` version on top of it. This is **one
  reasonable reading** of "give `remove_outliers` a dedicated class... structurally, not a
  warning" from the #420 review thread — the alternative literal reading ("prefer whichever
  manifest was touched most recently") is proven unfixable by the repro trace above, so some
  departure from the literal wording is unavoidable; this is the specific departure chosen, not
  the only possible one. **This is the central open judgment call in this design** (Open
  Questions) — a lineage-aware alternative (comparing `based_on_version` against the current `qc`
  latest, falling back to the plain clean when the trim is provably stale) was considered and
  rejected because `_resolve_versioned_cleaned`'s `(path, label, error)` return has no channel for
  a non-fatal advisory, so that alternative's fallback would be just as silent as the original
  bug. The trade-off this decision accepts is asymmetric with, not equivalent to, the original
  bug: it is auditable (the stale `outliers` entry's own `based_on_version` still names the `qc`
  version it was trimmed from) and recoverable by a known action (re-run `remove_outliers`),
  where the original bug was neither.
- **Decision 5: qualify the resolved label with the tool class ONLY when `"latest"` actually
  resolves via the `outliers` class — never when it resolves via `qc`.** `remove_outliers`'s
  `provenance.based_on_version` is always the `qc`-class label it read via `version="latest_qc"`
  (e.g. `"v3_cleaned"`), so no new ambiguity is introduced there. For the cross-class
  `version="latest"` path: when it resolves via `outliers` (the new, previously-impossible case),
  the label is qualified as `f"outliers_{entry.id}_cleaned"` so a human or log reading
  `frame.source` can tell it came from a trim, not a plain clean. **When `"latest"` resolves via
  `qc`** (which — since `outliers` always wins whenever it has any entry, per Decision 3 — means
  exactly the case where no `outliers` version exists yet), **the label stays exactly
  `f"{entry.id}_cleaned"`, unqualified, byte-for-byte what `version="latest"` already returns
  today.** This is deliberately asymmetric,
  not an oversight: it means the overwhelmingly common case (no trim has ever been made for this
  experiment) has **zero observable change** to `ExperimentFrame.source` or any `based_on_version`
  a downstream tool persists from it — `pca_analysis.py`, `clustering.py`, `descriptive_stats.py`,
  `umap_analysis.py`, and `cross_experiment_correlations.py` all currently do
  `based_on_version = frame.source` (verified — each stamps this today), so qualifying the label
  unconditionally would silently change the persisted provenance format for every future run on
  every experiment, not just ones with a trim, and would also break the existing
  `test_storage_backend.py::test_resolve_versioned_cleaned_via_local_list_prefix_fallback`, which
  asserts the exact unqualified `"v1_cleaned"` label for a qc-only manifest. Confining the new
  format to the genuinely new case (an `outliers` version exists and wins) avoids both.
  `version="latest_qc"` and explicit `"v<N>"` are qc-class-only by construction and always use
  the unqualified format.
- **Decision 6: `FakeReader` treats `"latest_qc"` as an alias for `"latest"`.** `FakeReader` has
  no notion of tool classes at all (`fake_reader.py:66-94` — one flat `{version_id: df}` map per
  experiment); it cannot model the `qc`/`outliers` split and was never meant to (the original
  `add-bloommcp-remove-outliers-tool` design already established that the fakes path proves
  per-port contracts while the real-adapter path over the shared in-memory object store proves
  cross-port/cross-manifest composition). Adding one `elif version == "latest_qc": version =
  "latest"` line keeps every existing `FakeReader`-based `remove_outliers` unit test passing
  unchanged after the call-site switch in Decision 2.
- **Decision 7: no schema change, no error-handling change for `ManifestSchemaError`.** A schema
  error on *either* checked class's manifest still propagates immediately as today's error
  string — it is never treated as a soft miss that falls through to the other class, matching
  the existing principle that a corrupt manifest is a hard failure, not something to silently
  route around. This must hold in **both** iteration positions: a schema error on `outliers`
  (checked first) must propagate without falling through to `qc`, **and** a schema error on `qc`
  (checked second, reached only when `outliers` resolves to no entry) must propagate too — both
  are tested (tasks.md 1.1f/1.1g).
- **Decision 8: the shared `version="latest"` semantics change is disclosed on the consumer
  tools, not only in this design doc.** `pca_analysis`, `umap_analysis`, `clustering`,
  `descriptive_stats`, and `cross_experiment_correlations` all call `require_clean=True` /
  `version="latest"` (the default) with no call-site change — but what "latest" *means* changes
  for any experiment that has ever been trimmed (Decision 4). An agent or bench scientist reading
  a tool's parameter description has no way to know this from the code alone. Each tool's
  docstring/param description SHALL gain one sentence: "latest" resolves the most recent outlier
  trim when one exists, not merely the most recent clean.

## Post-review addendum

A 5-lens subagent review of the implemented PR (#576) found two real gaps beyond the design
above, both fixed on the same branch:

- **Decision 7 understated the hard-error scope.** As shipped, only a `ManifestSchemaError` was
  a hard error for `version="latest"`; every *other* failure to resolve an entry that exists
  (missing `_cleaned.csv` output key, an unlocatable version directory, a failed download) was
  still treated as a soft miss, falling through to the next class. That reproduces this change's
  own target hazard — a storage hiccup on the `outliers` class silently resolving `qc`'s
  otherwise-valid entry instead, with no error — just triggered by infrastructure instead of a
  `qc_clean` re-run. Fixed: once `_resolve_one_class` finds an entry at all (`entry is not None`),
  *any* subsequent failure to resolve it is now a hard error, unconditionally — only "no entry
  exists" remains a soft miss. Regression test:
  `test_latest_outliers_entry_exists_but_download_fails_is_a_hard_error`.
- **Decision 4's disclosed trade-off had no observability.** The design accepted that a stale
  trim (based on a superseded `qc` clean) keeps resolving as "latest cleaned" until a fresh
  `remove_outliers` run supersedes it — but nothing surfaced that staleness at read time, only a
  manual manifest diff could. Added `_log_if_trim_is_stale`: a non-blocking, best-effort
  `logger.info` (never raises, never affects resolution) that fires when the resolved `outliers`
  entry's `based_on_version` no longer matches the current `qc`-class latest. This is the "cheap
  middle option" the Open Questions section below already named but hadn't implemented.
- **Unrelated ripple-audit gap:** `qc_inspect.py`'s stated contract ("reads the raw frame, no
  `require_clean`") never actually passed `version="raw"` — a pre-existing bug (order-dependent
  on whether `qc_clean` had ever run) that this change's own `outliers`-preferring resolution made
  materially worse (deterministic once any trim exists, not merely order-dependent). Fixed by
  passing `version="raw"` explicitly; the 5-consumer docstring audit (Decision 8) should have
  caught this 6th call site and didn't.
- **Minor:** `QC_TOOL_CLASS` / `OUTLIERS_TOOL_CLASS` constants added to `experiment_utils.py` and
  referenced by `qc_clean.py`, `remove_outliers.py`, `list_existing_analyses.TOOL_CLASSES`, and
  `manifest.CANONICAL_TOOL_CLASSES` instead of each re-typing the literal string — the reviewer's
  point that this duplication is exactly the drift class #420 itself is about.

## Risks / Trade-offs

- **Decision 4's trade-off is real and is the central judgment call in this design** (see Open
  Questions) — a bench scientist who re-runs `qc_clean` to fix an upstream data problem, without
  remembering to also re-run `remove_outliers`, will keep getting the old trim (now visibly
  stale relative to the new clean, but not flagged as such) rather than the new clean. This is
  accepted because the alternative (silently falling back to the new clean) is the bug this
  change exists to close.
- **`remove_outliers`'s own re-trim semantics change slightly**: running it twice in a row with
  no intervening `qc_clean` today (pre-fix) re-trims its own prior trim (both live in the shared
  `qc` class, so "latest" already means "whatever committed last," trim included); after this
  fix it always re-trims the current `qc` clean instead. This is a disclosed behavior
  improvement (eliminates unbounded trim-of-a-trim compounding), not a neutral side effect —
  called out explicitly in case an existing test asserts the old compounding behavior.
- **One extra Storage read per `version="latest"` cleaned-tier resolution** (up to two manifests
  read instead of one) — negligible against the existing per-call round trips.
- **Pre-existing `qc`-class-persisted trims are not retroactively protected** and are not
  scanned for by this change (Non-Goal) — anyone currently relying on a trim from before this
  ships may already be silently analyzing un-trimmed data today, undetected. Filed as follow-up
  [#585](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/585), covering
  **both**: (a) a one-time audit script for the historical case
  (`VersionEntry.tool == "remove_outliers"` entries in a `qc` manifest that are not that
  manifest's current `latest`), and (b) surfacing the read-time staleness log (Post-review
  addendum above) ambiently — e.g. a hint in `list_existing_analyses` output — rather than only
  when something actually reads the experiment. Neither is implemented in this change; a pure
  detect-and-log addition (not a resolution-behavior change) would not reintroduce the
  silent-fallback problem Decision 4 rejects, so it remains a reasonable follow-up rather than a
  rejected idea.
- **Two independent version-number sequences per experiment** (`qc`'s own `v1, v2, ...` and
  `outliers`' own separate `v1, v2, ...`) is the same shape already accepted for every other
  tool-class pair (`stats`, `clustering`, `pca`, ...); not new to this change.
- **Cross-proposal sequencing hazard on `bloommcp-experiment-read`.** The still-unarchived
  `add-bloommcp-local-experiment-reader` change carries its own full `MODIFIED Requirements` for
  "ExperimentReader Port" and "SupabaseReader Adapter" against this same capability
  (`openspec/changes/add-bloommcp-local-experiment-reader/specs/bloommcp-experiment-read/spec.md:82-143`),
  written against the **old** single-`qc`-class wording (it adds a `LocalReader Adapter`
  requirement but had to restate these two requirements verbatim, per OpenSpec's "MODIFIED
  requirements include full text" rule). If that change is archived **after** this one without
  first rebasing its restated text onto this change's `outliers`-preferring / `latest_qc`
  wording, the archive step would silently revert `openspec/specs/bloommcp-experiment-read/spec.md`
  back to describing only the `qc` class — resurrecting the exact "silently stale spec" failure
  mode this proposal is built to close for `remove_outliers` itself. Flagged for whoever archives
  either change second to rebase against the other's already-archived text; not fixed here since
  it is not this proposal's file to edit.

## Migration Plan

Additive/behavioral only. No manifest schema change, no rewrite of existing manifests. Rollback
= revert the `_TOOL_CLASS` constant, the `version="latest_qc"` call-site change, and the
`_resolve_versioned_cleaned` branch; any `outliers`-class runs committed in the interim remain
readable (they simply stop being resolved as "latest cleaned" until re-applied).

## Open Questions

- **Decision 4 (fixed priority) vs. a lineage-aware alternative (settle at review).** Fixed
  priority means a trim, once made, is authoritative until explicitly redone — simple,
  structurally eliminates the silent-revert hazard, but means a scientist must remember to
  re-trim after any deliberate re-clean. A lineage-aware alternative (compare the `outliers`
  latest's `based_on_version` against the current `qc` latest; fall back to the plain clean only
  when the trim is provably based on a superseded clean) would auto-recover a genuinely-fresh
  clean without requiring a manual re-trim, at the cost of reintroducing a silent fallback to
  un-trimmed data in exactly the case the issue complains about (a `qc_clean` re-run the user
  didn't intend to also invalidate the trim looks identical, at this layer, to one they did).
  Recommend fixed priority (Decision 4) unless a reviewer has a concrete workflow where the
  lineage-aware behavior is needed.
- **A cheaper middle option exists and is deliberately not implemented here: fixed-priority
  resolution (no behavior change) plus a non-blocking staleness signal** (log a message, or
  surface a hint in `list_existing_analyses`, when an `outliers` entry's `based_on_version`
  no longer matches the current `qc`-class latest) rather than either resolving differently or
  saying nothing. Unlike the issue's rejected "cheap interim" (a warning **instead of** the
  structural fix) or the rejected lineage-aware fallback (a warning **substituting for** a
  changed, silently-different return value), this would be a warning **in addition to** the
  unchanged fixed-priority resolution — it doesn't reintroduce a silent fallback because nothing
  about what is *returned* changes. Left to the follow-up issue (Risks) rather than this change,
  to keep this proposal's surface area to the resolution fix itself.

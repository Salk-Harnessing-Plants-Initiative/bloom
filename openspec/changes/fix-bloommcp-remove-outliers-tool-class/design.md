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
- **Decision 4 (disclosed trade-off, not a bug): once any `outliers` version exists for an
  experiment, a subsequent plain `qc_clean` re-run does not become "latest" for
  `require_clean=True` consumers on its own.** It only becomes reachable once a fresh
  `remove_outliers` run (which — per Decision 2 — always reads the current `qc` latest,
  ignoring the stale trim) commits a new `outliers` version on top of it. This is the intended
  reading of "give `remove_outliers` a dedicated class... structurally, not a warning" from the
  #420 review thread: once an experiment has opted into outlier-trimming, the trimmed pipeline
  output is the analysis-ready tier until the trim itself is redone, not until any clean happens
  to be redone. **This is the one place a reviewer might want the heavier alternative instead
  (lineage-aware staleness detection comparing `based_on_version` against the current `qc`
  latest, falling back to the plain clean when the trim is provably stale) — flagged in Open
  Questions**, but that alternative reintroduces a form of the original silent-revert (a
  genuinely-superseded trim would fall back to an un-trimmed frame with no distinguishable
  signal, since `_resolve_versioned_cleaned`'s `(path, label, error)` return has no channel for
  a non-fatal advisory) and was rejected here for the same reason the issue's own "cheap interim"
  was rejected: it re-admits a silent, easy-to-miss path back to un-trimmed data.
- **Decision 5: `remove_outliers`' `provenance.based_on_version` stays unambiguous under this
  design** — it is always the `qc`-class label the tool actually read via `version="latest_qc"`
  (e.g. `"v3_cleaned"`, always a `qc`-class version), never an `outliers`-class label, so no new
  cross-catalog ambiguity is introduced in the one place this codebase currently stamps
  `based_on_version`. The **cross-class `"latest"` resolution's own returned `source` label**
  (read by non-`remove_outliers` consumers, e.g. `pca_analysis`'s `frame.source`) is qualified
  with the winning tool class — `f"{tool_class}_{entry.id}_cleaned"` (e.g. `"outliers_v2_cleaned"`
  vs `"qc_v3_cleaned"`) — specifically for this new multi-class path, so a human or log reading
  `frame.source` can tell which manifest it came from. The existing `"latest_qc"` and explicit
  `"v<N>"` paths keep today's unqualified `f"{entry.id}_cleaned"` format unchanged (still
  unambiguous — single class).
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
  route around.

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
  ships may already be silently analyzing un-trimmed data today, undetected. A follow-up issue
  for a one-time audit script (`VersionEntry.tool == "remove_outliers"` entries in a `qc`
  manifest that are not that manifest's current `latest`) is recommended but out of scope here.
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

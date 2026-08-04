## Context

`fix-bloommcp-remove-outliers-fit-gate` (#419) and `fix-bloommcp-remove-outliers-tool-class`
(#420) already established the shape and the primitives this change builds on:

- `remove_outliers.py` persists exclusively under `experiment_utils.OUTLIERS_TOOL_CLASS`
  (`"outliers"`) since #420 — a dedicated `AnalysisDir(output_root, f"{stem}.csv", "outliers")`
  manifest, never `"qc"`, for every commit made *after* #420 shipped.
  `manifest.py`
- Every commit's `VersionEntry.output_keys` maps a logical output name (`"_cleaned.csv"`,
  `"outlier_report.json"`) to its full storage object key (confirmed against
  `result_store/_artifacts.py::hash_outputs`, which is what actually populates it at commit time:
  `output_keys[name] = key_for(rel)`, `key_for` being `AnalysisDir.key`). `read_json(key)`
  (`supabase_client.py`) reads that key's content directly — the same primitive `#585`'s script
  already uses for manifests, applicable unchanged to any other JSON object, including
  `outlier_report.json`.
- `remove_outliers.py` currently defines `_UNTRUSTWORTHY_FIT` (a frozenset of
  `goodness_of_fit.fit_quality` values gating persistence — `#419`) and `_fit_is_trustworthy`
  (deriving the tri-state flag from a `goodness_of_fit` dict) as **private, module-local**
  primitives — reasonable when only that tool needed them, but this audit script needs the exact
  same classification, and duplicating the frozenset literal is precisely the drift risk a `#419`
  PR review already flagged (and fixed) for that tool's other duplicated constants
  (`contamination=0.1`). `_REPORT_NAME = "outlier_report.json"` is similarly private.
- `#585`'s `audit_stale_outlier_trims.py` is the direct structural precedent: a pure,
  unit-testable `scan_...() -> dict` function (never imports `write_manifest`/`upload_file`),
  a thin `run()`/`main()` that also persists the report as a timestamped, self-describing JSON
  object under `bloommcp_output/_audit_reports/`, and per-stem error isolation (a corrupt/unreadable
  manifest for one experiment is recorded in `errors` and does not abort the rest of the scan).
  This change follows that shape exactly, differing only in *what* it checks per manifest.
- **A real scope subtlety `#585`'s own script does not need to handle, but this one does:**
  `#585` scans `qc_<stem>` manifests (the shared class both tools wrote to *before* #420). This
  change's natural target is `outliers_<stem>` manifests (the class `remove_outliers` writes to
  *after* #420) — but an experiment trimmed *before* #420 shipped, whose `qc_<stem>` manifest's
  `latest` is still that pre-#420 `remove_outliers` entry (never superseded by any `qc_clean` *or*
  post-#420 `remove_outliers` run since), has **no `outliers_<stem>` manifest at all** — scanning
  only `outliers_` prefixes would silently miss it, even though that trim is exactly as currently
  canonical as any post-#420 one. See Decision 2 for how this is scoped.

## Goals / Non-Goals

- **Goal:** a developer can run a single script against a target environment's real bucket and get
  back every experiment whose *currently canonical* `remove_outliers` trim (the one a
  `require_clean=True` read resolves today) rests on an untrustworthy mahalanobis fit — enough
  detail (stem, run ref, `fit_quality`, `n_outliers`) to decide whether to re-trim.
- **Goal:** the untrustworthy-fit classification has exactly one implementation, shared by
  `remove_outliers`'s live gate and this audit, so the two can never silently disagree on what
  counts as untrustworthy (the same "one source of truth" reasoning `#585` already established
  for `trim_staleness`).
- **Goal:** read-only against every experiment manifest; the only write is the audit's own
  timestamped report object, in the same `bloommcp_output/_audit_reports/` prefix `#585` already
  established (not a new prefix), with a distinct filename pattern so the two scripts' reports
  never collide.
- **Non-Goal:** re-running `remove_outliers`, deleting/superseding a flagged run, or any other
  mutation — report-only, matching `#585`'s precedent and `#593`'s own ask.
- **Non-Goal:** scanning `qc_<stem>` manifests for the pre-#420-legacy-never-superseded edge case
  described in Context — see Decision 2 for the reasoning and the narrower follow-up this leaves
  open rather than silently dropping.
- **Non-Goal:** any change to `remove_outliers`'s runtime gate behavior, or to `#420`'s
  `qc`/`outliers` resolution priority — this change only adds a detection/reporting script plus
  the shared-primitive extraction Decision 1 describes.

## Decisions

- **Decision 1: promote `UNTRUSTWORTHY_FIT_QUALITIES`, `fit_is_trustworthy`, and
  `OUTLIER_REPORT_NAME` from `remove_outliers.py` (private) to `experiment_utils.py` (public),
  and have `remove_outliers.py` import them instead of defining its own copies.** Same
  "extract to `experiment_utils` so a script and the tool share one source of truth" pattern
  `#585` used for `trim_staleness` (extracted from `remove_outliers`'s sibling concern,
  `_log_if_trim_is_stale`). `remove_outliers.py`'s own behavior is byte-for-byte unchanged — this
  is a pure refactor (move + import), verified by re-running `test_remove_outliers_tool.py`
  unmodified. Naming drops the leading underscore (matching `experiment_utils`'s existing public
  constants: `QC_TOOL_CLASS`, `OUTLIERS_TOOL_CLASS`, `REMOVE_OUTLIERS_TOOL_NAME`, `CLEANED_CSV_NAME`)
  since these are now cross-module, intentionally-shared symbols, not module-private
  implementation detail.
- **Decision 2: scan `outliers_<stem>` manifests only; explicitly do not also scan `qc_<stem>`
  manifests for the never-superseded pre-#420 edge case.** Reasoning, not silent scope-narrowing:
  1. By the time `#419` shipped, `#420` had already been live in every environment for a period —
     any experiment still under active analysis has very likely picked up at least one
     post-#420 `remove_outliers` run (which *does* create an `outliers_<stem>` manifest,
     making it visible to this scan) or a `qc_clean` re-run (which supersedes the stale trim
     entirely, making it a non-issue). The uncovered case — an experiment trimmed pre-#420 and
     never touched by any tool since — is a shrinking, already-stale-by-inactivity historical
     residue, not the common case this audit needs to prioritize.
  2. `#585`'s own script already visits every `qc_<stem>` manifest and already computes
     "is the current `latest` `remove_outliers`-authored" as an intermediate fact (it's the
     *negation* of `#585`'s own hit condition — `#585` reports when `latest.tool != "remove_outliers"`,
     this change's uncovered case is exactly when `latest.tool == "remove_outliers"` **and** no
     `outliers_<stem>` manifest exists at all). Extending `#585`'s script to *also* check
     `goodness_of_fit` on that already-identified subset is a smaller, more surgical follow-up
     than duplicating its entire `qc_`-scanning/error-handling machinery in a second script here.
  3. Duplicating both scripts' scan loops (near-identical `list_prefix` + per-stem
     `AnalysisDir.read_manifest()` + error-isolation logic) into one combined "scan every
     manifest, cross-reference every rule" script was considered and rejected for this change:
     it would blur two independently-reviewable, independently-testable audits (staleness vs.
     fit-quality) into one, and neither `#585` nor `#419` proposed that scope. Left as an Open
     Question rather than decided unilaterally here.
- **Decision 3: read the flagged fit's `outlier_report.json` via the `latest` `VersionEntry`'s
  `output_keys`, not by reconstructing a path.** `output_keys["outlier_report.json"]` (populated
  at commit time by `result_store/_artifacts.py::hash_outputs`) is the authoritative, exact
  storage key — reconstructing `f"{prefix}{version_dir}/outlier_report.json"` independently would
  duplicate that logic and could silently drift if the version-dir naming convention ever changes.
  A `VersionEntry` missing that key (a legacy/malformed commit) is recorded as a per-stem error,
  not a crash, matching `#585`'s resilience posture.
- **Decision 4: a hit requires exactly `latest_entry.tool == REMOVE_OUTLIERS_TOOL_NAME` (always
  true in practice for an `outliers_<stem>` manifest, but asserted rather than assumed) and
  `fit_is_trustworthy(report.get("goodness_of_fit")) is False`.** `None` (an `isolation_forest`
  trim — no chi-squared assumption to fail) and `True` (an acceptable-or-better mahalanobis fit)
  are both correctly *not* hits — the exact same tri-state semantics `remove_outliers`'s live gate
  uses, via the same shared `fit_is_trustworthy` (Decision 1), so the two can't drift apart on
  what counts as untrustworthy.
- **Decision 5: report fields mirror `#585`'s hit shape where the concepts overlap, add
  fit-specific fields where they don't.** Per hit: `stem`, `run_ref` (the flagged `VersionEntry.id`),
  `created_at`, `based_on_version`, `fit_quality` (the exact `goodness_of_fit.fit_quality` string),
  `n_outliers`/`n_input_samples`/`n_output_samples` (from the same report, for a human to judge
  severity without a second lookup), and `method` (always `"mahalanobis"` in practice — the gate
  this reports on pre-dates the fit-gate, so no `isolation_forest` entry can ever be a hit, but
  recorded for self-description rather than assumed by the reader).
- **Decision 6: persist the report under the same `bloommcp_output/_audit_reports/` prefix `#585`
  established, with a distinct filename prefix (`untrustworthy_outlier_fits_...`) and its own
  random suffix** — same collision-avoidance reasoning `#585`'s `write_report` already documents
  (two runs completing in the same wall-clock second must not clobber each other), applied to a
  sibling report rather than inventing a second reports directory.

## Risks / Trade-offs

- **The Decision 2 scope gap is real, not hypothetical**, though expected to be small and
  shrinking (see Decision 2's reasoning). Not fixed here; left as an explicit, disclosed Non-Goal
  and Open Question rather than silently absent from either this design or the issue it closes.
- **Same single-active-backend limitation `#585` already inherits** (open issue #573: `supabase`/
  `local` backends each own a physically disjoint manifest) — this script reads through the same
  `AnalysisDir`/storage-backend seam, so a backend-split experiment's history could give an
  incomplete answer. Not this change's scope to fix.
- **No cross-referencing of downstream consumers.** A hit names the `outliers`-class run itself,
  not which `pca_analysis`/`clustering`/etc. runs actually consumed it — same scope limit `#585`
  accepted for its own audit.
- **Refactor risk (Decision 1) is low but non-zero**: moving `_UNTRUSTWORTHY_FIT`/
  `_fit_is_trustworthy` out of `remove_outliers.py` touches a file that just shipped a
  security/behavior-sensitive gate (`#419`). Mitigated by re-running `test_remove_outliers_tool.py`
  unmodified after the move (a pure import-path change should not require touching any of that
  file's ~51 existing tests) and running the full suite before calling this done.

## Migration Plan

Additive/behavioral-neutral. `remove_outliers.py`'s public contract (its Pydantic models, its
raised errors, its persisted manifest shape) is unchanged — Decision 1 only changes where two
constants and one function are *defined*, not what they compute. Rollback = revert the
`experiment_utils.py` extraction and the new script/test files; no manifest or schema migration
either way.

**Commit/PR plan (per a git-workflow review pass on this proposal):** unlike `#585` — which fused
its analogous primitive-extraction refactor with its new script in one commit — this change ships
the Decision 1 refactor (tasks.md section 1) as its own commit, verified green in isolation,
*before* the audit script's commit (sections 2-3) lands on top. Elevated care versus `#585`'s
precedent because `remove_outliers.py` is the file `#419` just shipped a behavior-sensitive gate
in; bisectability matters more here than it did for `#585`'s refactor target. One PR into
`staging` (matching `#585`'s actual PR strategy — splitting this small, additive, no-behavior-
change capability across two PRs would be disproportionate overhead), reviewed as ≥2 commits
within it.

## Open Questions

- **Decision 2's scope gap** — extend `#585`'s own script to also check `goodness_of_fit` on the
  `qc_`-manifest-latest-is-`remove_outliers` subset it already identifies (recommended, smallest
  surgical addition to existing machinery), build a small dedicated third script for just that
  edge case, or accept the gap as-is given its expected shrinking size? Recommend the first
  option as a follow-up, not decided here.

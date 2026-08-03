## Context

`fix-bloommcp-remove-outliers-tool-class` (#420) already established the shape this change builds
on:

- `experiment_utils.QC_TOOL_CLASS` / `OUTLIERS_TOOL_CLASS` are the single-sourced literals for the
  two cleaned-producing tool classes.
- `experiment_utils._log_if_trim_is_stale(stem, outliers_label)` already computes the staleness
  comparison this change reuses: it reads the `outliers`-class `latest` `VersionEntry` and the
  `qc`-class `latest` `VersionEntry` (both via
  `bloom_mcp.manifest.AnalysisDir(...).get_version("latest")`), and considers the trim stale when
  `outliers_entry.based_on_version != f"{qc_entry.id}_cleaned"`. It returns silently (no log) when
  `outliers_entry` is `None`. It is purely observational — it never raises, never affects
  `_resolve_versioned_cleaned`'s return value.
- `AnalysisDir(output_root, experiment_filename, tool_class)` wraps one `(experiment, tool_class)`
  manifest: `.read_manifest()` returns the `Manifest` (or `None`), `.list_versions()` returns every
  `VersionEntry` sorted by `created_at`, `.get_version(version_id)` resolves one entry (or
  `"latest"` via `manifest.latest`). This reads the physical manifest directly through
  `bloom_mcp.storage_backend.active_backend()` (`supabase` or `local`) — the same seam
  `_log_if_trim_is_stale` and `_resolve_versioned_cleaned` already use, and a **different**,
  lower-level seam than the `ExperimentReader`/`ResultStore` ports every tool consumer is normally
  required to depend on exclusively (see Decision 2).
- `supabase_client.list_prefix(prefix)` lists basenames directly under a prefix — e.g.
  `list_prefix("bloommcp_output/")` returns entries like `qc_<stem>`, `outliers_<stem>`,
  `stats_<stem>`, etc. — the only way to enumerate *every* experiment that has ever had a `qc`
  manifest written, as opposed to `ExperimentReader.list_experiments()`, which enumerates
  currently-known raw inputs and would miss an experiment whose raw CSV was since removed but
  whose manifest history still exists.
- `list_existing_analyses` (the always-available core discovery tool) already aggregates a
  per-experiment response by walking every tool class through `ResultStore.list_runs`, catching
  and collecting exceptions per class into an `errors` list rather than failing the whole call.
  `list_existing_analyses.py` is one of the ten files
  `bloommcp/tests/test_persistence_import_guard.py` specifically asserts depends only on the
  ports, never on `supabase`/`AnalysisDir`/storage primitives directly (see Decision 2).
- `StoredRun` (the `ResultStore.list_runs` return type) does **not** carry `based_on_version` — it
  is a backend-neutral projection that intentionally exposes only what every tool-class run has in
  common. Staleness detection is a `qc`/`outliers`-pair-specific concept, not a generic per-run
  field, so it stays out of `StoredRun` and is computed as a separate top-level field instead.
- **Open issue #419** (same repo, same tool) recommends re-running `remove_outliers` with a
  different `method`/threshold after a poor fit — i.e., `remove_outliers` can legitimately commit
  more than once for the same experiment with no intervening `qc_clean`. Any pre-#420 audit logic
  must not confuse this pattern (a later, still-current trim) with an actual silent revert (Decision
  4 was revised specifically because of this).
- **Open issue #573** (filed 2026-07-31, same author) documents that `supabase`/`local` storage
  backends each own a **physically disjoint manifest** — an experiment's history can, in principle,
  be split across backends if the active backend changed mid-lifecycle. `trim_staleness` and the
  audit script both read through the same single-active-backend seam `_resolve_versioned_cleaned`
  already does, so they inherit that same limitation (see Risks).

## Goals / Non-Goals

- **Goal:** a developer can run a single script against a target environment's real bucket and get
  back the set of experiments whose `remove_outliers` trim was silently reverted before #420
  shipped, with no risk of mutating anything, and without flagging legitimate multi-attempt
  `remove_outliers` iteration (#419's pattern) as a false positive.
- **Goal:** an agent or scientist calling `list_existing_analyses` can see whether an experiment's
  current trim is stale without a separate, heavier `require_clean=True` read.
- **Goal:** the staleness comparison itself has exactly one implementation, reused by the existing
  read-time log and the new discovery field, so the two call sites cannot silently disagree on what
  "stale" means.
- **Non-Goal:** changing what `version="latest"` resolves to, or adding a fallback/auto-recovery
  path. Both are `fix-bloommcp-remove-outliers-tool-class` Decision 4's already-settled scope.
- **Non-Goal:** a generic "run provenance" field on `StoredRun`/`ResultStore`. Only the two
  cleaned-producing classes have a meaningful staleness relationship to each other today; forcing
  that concept into the backend-neutral `StoredRun` shape used by every tool class would be
  premature generalization for a two-class special case.
- **Non-Goal:** detecting or correcting a backend-split manifest history (#573). This change
  assumes an audited/queried experiment's `qc`/`outliers` manifests were written by a single
  storage backend throughout their lifecycle — the same assumption every existing
  `_resolve_versioned_cleaned` caller already makes today. If #573 is resolved with a
  cross-backend read, `trim_staleness` and the audit script should be revisited, but that is
  out of scope here.
- **Non-Goal:** fully closing #585's "which of my experiments currently have a stale trim, in
  bulk" framing. `trim_is_stale` reduces the *per-experiment* read cost (you still have to name
  the experiment), and the audit script answers the *historical, pre-#420* question in bulk — but
  neither gives a bulk answer to "which currently-known experiments have a stale trim right now."
  A periodic/on-demand bulk scan for the *ongoing* case (one of #585's own candidate follow-ups)
  is not built here; see Open Questions.

## Decisions

- **Decision 1: extract `trim_staleness(stem) -> Optional[bool]` out of `_log_if_trim_is_stale`,
  rather than writing a second, independent comparison for `list_existing_analyses`.** Both
  surfaces need to answer the same question — "is a trim's `based_on_version` behind the current
  `qc` latest" — and #420 already established the single-sourced-constant convention specifically
  to prevent this drift class.
  - **Return contract, precisely:** `None` when no `outliers`-class version exists at all
    (nothing to assess — distinguishes "never trimmed" from "trimmed and current" for
    `list_existing_analyses`, which needs that distinction to decide whether to include the field
    at all — Decision 3). `True` when an `outliers`-class version exists but the `qc`-class
    manifest has **no** `latest` entry at all — this is a **behavior change** from
    `_log_if_trim_is_stale`'s current silent-return-on-either-`None` shape, made deliberately: an
    `outliers` entry with no live `qc` baseline to compare against is a more concerning state than
    "current," not an equivalent-to-"nothing to see" state, and today's silent handling of this
    corner (untested, unreached by either existing test) was itself an oversight worth correcting
    while this code is already being touched. `True`/`False` otherwise, via the existing
    `based_on_version` comparison.
  - **`_log_if_trim_is_stale` becomes a thin wrapper**: log if and only if `trim_staleness(stem)`
    is `True`. Its two existing tests only assert a fixed log substring, not the interpolated
    `based_on_version` values from the old inline implementation — the refactor keeps rendering
    those values by having `trim_staleness` return `(bool, based_on_version, current_qc_label)` as
    a small `NamedTuple` when it has both entries to compare (or `None` when there is nothing to
    assess), rather than a bare `Optional[bool]`, so no logged information is lost. Callers that
    only care about the boolean use `.is_stale`; `list_existing_analyses` (Decision 3) uses the
    same field.
  - `trim_staleness` itself does not swallow exceptions — a manifest read failure propagates.
    `_log_if_trim_is_stale` wraps it in its own existing try/except-and-swallow (unchanged,
    purely observational, must never raise). `list_existing_analyses` wraps it differently
    (Decision 3).
- **Decision 2: `list_existing_analyses.py` gains a narrow, disclosed, transitive dependency on
  the manifest/storage layer via `experiment_utils.trim_staleness` — not a new port method.**
  `test_persistence_import_guard.py` asserts this file imports neither `supabase` nor
  `AnalysisDir` directly, and it still won't (it imports `trim_staleness` from
  `experiment_utils`, which is where the direct `AnalysisDir` read already lives, exactly as
  `_log_if_trim_is_stale` does today from inside `_resolve_versioned_cleaned` itself). This is a
  disclosed exception, not a loophole exploited silently: `trim_is_stale` is an ambient,
  advisory-only hint layered on top of the tool's existing `ResultStore`-backed `analyses` payload
  (which is unaffected and still goes through the port), not a replacement data path — routing it
  through a hypothetical new `ResultStore.trim_staleness()` port method would mean growing the
  port's surface for a single niche, two-class-specific, observation-only signal that every other
  adapter (`FakeResultStore`) would have to stub meaninglessly. `list_existing_analyses.py`'s
  import of `trim_staleness` gets a one-line comment recording this reasoning, and
  `test_persistence_import_guard.py`'s module docstring gets one sentence noting the narrow,
  transitive exception, so a future reader of the guard isn't misled into thinking its AST scan
  is a complete dependency boundary for this one file.
- **Decision 3: `trim_is_stale` is present in `list_existing_analyses`'s response only when
  `trim_staleness(stem)` successfully returns non-`None`; it is never emitted as `false` for an
  untrimmed experiment, and its *absence* is not a safety claim.** The call is wrapped in a
  `try/except` that appends `f"trim_staleness: {exc}"` to the response's existing `errors` list on
  failure and leaves `trim_is_stale` unset — mirroring the per-tool-class `list_runs` error
  handling already in this function. This means `trim_is_stale`'s absence is ambiguous between
  "never trimmed" and "the check failed" by construction (the same ambiguity `by_tool_class`
  already has for "no runs" vs. "listing failed" in this same function today) — **this field is
  advisory only**, a convenience signal to reduce unnecessary `require_clean=True` reads, never a
  substitute for the authoritative, hard-erroring resolution path
  (`_resolve_versioned_cleaned`/`_resolve_one_class`) that remains the only place a real data
  hazard is guaranteed to surface loudly. The tool's docstring says this explicitly: "if
  `trim_is_stale` is absent, check `errors` for a `trim_staleness` entry before concluding the
  experiment was never trimmed."
- **Decision 4 (revised from an earlier, incorrect draft — kept here because the mistake is
  instructive): a historical-audit hit is per-*manifest*, not per-non-latest-entry, and requires
  the manifest's *current* `latest` entry to be a plain clean, not another trim.**
  - **The first draft of this design defined a hit as "any `VersionEntry` with
    `tool == "remove_outliers"` whose `id` is not the manifest's `latest`."** This is wrong: per
    #419, a scientist can legitimately re-run `remove_outliers` more than once (e.g., switching
    method after a poor fit) with no intervening `qc_clean`. Walk that sequence:
    `qc_clean` (v1) → `remove_outliers` (v2, mahalanobis, poor fit) → `remove_outliers` (v3,
    isolation_forest) — `manifest.latest == "v3"`. Under the first-draft rule, v2 (`tool ==
    "remove_outliers"`, `id != latest`) is reported as a "silently superseded trim" — but nothing
    was reverted; v3 is a valid, current, intentional re-trim. Shipping that rule would make the
    audit's own output untrustworthy on exactly the workflow #419 recommends.
  - **The corrected rule:** for each `qc`-class manifest, a hit exists **iff** (a) at least one
    `VersionEntry` with `tool == "remove_outliers"` exists anywhere in the manifest's version
    history, **and** (b) the entry `manifest.latest` currently points at has `tool !=
    "remove_outliers"` (i.e., a subsequent `qc_clean` is what's currently "latest" for this
    experiment under the old shared-class resolution — a real revert). This correctly excludes
    the `remove_outliers`-then-`remove_outliers` case above (latest is itself a `remove_outliers`
    entry — not a hit) and correctly includes the actual bug case
    (`qc_clean` → `remove_outliers` → `qc_clean` again — latest is a `qc_clean` entry, and a
    `remove_outliers` entry exists earlier in history — a hit).
  - **The reported hit names the most recently-committed `remove_outliers` entry** (by
    `created_at`, in case more than one exists in history) as "the trim that was superseded,"
    alongside its `id` and `created_at`, and the manifest's current `latest` entry's `id`,
    `tool`, and `created_at` (added `tool`/second `created_at` vs. the original draft, so a
    reader can see at a glance what superseded the trim and when).
  - Because #420 changed `remove_outliers`'s `_TOOL_CLASS` to `"outliers"`, no `remove_outliers`
    entry can be newly written into a `qc` manifest going forward — every hit this script can ever
    find is, by construction, a pre-#420 artifact; the script needs no separate "before #420
    shipped" timestamp cutoff.
- **Decision 5: the audit script is a standalone script under `bloommcp/scripts/`, not a new MCP
  tool**, for the reasons in the original draft (one-time, read-only, whole-bucket scope, no
  natural `experiment` parameter, developer-run not agent-invoked) — see the "Enumeration
  approach" and "`bloommcp/scripts/` precedent" reasoning retained from the prior revision of this
  document.
  - **Enumeration:** `list_prefix("bloommcp_output/")` once, filter to names starting with
    `f"{QC_TOOL_CLASS}_"` (not the bare literal `"qc_"` — single-sourced against the same constant
    `qc_clean.py`/`remove_outliers.py`/the registries already import, closing the exact
    literal-drift hole #420/#585 are both about), and derive each stem by stripping that prefix.
  - **Per-manifest failure handling is deliberately broader than `_resolve_one_class`'s.**
    `_resolve_one_class` (the live resolution path) only catches `ManifestSchemaError` and treats
    every other failure as a hard error — correct there, because silently routing around a
    partially-broken entry during a real read risks resurfacing #420's own hazard. This script is
    a best-effort, read-only, one-shot forensic sweep over a potentially large bucket, where the
    opposite failure mode (one corrupt manifest aborting the entire scan and reporting nothing)
    is worse: it catches **any** exception per-stem (malformed JSON via `json.JSONDecodeError`,
    `ManifestSchemaError`, a pydantic `ValidationError`, or a storage/network error from
    `list_prefix`/`read_json` itself), records `{stem, error}` in the report's `errors` list, and
    continues to the next stem.
  - **The top-level enumeration call (`list_prefix("bloommcp_output/")` itself) is not
    caught.** If the environment is unreachable or misconfigured, there is nothing to report at
    all — the script should fail loudly and exit non-zero with a clear message, not silently
    report an empty, misleadingly "successful" scan. This is different from a single bad manifest
    (which the script can route around) and is reflected in `main()`'s exit code (Decision 6).
  - **The report is written to a timestamped JSON file, not only printed to stdout.** #420's own
    Risks section frames this exact hazard in terms of research "already published/in progress"
    riding on possibly-un-trimmed data — a finding worth persisting as a durable artifact
    (`bloommcp_output/_audit_reports/stale_outlier_trims_<UTC-timestamp>.json`, written via the
    same `active_backend()` write path other tools already use) rather than relying on whoever
    runs the script to remember to redirect stdout. Writing this one report file is not a
    violation of the "never mutates a manifest" guarantee (Requirement scope: no `manifest.json`
    for any `qc_<stem>`/`outliers_<stem>` experiment prefix is touched; the audit report is a new,
    separate object under its own prefix) — this distinction is made explicit in the spec
    scenario text and is the one exception to "read-only" the script has, called out by name so it
    isn't mistaken for scope creep on the "never mutates storage" guarantee.
- **Decision 6: `main()`'s exit code distinguishes "couldn't run" from "ran and found things."**
  Exit `1` only if the top-level enumeration itself fails (nothing was scanned). Exit `0`
  whenever the scan completes — including when it reports hits and/or per-stem errors — since a
  non-empty hit list or a partially-unreadable manifest are the script's normal, successful
  output, not a script failure.

## Risks / Trade-offs

- **`trim_is_stale` adds one extra pair of manifest reads to `list_existing_analyses`** — bounded
  by the same 30-second response cache already in place, and negligible against that tool's
  existing per-tool-class `list_runs` calls.
- **The historical audit's hit list is not itself a remediation**, and does not identify which
  *downstream* runs (a specific `pca_analysis`, `clustering`, etc.) consumed the un-trimmed data
  during the affected window — only that the `qc`/`outliers` manifest pair for an experiment shows
  the pattern. Cross-referencing every other tool class's `based_on_version` provenance per hit
  would make the report far more actionable but is a heavier, unscoped lift (left as an Open
  Question) — #585 itself frames the ask as "report-only," and this change already extends that
  ask slightly (the timestamped report file) without extending it further into cross-tool-class
  forensics.
- **The audit script and `trim_staleness` inherit `_resolve_versioned_cleaned`'s single-
  active-backend assumption (#573)** — see Non-Goals. A backend-split experiment could produce an
  incomplete or misleading answer from either surface; this is a pre-existing limitation of the
  seam being reused, not introduced by this change, but is newly exercised at bucket-wide scale by
  the audit script (a single-experiment read hitting this limitation is one wrong answer; a
  bucket-wide scan hitting it silently produces one wrong answer per affected experiment with no
  aggregate signal that it happened).
- **`trim_is_stale` cannot be computed for an experiment whose raw input has been deleted** —
  `list_existing_analyses`'s existing "known experiment" guard (`known = {exp.filename for exp in
  _ports.reader().list_experiments()}`) rejects the call before `trim_staleness` is ever reached,
  for exactly the class of experiment the audit script's `list_prefix`-based enumeration was
  chosen specifically to still reach (see Context). This is a disclosed, intentional division of
  labor, not an oversight: the ongoing per-experiment field only ever needs to work for
  currently-live experiments (an agent names one it can currently see); the audit script's job is
  precisely to cover the manifest-only-survivor case the ongoing field cannot reach.
- **Access scope:** the audit script enumerates every experiment in the bucket under the shared
  `bloom_agent` service-role credential. This introduces no new access-boundary crossing — Bloom's
  Storage model grants `bloom_agent` bucket-wide `SELECT` today (see the `agent_read_objects`
  policy backing `bloommcp-data`), and `bloommcp/src/bloom_mcp/identity.py` documents that every
  existing tool already reads under this same shared, bucket-wide role; there is no
  per-scientist/per-experiment RLS boundary for a bucket-wide scan to bypass.
- **Cross-proposal sequencing hazard, same class #420's own design.md flagged for a different
  pair of changes.** `fix-bloommcp-remove-outliers-tool-class` (#420/PR #576) is merged into code
  but its OpenSpec change is **not yet archived** — the canonical
  `openspec/specs/bloommcp-experiment-read/spec.md` still describes only the pre-#420,
  single-`qc`-class resolution order. This change's spec (a new, separate capability) does not
  modify that canonical spec and so does not conflict with it directly, but this change's own
  text (design.md, this file) freely references `OUTLIERS_TOOL_CLASS`, `latest_qc`, and
  `_log_if_trim_is_stale` as already-real concepts, which the canonical spec tree doesn't yet
  reflect. Flagged for whoever archives changes in this area: archive
  `fix-bloommcp-remove-outliers-tool-class` before or together with this change, not after, so the
  canonical spec tree never has this change's capability referencing a resolution order the
  canonical `bloommcp-experiment-read` spec doesn't yet describe.

## Migration Plan

Additive only. No manifest schema change, no data mutation to any `qc_<stem>`/`outliers_<stem>`
manifest, no existing resolution behavior changed. The one write this change introduces (the
audit's own timestamped report file, Decision 5) is a new object under its own prefix, never an
overwrite of existing state. Rollback = revert the three additions independently (the
`trim_staleness` extraction, the `list_existing_analyses` field, the new script) — none depends on
the others being present.

## Open Questions

- **Should the audit's hit list be auto-filed as tracking issues, or cross-reference other tool
  classes' `based_on_version` chains to name affected downstream runs?** Left as a durable JSON
  report only (Decision 5) for this change — both are real, valuable extensions, but neither is
  needed to answer #585's literal ask, and the right shape for either is clearer after the audit
  has actually been run once against a real environment and the hit volume/pattern is known.
- **A bulk, on-demand "which currently-known experiments have a stale trim right now" scan**
  (distinct from the historical audit, which only looks for the pre-#420 pattern) was one of
  #585's own candidate follow-ups and is explicitly not built here (Non-Goals) — `trim_is_stale`
  only answers this per-experiment. If this is needed, the natural implementation would iterate
  `ExperimentReader.list_experiments()` and call `trim_staleness` per experiment, sharing
  `list_existing_analyses`'s own logic — but building that iteration/reporting surface is a
  separate, unscoped follow-up.

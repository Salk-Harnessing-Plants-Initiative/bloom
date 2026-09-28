## ADDED Requirements

### Requirement: Id-list filters stay within the gateway's URL limit
Every PostgREST `in.(…)` filter the route issues SHALL be split into batches so that no batch's rendered id list exceeds a character budget of 4000. The budget matches bloomctl's `ID_FILTER_BUDGET_CHARS`, which sits below the ~5.4 KB `414 URI Too Long` ceiling measured in bloom#674. Results from all batches SHALL be merged before use.

This covers:
- the `scan_ids` existence check against `cyl_scans_extended`;
- when the dedup preview runs, both of its filters: scan ids against `cyl_scan_traits`, and source ids against `cyl_trait_sources`.

The number of filter requests SHALL depend only on the rendered length of the id lists, never on issuing one query per scan.

#### Scenario: A large scan_ids request is existence-checked in batches
- **WHEN** `target_level = "scan_ids"` with 3000 existing four-digit scan ids
- **THEN** the existence check is issued as more than one `cyl_scans_extended` filter request, none of whose id lists exceeds 4000 characters
- **AND** the route proceeds as if all 3000 were found in one query

#### Scenario: A missing id is still detected across batches
- **WHEN** a 3000-id `scan_ids` request contains one id that doesn't exist, and that id falls in the last batch
- **THEN** the route responds `404` naming that id, and writes no rows

#### Scenario: A large experiment's dedup preview is batched
- **WHEN** an experiment-level request with non-empty `params` enumerates 2500 scans
- **THEN** every `cyl_scan_traits` and `cyl_trait_sources` filter request stays within the 4000-character budget
- **AND** `reused_count` equals what a single unbatched query would have produced

## MODIFIED Requirements

### Requirement: Dedup preview is informational only — it never withholds a scan from enqueue

The route SHALL compute `compute_param_hash` (from the pinned `sleap-roots-contracts` package)
**once**, over the request's own `params` object exactly as supplied in the request body — this
route does not call `sleap_roots_contracts.resolve_params()` or otherwise derive `params` from a
scan's Bloom metadata; the caller is responsible for supplying whatever `params` value it wants
hashed, stored, and compared — and SHALL check whether that hash string equals
the already-stored `metadata->'params'->>'param_hash'` value on **any** of a scan's existing
`cyl_trait_sources` rows (reached via `cyl_scan_traits.scan_id → source_id → cyl_trait_sources.id` —
a scan can have more than one source, from successive computations). The route MUST NOT re-hash a
stored source's whole `metadata` column and compare that to the request hash — `metadata` is the
full Provenance envelope (code shas, container digests, workflow ids, etc., which differ on every
run even for identical params), so hashing it whole would essentially never match. Only the
pre-computed `param_hash` field already embedded in `metadata` at write-back time is compared,
string-to-string. **This check MUST cover all of a scan's sources, not only the most recent one.**
This deliberately does not reuse the `cyl-trait-read` capability's `is_latest` = `max(source_id)`
rule: that rule answers "which source should a read default to display," not "has this exact param
combination ever been computed for this scan" — those are different questions, and reusing
`is_latest` here would under-count `reused_count`, since an older source with matching params could
be missed whenever a newer source with *different* params also exists for the same scan (e.g. a scan
computed at `age=14` then later at `age=21`; a new request for `age=14` again must still be recognized
as a match even though `age=21` is now the latest source). A scan with at least one matching-params
source contributes to `reused_count` **exactly once**, regardless of how many of its sources match.
The check across all enumerated scans MUST NOT be a per-scan query loop: it SHALL filter
`cyl_scan_traits` by `scan_id IN (...)` and `cyl_trait_sources` by `id IN (...)`, each split only as
required by the "Id-list filters stay within the gateway's URL limit" requirement, so the number of
queries grows with the rendered length of the id lists and never with one query per scan.
**When the request's `params` is the empty object `{}`, the route SHALL skip both preview queries and
use `reused_count = 0`:** stored `param_hash` values are computed over resolved params
(`species`/`mode`/`age`), so `compute_param_hash({})` cannot equal any of them, and skipping the
queries yields the identical result.
**This check is informational only: it MUST NOT change the scan's initial `status` (always written as
`'queued'`) and MUST NOT exclude the scan from batching or enqueue.** Every enumerated scan is always
written and enqueued regardless of dedup-preview outcome — the real GPU-avoidance decision is made
cluster-side by the predict loop's existing per-scan skip-if-done check, which (unlike this Bloom-side
preview) knows the actual current model versions and code shas.

#### Scenario: A scan with any matching-params source contributes to reused_count but is still enqueued

- **WHEN** at least one of a scan's `cyl_trait_sources` rows has a stored `param_hash` equal to the
  request's `compute_param_hash`, regardless of whether it is that scan's most recent source
- **THEN** `reused_count` in the response includes that scan
- **AND** its `cyl_pipeline_run_scans` row is still written with `status = 'queued'`
- **AND** the scan is still included in a batch and enqueued

#### Scenario: An older matching source is found even when a newer source has different params

- **WHEN** a scan has an earlier source recorded with `params={age: 14}` and a more recent source
  (higher `source_id`) recorded with `params={age: 21}`, and the current request resolves to
  `params={age: 14}`
- **THEN** `reused_count` includes that scan (the earlier, non-latest source's match is still found)

#### Scenario: A scan with two matching sources still contributes only once

- **WHEN** a scan has two distinct `cyl_trait_sources` rows both recording the same `param_hash` as
  the current request (e.g. the same params were legitimately computed twice historically)
- **THEN** `reused_count` includes that scan exactly once, not twice

#### Scenario: The check is batched by id-list length, not a per-scan loop

- **WHEN** the dedup preview runs, with non-empty `params`, against two requests enumerating
  different scan counts that both fit within one 4000-character id list (e.g. 3 scans, then 30 scans)
- **THEN** the number of queries issued against `cyl_scan_traits`/`cyl_trait_sources` is the same in
  both cases — it does not scale with the number of enumerated scans

#### Scenario: Empty params skips the preview

- **WHEN** a request with `params: {}` enumerates any number of scans, including scans with sources
- **THEN** no query is issued against `cyl_scan_traits` or `cyl_trait_sources` for the preview
- **AND** `reused_count = 0`, and every scan is written `'queued'` and enqueued

#### Scenario: A scan with no prior source does not contribute to reused_count

- **WHEN** a scan has no `cyl_trait_sources` row at all
- **THEN** `reused_count` does not include that scan
- **AND** its row is written with `status = 'queued'` and enqueued, same as any other scan

#### Scenario: A scan whose sources all used different params does not contribute to reused_count

- **WHEN** none of a scan's recorded sources' params (by `compute_param_hash`) match the current
  request's `params` as supplied
- **THEN** `reused_count` does not include that scan

#### Scenario: All enumerated scans have a matching source

- **WHEN** every enumerated scan for a request has at least one source matching the request's `params`
- **THEN** `reused_count = scan_count` in the response
- **AND** every scan is still written with `status = 'queued'` and still enqueued in batches — the
  run does **not** short-circuit to `complete` on the strength of this preview alone

## PR 1 — finish the download extraction (#674)

Pure refactor. Cyl's existing tests are the oracle: they pass **unchanged, with no assertion
edited**. No behaviour change to `cyl download`.

- [ ] 1.1 `find_frame_collisions` becomes a wrapper around `find_collisions`, passing `dest_of` and
      `describe`; the clash wording is preserved exactly, because cyl's tests assert it.
- [ ] 1.2 `download_frame` becomes: compute dest, call `download_to`, map `Fetched` → `FrameResult`.
      `download_to` takes `expected_size: int | None = None`; `None` is cyl's case, since
      `cyl_images` records no size (#672).
- [ ] 1.3 Replace the containment check pasted inline in `image_dest` with a `contained_dest` call.
- [ ] 1.4 Replace `download_selector`'s dict comprehension with `selector_of`.
- [ ] 1.5 Pass `bucket` explicitly rather than relying on `download_frame`'s hardcoded
      `IMAGES_BUCKET`, so cyl gets #650's required-`bucket` property from the mechanism instead of
      from a constant.
- [ ] 1.6 Stop importing the private `_path_key` across the package boundary.
- [ ] 1.7 Confirm cyl's containment test lands on the **shared** guard once the fold is done.
      #674 says blanking that guard leaves the suite green at 744 passed; measured against staging
      at 798 it already fails plate's test, so that claim is stale — plate is covered, and cyl's
      inline copy was the untested duplicate. After the fold, blanking it fails both.
- [ ] 1.8 Move `_queried` out of `plate/download.py` into `_postgrest.py` and wrap `fetch_scan`,
      `fetch_scans` and `fetch_genotypes`, so a PostgREST failure stops reaching cyl users as a
      traceback. Catch `httpx.TransportError` alongside `APIError` — a read timeout is currently raw
      on both paths.
- [ ] 1.9 Parameterise `RETRY_HINT` by `noun`, so a plate run stops printing "1/3 captures"
      immediately above "Some frames are failing".
- [ ] 1.10 Correct `_download.py`'s docstring: it forbids naming columns, then names `qr_code`,
      `date_scanned`, `frame_number` and `scans.csv`.

## PR 2a — shared selection layer

- [ ] 2a.1 Add a `selection` module wrapping `cyl_plant_search_query` — barcodes, accession ids,
      species ids, experiment ids in; resolved plant rows out.
- [ ] 2a.2 Implement paging over the RPC's row cap; assert in tests that a selection larger than one
      page resolves completely.
- [ ] 2a.3 Raise a clear error when a filter list exceeds the per-field cap, naming cap and supplied
      count.
- [ ] 2a.4 Resolve `--accession` / `--species` / `--experiment` names to ids reusing the existing
      case-insensitive, trimmed matching helper in `_select.py`.
- [ ] 2a.5 Unit tests for intersection semantics, empty selection, and cap handling.

## PR 2b — `cyl search`

- [ ] 2b.1 Add the `search` command with `--accession`, `--species`, `--experiment`,
      `--experiment-id`.
- [ ] 2b.2 Add `--barcodes-file <path|->` and `--barcodes a,b,c` (mutually exclusive), reading stdin
      on `-`.
- [ ] 2b.3 Report unmatched barcodes distinctly; exit zero when at least one matched.
- [ ] 2b.4 Add `--output csv|json` emitting `scan_id` and `qr_code`; keep all human text on stderr.
- [ ] 2b.5 Migration: a **separate** rollup query returning DISTINCT values for the chosen field,
      with the same permissions, caps and grants as `cyl_plant_search_query`. Do not add a parameter
      to `cyl_plant_search_query` itself — that overloads rather than replaces it and risks ambiguous
      RPC resolution for the live web search box.
- [ ] 2b.6 Add `--show plants|experiments|accessions|species` (default `plants`), with the rollup
      computed server-side over the whole match.
- [ ] 2b.7 Warn on stderr when `--show plants` would exceed the row cap, naming the `--show` value
      that answers completely.
- [ ] 2b.8 Tests: batch resolution in one query, unmatched reporting, cap rejection, clean stdout,
      each `--show` direction, and a rollup over a match larger than one page.

## PR 3 — `cyl download` takes lists

- [ ] 3.1 Change `fetch_scans()` from scalar `experiment_id` / `plant_qr_code` to list-valued
      filters.
- [ ] 3.2 Make `--experiment-id` and `--scan-id` repeatable; add `--barcodes-file`, `--barcodes`,
      `--accession`, `--species`.
- [ ] 3.3 Replace the mutual-exclusion rule with intersection; preserve `--experiment-name`
      resolution and its ambiguity guard.
- [ ] 3.4 Add `--dry-run` reporting scan, experiment, and estimated image counts.
- [ ] 3.5 Add the threshold gate requiring `--yes` for oversized selections.
- [ ] 3.6 Tests: multi-barcode download, multi-experiment `scans.csv`, empty selection, dry run,
      threshold gate both ways.

## PR 4 — traits alongside images

- [ ] 4.1 Add `--with-traits` writing `traits.csv` for the resolved scan set via the source-aware
      trait views, keyed on `scan_id`.
- [ ] 4.2 Add `--traits-only`; reject combination with `--meta-only`.
- [ ] 4.3 Report the count of selected scans that had no trait rows.
- [ ] 4.4 Tests: join integrity between `traits.csv` and `scans.csv`, traits-only skips images,
      conflicting-flag rejection.

## PR 5 — `cyl traits list`

- [ ] 5.1 Migration: trait aggregate query returning name, source, scan count, min and max for the
      supplied filters — same permission model, caps and grants as `cyl_plant_search_query` (#516),
      and the same column shape as `get_experiment_traits`. Leave `get_experiment_traits` unmodified:
      widening it would overload it and break its parity and grant tests.
- [ ] 5.2 Add `cyl traits list` accepting the shared selectors; report one row per trait per source.
- [ ] 5.3 Preserve fractional values; do not round.
- [ ] 5.4 `--output csv|json`; human text on stderr.
- [ ] 5.5 Confirm the aggregate is callable from the web client with no backend change.
- [ ] 5.6 Tests: per-experiment ranges, fractional values, range across more rows than one page,
      per-source rows not merged, clean stdout.

## PR 6 — `cyl traits select` and `--trait` on `cyl download`

- [ ] 6.1 Add a migration for a trait-predicate saved query — "scans whose trait X falls in a range"
      — mirroring `cyl_plant_search_query` (#516): `SECURITY INVOKER`, filter and row caps, grants to
      `authenticated` / `bloom_user` / `bloom_admin` / `bloom_agent`, `anon` revoked.
- [ ] 6.2 Confirm the new query is callable from the web client with no backend change, so web bulk
      download is a front-end task later. Record the check in the PR.
- [ ] 6.3 Add repeatable `--trait NAME[:MIN][:MAX]` with open-ended bounds; accept `--min` / `--max`
      only for a single predicate and reject them otherwise.
- [ ] 6.4 Intersect multiple predicates.
- [ ] 6.5 Accept the shared selectors (`--barcodes-file`, `--barcodes`, `--accession`, `--species`,
      `--experiment-id`) as a pre-filter, passed to the same query in one request.
- [ ] 6.6 Add `--grain scan|barcode` (default `scan`) and `--match any|all` for barcode grain;
      reject `--match` at scan grain.
- [ ] 6.7 Emit `qr_code` and `scan_id` at both grains; barcode grain also reports `scans_matched`
      and `scans_total`.
- [ ] 6.8 Read the latest source when a trait name spans several, and name the source read in the
      output. No `--source` flag — choosing one is tracked in #626.
- [ ] 6.9 Add the same `--trait` predicate to `cyl download`, routed through the same query — no
      second implementation.
- [ ] 6.10 Keep trait selection independent of trait output: `--trait` selects, `--with-traits` /
      `--traits-only` decide files written.
- [ ] 6.11 Tests: single range, open bounds, intersecting predicates, barcode-list pre-filter, both
      grains, `--match all`, ambiguity failure, round-trip into download, `--trait` on download
      resolving the same set as `traits select`, and selection/output independence.

## Every PR — docs and checks

- [ ] 7.1 Update `bloomcli/README.md` and `README.pypi.md` — a bulk selection section and a
      search → select → download walkthrough.
- [ ] 7.2 Add a `CHANGELOG.md` entry under `[Unreleased]`.
- [ ] 7.3 Run `uvx ruff@0.9.9 check .` and `uv run --extra test pytest -m "not integration" -q`.
- [ ] 7.4 Verify the full pipeline against staging: search → trait select → bulk download with
      `--with-traits`.

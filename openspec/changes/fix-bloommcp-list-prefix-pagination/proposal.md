## Why

`SupabaseStorageBackend.list_prefix` calls `client.list(prefix)` with no options, so it
inherits storage3's `DEFAULT_SEARCH_OPTIONS` — `limit: 100, offset: 0`. A prefix with more
than 100 immediate children returns a **silently truncated** list on the deployed `supabase`
backend: no error, no warning, just a short answer that reads as complete (#396, follow-up
from #389, whose review raised the cap and deferred the fix).

`list_prefix` has exactly five callers, and the truncation is not confined to a remote corner
of them:

- **`read_manifest` gates the entire manifest read on a `list_prefix` membership test** —
  `if _MANIFEST_BASENAME not in list_prefix(prefix)` (`manifest/manifest.py:47`). A listing
  that omits `manifest.json` makes it return `None`, which callers read as *"this experiment
  has no history"*: `create_run` then allocates `v1` over an existing `v1`, and `commit()`
  builds a brand-new `Manifest(versions=[entry])` and upserts it over the real one
  (`result_store/supabase_store.py:340-381`), destroying the version history behind an
  `info`-level log. `_resolve_one_class` degrades an `outliers` miss to the `qc` class
  (`experiment_utils.py:685-691`) — the silent revert to untrimmed data that function exists
  to prevent.
- **The audit sweeps enumerate the root prefix.** Both shipped audits call
  `list_prefix("bloommcp_output/")` (`scripts/audit_stale_outlier_trims.py:115`,
  `audit_untrustworthy_outlier_fits.py:114`), whose immediate children are
  `<tool_class>_<stem>` directories across 16 canonical tool classes. That prefix is the one
  that crosses 100 first — at roughly 7 experiments exercised across the full tool set. The
  reports persist an `experiments_scanned` count that would be silently short, while
  `bloommcp-outliers-staleness-audit` requires the scan to enumerate *"every `qc_<stem>`
  manifest"* (`spec.md:117-119`) and treats a missing manifest as unremarkable.
- **The version-directory fallback** (`experiment_utils.py:517-527`, reached only for legacy
  manifests with an empty `version_dir`) degrades to a hard, *misattributed* error —
  "Manifest references version vN but its directory was not found" — for a directory that
  exists.

Why the manifest gate has not fired is worth stating precisely, because the proposal's value
turns on it: it depends entirely on where `manifest.json` lands in the server's first 100 rows.
Under a name-ascending order over the whole prefix it is first (`m` < `v`, and every version
dir starts with `v`), so it is safe. But Supabase's `object/list` is backed by
`storage.search()`, which returns folder pseudo-entries and file rows as separate `UNION ALL`
branches, so `sortBy` orders *within* each branch and not globally; under a folders-first
union, `manifest.json` follows every version directory and falls off page 1 above ~100 of them.
We cannot settle which holds without the live storage-api image. **Paging removes the
dependence entirely**, which is the point: the current code's correctness rests on an
unverified ordering property of a third-party endpoint.

The existing cross-backend parity tests cannot see any of this — they exercise the
**fake/local** interface, and neither `Path.iterdir()` nor the dict-backed in-memory fake has
a page limit. So the defect is invisible to CI and would surface as a wrong answer, not a
failure.

## What Changes

- `SupabaseStorageBackend.list_prefix` pages through the bucket: it repeats
  `client.list(prefix, {"limit": …, "offset": …, "sortBy": …})`, advancing the offset by the
  number of entries actually received, until a page comes back shorter than the requested
  limit, and returns the concatenated names.
- The page size is pinned to storage3's own default (100) so the request is one the server
  already accepts unmodified, and `sortBy` is pinned to `name`/`asc` so page boundaries are
  deterministic across requests instead of borrowed from a third-party default.
- Names are de-duplicated order-preservingly, so a page boundary raced by a concurrent write
  cannot double-count a child — `experiments_scanned` in a persisted audit report is exactly
  the kind of integrity counter that would otherwise absorb it.
- A server that disregards `offset` is caught on the **second** request by a no-progress check
  (a page contributing no new names), with a request cap as a hard backstop. Either raises
  `StorageBackendError` naming the prefix, after an `error`-level log — never an unbounded
  request loop and never a truncated list that reads as complete.
- Adds direct `SupabaseStorageBackend.list_prefix` paging tests (first of their kind for this
  method), a caller-level test that resolves a version directory sitting on page 2, and a
  cross-backend parity case at >1 page.
- `LocalStorageBackend.list_prefix`, the `bloom_mcp.supabase_client` call surface, and every
  caller are unchanged.

## Impact

- Affected specs: `bloommcp-storage-backend` (ADDED: `Supabase Listing Pagination`)
- Affected code:
  - `bloommcp/src/bloom_mcp/storage_backend.py` — `SupabaseStorageBackend.list_prefix`, new
    module constants, the `StorageBackendError` docstring (today scoped to "local-backend
    filesystem failure" though the Supabase adapter already raises it), and the module/class
    docstrings asserting the Supabase methods are the pre-backend helpers "verbatim"
  - `bloommcp/tests/test_storage_backend.py` — new paging tests, and `_FakeSbStorageClient.list`
    (`:641`), whose one-positional-arg signature breaks three currently-green tests the moment
    the call carries options
  - `tests/unit/test_supabase_client.py` — `test_list_prefix_returns_basenames` asserts the
    exact `client.list` call
  - `bloommcp/CHANGELOG.md` — `### Fixed` under `## [Unreleased]`; the package ships to PyPI
  - No change required, but listed as verified-unaffected: `supabase_client.list_prefix`
    (`:242-250`, whose docstring documents the listing contract), both audit scripts, and
    `scripts/gen_plot_snapshots_golden.py:172-173` (stubs `list_prefix` to `[]`)
- **Operational follow-up**: every report under `bloommcp_output/_audit_reports/` was written
  through the truncating enumeration and carries no field recording whether the listing was
  complete. After deploy, re-run both audits and compare `experiments_scanned` and the stem
  set against the last reports; if they differ, the #585/#593 forensic sweeps that drove #420
  remediation status were incomplete and that needs saying out loud.
- **Merge order**: PR #782 (`#573`, open) edits `storage_backend.py`, `test_storage_backend.py`,
  and `CHANGELOG.md`, appending a test section at EOF. This change deliberately places its
  constants beside `_TMP_PREFIX` and its tests inside the existing section-5 `list_prefix`
  family to stay clear of those hunks; whoever lands second should still expect a CHANGELOG
  rebase. #782 also adds a third root-sweeping audit, which inherits this fix.
- See also #498 — the same defect class one layer down (`bloomctl cyl list` reads unbounded
  via PostgREST); out of scope here.
- No API, schema, env-var, or dependency change. A prefix at or under one page still costs
  exactly one request.

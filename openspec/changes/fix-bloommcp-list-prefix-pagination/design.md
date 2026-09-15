## Context

`SupabaseStorageBackend.list_prefix` (`bloommcp/src/bloom_mcp/storage_backend.py:198-203`) is
three lines:

```python
client = get_storage_client()
items = client.list(prefix)
return [item["name"] for item in items]
```

storage3's `SyncBucketProxy.list(path, options=None)` merges the caller's options over
`DEFAULT_SEARCH_OPTIONS = {"limit": 100, "offset": 0, "sortBy": {"column": "name", "order":
"asc"}}` and POSTs them to `object/list` (`_sync/file_api.py:417-444`). Passing no options
therefore asks for the first 100 entries and returns them with no indication that more exist.
Verified against the resolved `storage3==2.31.0`; note this is a **transitive** resolution —
`bloommcp/pyproject.toml:41` declares only `supabase>=2.0.0,<3`, so the 100 is a lockfile fact,
not a pinned contract. That is why a test ties the page size to the library's own default
rather than trusting a comment.

Constraints:

- The `list_prefix(prefix) -> list[str]` signature is fixed by `Storage Backend Interface`;
  paging is entirely internal to the Supabase adapter. (That requirement's own helper count is
  stale — it says "seven", the Purpose says "five", and the code's Protocol has eight since
  `get_object_size`; pre-existing drift, not this change's to fix.)
- `LocalStorageBackend.list_prefix` uses `Path.iterdir()` (complete, plus a `_TMP_PREFIX`
  filter for orphaned atomic-write temp files); the in-memory `_InMemoryObjectStore` iterates a
  dict. Neither has a page limit, which is exactly why the existing parity tests cannot see
  this bug.
- Callers use the result for membership (`"manifest.json" in names`) and prefix matching
  (`startswith(f"{entry.id}_")`, first-match-wins), never for indexing.

## Goals / Non-Goals

- Goals: no silent truncation at any prefix size; one request for the common (≤1 page) case;
  a bounded, loud, early failure if the server does not honor `offset`; tests that pin the
  paging contract rather than a page-limitless fake's.
- Non-Goals: migrating to `list_v2` (cursor-based, returns a pydantic `SearchV2Result` rather
  than `list[dict]`, and needs a newer server); making `list_prefix` lazy/streaming; paging the
  local backend (nothing to page); **full** concurrent-mutation safety — de-duplication makes
  double-reporting impossible, but a child *deleted* mid-sweep can still be missed, and this
  change does not attempt to detect that; filtering Supabase-side synthetic rows.

On that last point: the local backend filters `.tmp-*` explicitly for parity, and the obvious
question is whether the Supabase side needs an analogue (e.g. `.emptyFolderPlaceholder`). It
does not, and the repo has never accounted for such a row: the placeholder is created by the
folder-create API, and bloommcp only ever creates folders implicitly by uploading an object
under them. The name *sets* are genuinely identical, so the parity scenario is not promising
something stronger than the deployed backend delivers.

## Decisions

- **Decision: page with `limit`/`offset` against `client.list`, terminating on the first page
  shorter than the requested limit, advancing the offset by `len(page)`.** This is the paging
  primitive the resolved client exposes for this endpoint. Terminating on a short page keeps
  the ≤1-page case (every prefix in practice today) at exactly one request, so the hot
  `read_manifest` existence check — which runs once per tool class in a
  `list_existing_analyses` call — pays nothing. Advancing by `len(page)` rather than by the
  requested limit costs nothing and closes the mirror-image hole: a client or server that
  disregards `limit` and returns a longer page would otherwise have every entry between the
  limit and the page's true end skipped. The two rules are independent, and only the
  terminate-on-empty variant (below) trades a round-trip.
  - Alternative considered: terminate only on an **empty** page. That is the sole clamp-proof
    rule, but it costs a second request for every small prefix, including that existence
    check. Rejected — with the page size pinned to the client's own default the clamp case is
    the documented assumption instead (next decision), and note `offset += len(page)` does
    *not* by itself buy clamp-safety, so the two halves of this alternative are separable.
- **Decision: pin the page size to the client's default listing limit (100), and assert that
  equality in a test.** This is the value every unconfigured `.list()` call already sends, so
  it is a value the server is known to accept. A larger page (e.g. 1000) would cut round-trips
  but risks a server-side maximum, and if a deployment clamped rather than rejected, a short
  page would silently mean "clamped" — re-introducing the truncation being removed. Honest
  limit: this argument is about the client library, not proof about the server, so the spec
  states end-of-listing as a documented **assumption** rather than a fact. The test asserting
  `_SUPABASE_LIST_PAGE_SIZE == DEFAULT_SEARCH_OPTIONS["limit"]` is what keeps a lockfile bump
  from voiding it silently.
- **Decision: pass `sortBy` explicitly, and claim only request-stability from it.** Offset
  paging is coherent only over an order stable between requests. The resolved client happens
  to default to exactly `name`/`asc`, so this is a no-op on the wire today — but the loop's
  correctness should not rest on another package's default. What it does **not** establish is a
  global name-ascending total order: `object/list` is backed by `storage.search()`, which
  unions folder pseudo-entries and file rows as separate branches, so `sortBy` orders within a
  branch. The spec therefore claims stability, not sortedness. This ordering question is also
  why the "Why" section declines to present the manifest gate as safe-by-sort-position: under a
  folders-first union, `manifest.json` trails every version directory. Settling it needs the
  live storage-api image (see Open Questions).
- **Decision: de-duplicate order-preservingly (`dict.fromkeys`).** Within one prefix, names
  are unique, so a duplicate can only come from a concurrent write shifting rows across a page
  boundary. One line makes the spec's "no entry repeated" true by construction and protects
  `experiments_scanned` — a forensic report's own integrity counter — from double-counting.
- **Decision: detect no-progress first, cap requests as a backstop.** A server that ignored
  `offset` would return a full page forever; a pure short-page loop never terminates. A page
  that contributes **no new names** is the direct observation of that condition and catches it
  on the *second* request rather than the hundredth — and, unlike a bare request cap, it never
  mislabels a legitimately large prefix as a broken server. The cap remains as a hard backstop
  at 50 requests. Being exact about the boundary, since the earlier draft was not: a listing
  ends normally when a page comes back short, so up to 4,999 children enumerate fully and a
  5,000-child prefix raises. Raising rather than returning what was gathered is deliberate — a
  truncated list that *looks* complete is the defect under repair — and the error names the
  knob to raise.
- **Decision: log at error level before raising.** Not cosmetic. On one production path the
  message is discarded outright: `_resolve_one_class` returns `f"Could not list {path}: {e}"`
  without logging (`experiment_utils.py:516-519`), and `supabase_reader.py:87-94` then drops
  that string and raises a fixed `ExperimentNotFoundError`. Without a log, a cap breach there
  would leave no diagnostic anywhere. `StorageBackendError` is already this adapter's failure
  type (`create_signed_url`, `get_object_size`) and subclasses `OSError`, which keeps every
  existing broad `except` gate's contract unchanged — that is what the base class buys, and it
  is *not* an argument that the failure is loud at the caller.
- **Decision: build the client once, outside the loop.** `get_storage_client` does no network
  or auth round-trip, but it constructs a fresh httpx-backed stack per call, so per-page
  construction is pure waste.

## Risks / Trade-offs

- **"Loud" is an adapter-boundary property, not an end-to-end one.** Verified per call site:
  the audit sweeps propagate and exit non-zero (which is what
  `bloommcp-outliers-staleness-audit`'s own "hard, loud failure" scenario requires, and those
  sweeps call `list_prefix` as their first statement, so no partial report is lost); the read
  path turns it into `"could not read manifest for '<stem>': …"`, which is an error distinct
  from the `entry is None` soft "no version found" path; the write path relabels it to
  `ManifestReadError("manifest read failed for qc/<stem>")`, so the prefix survives only in the
  log. Hence the log, and hence the spec says the requirement governs the adapter only.
- **Lexicographic scatter makes the effective threshold non-obvious.** Under name-ascending
  order, the 111 names beginning `v1` (`v1`, `v10`–`v19`, `v100`–`v199`) all precede `v2`, so a
  version-dir prefix truncates at ~111 and the entries lost first are the **oldest** (`v2`–`v9`)
  — precisely the low-numbered, legacy manifests whose `version_dir` is empty and which
  therefore need the sibling-enumeration fallback. (`startswith(f"{entry.id}_")` cannot
  false-positive: `"v10_…"` does not start with `"v1_"`, so a wrong directory is never selected.)
- **More round-trips for large prefixes** (one per 100 entries) → accepted; `list_prefix` is
  called on manifest-existence checks and audit sweeps, not per-row, and the alternative is a
  wrong answer.
- **No overall deadline on a multi-page sweep.** `list_prefix` calls `get_storage_client()`
  without `timeout_seconds` (unlike `delete_files`, which takes that knob), so the worst case
  is the request cap times the client's per-request timeout. The 50-request backstop bounds it;
  a per-sweep deadline is not added here.
- **A new failure mode** on a path that previously could not fail this way → mitigated by the
  no-progress check firing on request two, and by a cap far above any real prefix.
- **Test coupling to the request shape**: the new tests assert the options dict passed to
  `client.list`. A client-library change to that shape fails them — which is the point; that is
  the drift that produced #396.

## Migration Plan

None for stored data: no key layout, env var, or public signature moves. Rollback is reverting
the commit; a prefix under 100 entries behaves identically before and after. The operational
follow-up is in `proposal.md` — previously written audit reports may be under-scoped and both
audits should be re-run after deploy.

## Open Questions

- The `storage.search()` union/ordering shape, and whether any deployment clamps `limit` below
  the requested value, are unverifiable from this repo (they ship inside the storage-api image;
  no docker daemon here). Both are currently load-bearing *assumptions*, stated as such. The
  cheap way to settle them is an integration test: the repo already runs `tests/integration/`
  against the live compose stack in CI's `compose-health-check`, and
  `test_cyl_intermediates_bucket.py` shows the psycopg + `storage.objects` seeding pattern —
  seed >100 objects under one prefix, assert `list_prefix` returns all of them, and dump
  `pg_get_functiondef('storage.search')`. Deliberately not in scope here: it needs the stack,
  and the unit-level fix stands on its own.
- If a prefix ever legitimately approaches the cap, cursor-based `list_v2` is the better answer
  than a bigger cap.

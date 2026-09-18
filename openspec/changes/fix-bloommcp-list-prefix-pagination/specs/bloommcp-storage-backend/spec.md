## ADDED Requirements

### Requirement: Supabase Listing Pagination

`SupabaseStorageBackend.list_prefix` SHALL return **every** immediate child of a prefix,
regardless of how many there are, rather than only the first page the Supabase Storage client
returns by default. It SHALL page through the listing by repeating the client's list call with
an explicit `limit` and an advancing `offset`, and SHALL advance that offset by the number of
entries actually received rather than by the number requested, so that a page larger than the
requested limit cannot cause entries to be skipped. It SHALL stop at the first page shorter
than the requested limit.

This pagination is the one intended deviation from the clause in `Backend Selection via
BLOOM_STORAGE_BACKEND` requiring the unset/`supabase` path to be byte-for-byte identical to
the prior Supabase-only behavior: that clause governs the bytes written and read for an
artifact, and SHALL be read as unaffected by the number of list requests used to enumerate a
prefix.

The requested page size SHALL be a fixed size the storage client's server already accepts
unmodified, and a test SHALL pin it to the storage client's own default listing limit so a
dependency upgrade that changes that default cannot silently invalidate the choice. A page
shorter than the requested limit SHALL be treated as an end-of-listing signal, on the
documented assumption that the server honors the requested limit rather than clamping it
below the requested value; the page size is chosen to equal the client's own default
specifically so that assumption holds for a value the server is already known to accept.

The listing SHALL be requested under an order pinned explicitly via `sortBy` (by object name,
ascending) rather than relying on the storage client's default sort, so that page boundaries
are deterministic across the requests of one sweep. This SHALL NOT be read as a guarantee of
a global name-ascending total order over the returned names, because the endpoint may order
folder and file entries as separate groups; what is required is only that the order be stable
between requests.

Returned names SHALL be de-duplicated while preserving order, so that a child shifted across
a page boundary by a concurrent write cannot be reported twice.

A prefix whose children fit in a single page SHALL cost exactly one list request, so the
common case — including the manifest-existence check on the read path — pays nothing for
pagination. The storage client SHALL be constructed once per `list_prefix` call rather than
once per page.

The sweep SHALL detect a server that disregards the advancing `offset` by observing that a
page contributed no new names, and SHALL additionally be bounded by a maximum request count as
a backstop. On either condition the backend SHALL log at error level and raise a
`StorageBackendError` naming the prefix, rather than looping without bound or returning the
entries gathered so far. Returning a partial list that reads as complete is the defect this
requirement removes, so the adapter SHALL NEVER silently truncate. The prefix SHALL appear
early in the message, because the text reaches persisted audit reports through a helper that
truncates it. The message SHALL NOT embed the accumulated entry names.

Callers SHALL be left to their existing error handling: this requirement governs the adapter
boundary only, and SHALL NOT be read as a promise about how far up any particular caller
surfaces the failure.

#### Scenario: A prefix with more children than one page lists all of them

- **WHEN** `list_prefix(prefix)` is called against the Supabase backend for a prefix holding
  more immediate children than one page (e.g. 250 version directories)
- **THEN** it returns the names of **all** of them, by issuing successive list calls whose
  `offset` advances until a short page ends the sweep, with no entry missing and no entry
  repeated

#### Scenario: A single-page prefix costs one request

- **WHEN** `list_prefix(prefix)` is called for a prefix whose children fit within one page,
  or for one that has no children at all
- **THEN** exactly one list call is made, one storage client is constructed, and its names are
  returned — an empty listing still yielding an empty list, not an error

#### Scenario: A prefix holding an exact multiple of the page size is not truncated

- **WHEN** a prefix holds exactly one full page of children and no more
- **THEN** a further list call is made (its full predecessor being indistinguishable from a
  continuing listing), it comes back empty, and all children of the full page are returned

#### Scenario: A page longer than the requested limit is not skipped past

- **WHEN** the storage client returns more entries than the requested `limit`
- **THEN** the offset advances by the number of entries actually received, so no entry between
  the requested limit and that page's true end is skipped

#### Scenario: The list request pins page size and sort order

- **WHEN** the Supabase backend issues a list call for a prefix
- **THEN** the options it passes carry the page size as `limit`, the current position as
  `offset`, and an explicit name-ascending `sortBy`; the prefix is passed through byte-identical
  to what the caller supplied, including an empty or trailing-slash form

#### Scenario: The page size stays tied to the client's default limit

- **WHEN** the test suite runs against the resolved storage client library
- **THEN** it asserts the configured page size equals that library's own default listing limit,
  so an upgrade changing that default fails a test rather than silently weakening the
  end-of-listing assumption

#### Scenario: A server that ignores offset fails loudly on the second request

- **WHEN** the storage client keeps returning the same full page for every request, so a page
  contributes no new names
- **THEN** the backend stops at that point — not after exhausting the request cap — logs at
  error level, and raises a `StorageBackendError` naming the prefix, rather than requesting
  without bound or returning a silently truncated list

#### Scenario: A listing that never terminates is bounded

- **WHEN** a sweep keeps making progress but exceeds the maximum request count
- **THEN** the backend logs at error level and raises a `StorageBackendError` naming the prefix
  and the bound, rather than continuing indefinitely

#### Scenario: A version directory beyond the first page still resolves

- **WHEN** the versioned-cleaned lookup falls back to enumerating sibling directories for a
  manifest entry whose recorded `version_dir` is empty, and that directory sorts beyond the
  first page
- **THEN** the cleaned CSV resolves successfully, instead of reporting that a directory the
  manifest references was not found

#### Scenario: Pagination is pinned by tests, not by the page-limitless fakes

- **WHEN** the test suite runs
- **THEN** it exercises `SupabaseStorageBackend.list_prefix` directly against a stand-in client
  that applies `limit`/`offset` over a name-sorted listing, mirroring the Supabase
  `object/list` semantics — covering multi-page, single-page, exact-multiple, over-long-page,
  and offset-disregarded cases — and asserts cross-backend name parity with the local backend
  for a prefix spanning more than one page, so the truncation cannot regress unnoticed behind
  a fake that has no page limit

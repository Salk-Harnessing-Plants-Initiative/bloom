## MODIFIED Requirements

### Requirement: Re-Signing An Already-Committed Run's Download Links

The `ResultStore` Protocol SHALL provide `get_download_links(experiment, tool_class,
run_ref="latest") -> StoredRun`, resolving a previously committed run through the same
manifest/record lookup `get_run` uses and returning it with a freshly built `output_links`
populated — unlike `list_runs`, which always returns `output_links == {}`. This is a
deliberate, caller-opted-in exception to that existing behavior, not a change to it: a caller
must call `get_download_links` by name to get signed links for a run it did not just commit
itself. This capability SHALL NOT persist anything, and SHALL NOT change the manifest,
`Provenance`, or `VersionEntry` schema in any way — every value it returns is either already
persisted (`output_sha256`, `output_keys`, `manifest_path`, `params`, `based_on_version`) or
resolved fresh at call time (`url`, `size_bytes`).

Before signing or sizing any output, `get_download_links` SHALL recompute the expected
object-key prefix fresh from `(experiment, tool_class, the resolved run's version_dir)` and
verify every persisted `output_key` falls within it, for both the `create_signed_url` and
`get_object_size` calls that follow; a key outside that prefix indicates corrupt manifest
data or a resolution bug (never a caller-input condition) and SHALL raise
`CorruptRunLinksError` rather than looking it up or signing it. This check is independent of,
and SHALL NOT depend on the merge order of, `add-bloommcp-signed-url-key-scoping`'s (#598)
analogous write-side guard on `commit`.

For a resolved run whose `output_keys` is empty (a legacy entry recorded before per-artifact
keys existed — e.g. a v2 manifest entry), `get_download_links` SHALL return `output_links == {}`
rather than raising, since there is no key to sign or size. For a resolved run with populated
`output_keys`, each output's `sha256` SHALL come from the persisted `output_sha256`, `url`
SHALL come from the active `StorageBackend`'s `create_signed_url` (the same fixed
`SIGNED_URL_EXPIRES_SECONDS` expiry `commit` already signs with — no per-call expiry
parameter), and `size_bytes` SHALL be resolved live via `StorageBackend.get_object_size` for
every output on every call — uniformly for a run committed a moment ago or long before this
capability existed, with no persisted size field of any kind. A failure to sign or size any
one output SHALL fail the whole call (propagating a clear error) rather than returning a
partially-populated `output_links` with no indication some outputs were silently skipped.

`get_run` (and therefore `get_download_links`, which calls it internally) SHALL also attach the
resolved run's own `params` (its exact recorded tool-call kwargs) and `based_on_version` to the
returned `StoredRun`, sourced from the same single `VersionEntry` the rest of the resolution
already reads — never from any other run for the same `(experiment, tool_class)`.
**`list_runs` and `commit` SHALL leave `params == {}` and `based_on_version == ""`** (their
`StoredRun`s' dataclass defaults): these two fields are populated only inside `get_run` itself,
never inside `StoredRun.from_version_entry`, specifically so `list_runs` — which backs
`list_existing_analyses`, an always-included discovery tool that returns every historical run's
`StoredRun` verbatim — never discloses one run's `params` while resolving a different one, nor
turns an always-on tool into a cross-run params leak. This SHALL hold regardless of whether
`output_keys` is populated — a run's `params`/`based_on_version` were part of the manifest
schema from the start (unlike `seed`/`agent`/`environment`, `output_sha256`/`output_keys`, all
v3-additive), so even the oldest recorded run has them.

**A signed link to the run's own `manifest.json` (a prior design of this same requirement,
`manifest_url`) SHALL NOT be provided by this method.** `manifest.json` is keyed only by
`(experiment, tool_class)` — never by `run_ref` — so a signed link to it cannot be scoped to
the single resolved run: it would expose every run ever committed for that pair, including each
one's own `params`/`source_id`/`source_name`/`based_on_version`, not just the one the caller
asked about. `params`/`based_on_version` above exist specifically to serve the same
provenance-verification need without that cross-run exposure.

`FakeResultStore` SHALL implement the same resolution, prefix guard, empty-`output_keys`
short-circuit for `output_links`, and single-run-scoped `params`/`based_on_version` attachment
in `get_run`, and SHALL produce a real (not fabricated) `size_bytes` for any run it itself
recorded via its own private, in-memory record of each output's byte size captured at commit
time (identical `hash_outputs` computation the real adapter also performs) — without making any
call to `StorageBackend`, since it never uploads real bytes for a live lookup to meaningfully
target. Because this adapter's `list_runs`/`get_run` share one in-memory list populated once at
`commit()` time (unlike the real adapter, which re-reads the manifest fresh on every call), it
SHALL keep `params`/`based_on_version` out of that shared list and instead resolve them for
`get_run` from a private, commit-time-populated side table keyed by
`(experiment, tool_class, run_ref)` — the same pattern its existing `size_bytes` bookkeeping
already uses — so `list_runs` never gains them by construction, not by convention.

An unresolvable `(experiment, tool_class, run_ref)` SHALL raise `RunNotFoundError`, identically
to `get_run`.

#### Scenario: A caller gets fresh links for a run committed in a prior session

- **WHEN** `get_download_links(experiment, tool_class, "latest")` is called for a run that was
  committed and whose signed URLs have since expired
- **THEN** it returns a `StoredRun` whose `output_links` carries a fresh, working `url` per
  output, each with the correct `sha256` and a live-resolved `size_bytes`, and whose `params`/
  `based_on_version` match that run's own recorded values

#### Scenario: An explicit run_ref resolves the same as get_run

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` is called with a specific
  version id (not `"latest"`)
- **THEN** it resolves the same run `get_run(experiment, tool_class, run_ref)` would, with
  freshly signed `output_links` and that same run's `params`/`based_on_version` attached

#### Scenario: size_bytes is always resolved live, never persisted

- **WHEN** `get_download_links` resolves any run with populated `output_keys` — regardless of
  when it was committed
- **THEN** every `size_bytes` comes from a live `StorageBackend.get_object_size` call, and no
  manifest, `Provenance`, or `VersionEntry` field is read, written, or created for this
  purpose

#### Scenario: A legacy run with no recorded keys yields no output links, but still its own params

- **WHEN** `get_download_links` resolves a run whose `output_keys` is empty (e.g. a v2 manifest
  entry recorded before per-artifact keys existed)
- **THEN** it returns the resolved `StoredRun` with `output_links == {}`, without raising, and
  with that run's own `params`/`based_on_version` still populated — these fields were recorded
  regardless of manifest schema version, unlike the v3-only fields absent from a v2 entry

#### Scenario: A retired tool_class is still resolvable

- **WHEN** `get_download_links` is called with a `tool_class` that has since been retired from
  active use (e.g. `"stats"`) but still has historical runs recorded
- **THEN** it resolves and re-signs that run's links exactly as it would for an active
  tool_class

#### Scenario: Unknown run reference is reported through the contract

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` is called for a reference or
  tool_class with no recorded run
- **THEN** it raises `RunNotFoundError`, identically to `get_run`

#### Scenario: A key outside the run's own scope is never signed or sized

- **WHEN** `get_download_links` resolves a run whose persisted `output_keys` includes a key (by
  test injection — no legitimate call path produces this) that does not fall under the freshly
  recomputed `(experiment, tool_class, version_dir)` prefix
- **THEN** it raises `CorruptRunLinksError` without calling `create_signed_url` or
  `get_object_size` for that key

#### Scenario: A single output's failure aborts the whole call

- **WHEN** any one output's `create_signed_url` or `get_object_size` call raises (for example,
  the object was deleted from storage after the manifest still lists it)
- **THEN** `get_download_links` raises rather than returning a partially-populated
  `output_links` for the other outputs

#### Scenario: get_run and list_runs never disclose params across runs

- **WHEN** two runs exist for the same `(experiment, tool_class)`, each committed with distinct
  `params`
- **THEN** `get_run(experiment, tool_class, run_ref=<either>)` returns only that run's own
  `params`/`based_on_version`, never the other's, and `list_runs(experiment, tool_class)`
  returns `params == {}`/`based_on_version == ""` for every entry regardless of which run each
  entry describes

#### Scenario: The fake store never calls StorageBackend

- **WHEN** `FakeResultStore.get_download_links(...)` is called for any run it has recorded
- **THEN** every `size_bytes` comes from that run's own recorded byte size (captured
  internally at commit time, identically to the real adapter's `hash_outputs` computation), and
  no call to `StorageBackend` of any kind is made

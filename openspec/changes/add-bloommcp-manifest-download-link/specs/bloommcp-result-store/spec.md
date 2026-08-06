## MODIFIED Requirements

### Requirement: Re-Signing An Already-Committed Run's Download Links

The `ResultStore` Protocol SHALL provide `get_download_links(experiment, tool_class,
run_ref="latest") -> StoredRun`, resolving a previously committed run through the same
manifest/record lookup `get_run` uses and returning it with a freshly built `output_links` and
`manifest_url` populated — unlike `get_run`/`list_runs`, which always return `output_links == {}`
and `manifest_url is None`. This is a deliberate, caller-opted-in exception to that existing
behavior, not a change to it: a caller must call `get_download_links` by name to get signed
links for a run it did not just commit itself. This capability SHALL NOT persist anything, and
SHALL NOT change the manifest, `Provenance`, or `VersionEntry` schema in any way — every value
it returns is either already persisted (`output_sha256`, `output_keys`, `manifest_path`) or
resolved fresh at call time (`url`, `size_bytes`, `manifest_url`).

Before signing or sizing any output, `get_download_links` SHALL recompute the expected
object-key prefix fresh from `(experiment, tool_class, the resolved run's version_dir)` and
verify every persisted `output_key` falls within it, for both the `create_signed_url` and
`get_object_size` calls that follow; a key outside that prefix indicates corrupt manifest
data or a resolution bug (never a caller-input condition) and SHALL raise
`CorruptRunLinksError` rather than looking it up or signing it. This check is independent of,
and SHALL NOT depend on the merge order of, `add-bloommcp-signed-url-key-scoping`'s (#598)
analogous write-side guard on `commit`. `manifest_path` is exempt from this guard: it is always
deterministically recomputed by the adapter itself from `(experiment, tool_class)` alone,
never read back from the manifest's own persisted content the way `output_key` is, so no
"corrupt manifest points at a foreign key" vector exists for it to guard against.

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

Independently of `output_links` and its empty-`output_keys` short-circuit, `get_download_links`
SHALL also sign the resolved run's own `manifest_path` via `create_signed_url` (the same fixed
expiry) and return it as `manifest_url` — a run's manifest always exists once committed,
regardless of whether that run's `output_keys` happen to be populated, so this is never
skipped or gated on the outputs' own key-presence check. A failure to sign the manifest SHALL
fail the whole call, identically to a per-output signing failure.

`FakeResultStore` SHALL implement the same resolution, prefix guard, and empty-`output_keys`
short-circuit for `output_links`, and SHALL produce a real (not fabricated) `size_bytes` for
any run it itself recorded via its own private, in-memory record of each output's byte size
captured at commit time (identical `hash_outputs` computation the real adapter also performs)
— without making any call to `StorageBackend`, since it never uploads real bytes for a live
lookup to meaningfully target. It SHALL synthesize `manifest_url` from `stored.manifest_path`
in the same `fake://signed/{key}?expires_in=...` style its `output_links` URLs already use, with
no additional bookkeeping (unlike `size_bytes`, there is no size or hash component to the
manifest link).

An unresolvable `(experiment, tool_class, run_ref)` SHALL raise `RunNotFoundError`, identically
to `get_run`.

#### Scenario: A caller gets fresh links for a run committed in a prior session

- **WHEN** `get_download_links(experiment, tool_class, "latest")` is called for a run that was
  committed and whose signed URLs have since expired
- **THEN** it returns a `StoredRun` whose `output_links` carries a fresh, working `url` per
  output, each with the correct `sha256` and a live-resolved `size_bytes`, and whose
  `manifest_url` carries a fresh, working link for that run's `manifest.json`

#### Scenario: An explicit run_ref resolves the same as get_run

- **WHEN** `get_download_links(experiment, tool_class, run_ref)` is called with a specific
  version id (not `"latest"`)
- **THEN** it resolves the same run `get_run(experiment, tool_class, run_ref)` would, with
  freshly signed `output_links` and `manifest_url` attached

#### Scenario: size_bytes is always resolved live, never persisted

- **WHEN** `get_download_links` resolves any run with populated `output_keys` — regardless of
  when it was committed
- **THEN** every `size_bytes` comes from a live `StorageBackend.get_object_size` call, and no
  manifest, `Provenance`, or `VersionEntry` field is read, written, or created for this
  purpose

#### Scenario: A legacy run with no recorded keys yields no output links, but still a manifest link

- **WHEN** `get_download_links` resolves a run whose `output_keys` is empty (e.g. a v2 manifest
  entry recorded before per-artifact keys existed)
- **THEN** it returns the resolved `StoredRun` with `output_links == {}`, without raising, and
  with `manifest_url` still populated — that run's `manifest.json` exists regardless of
  whether per-artifact output keys were ever recorded for it

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

#### Scenario: A manifest-signing failure aborts the whole call

- **WHEN** the `create_signed_url` call for the resolved run's `manifest_path` raises
- **THEN** `get_download_links` raises rather than returning a `StoredRun` with a populated
  `output_links` but a missing `manifest_url`

#### Scenario: The fake store never calls StorageBackend

- **WHEN** `FakeResultStore.get_download_links(...)` is called for any run it has recorded
- **THEN** every `size_bytes` comes from that run's own recorded byte size (captured
  internally at commit time, identically to the real adapter's `hash_outputs` computation),
  and `manifest_url` is a synthesized `fake://` string derived from `manifest_path`, with no
  call to `StorageBackend` of any kind

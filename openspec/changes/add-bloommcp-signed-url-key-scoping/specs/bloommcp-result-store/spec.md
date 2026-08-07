## MODIFIED Requirements

### Requirement: Per-Output Signed Links And Size At Commit

`ResultStore.commit(run, outputs)` SHALL return a `StoredRun` whose `output_links: dict[str,
OutputLink]` carries one entry per `outputs` entry, keyed identically, each an `OutputLink` with
the artifact's storage `key`, a signed/served `url` from the active `StorageBackend`'s
`create_signed_url`, its `sha256` (matching `output_sha256`), and its non-negative `size_bytes`
(a legitimate zero-byte artifact is not rejected — only an empty `outputs` dict is). This field
SHALL be populated only by `commit` — `get_run` and `list_runs` SHALL return `output_links` as an
empty dict (including when the resolved run was recorded before this capability existed, e.g. a
legacy v2 manifest entry with no `output_sha256`/`output_keys`), so that resolving or listing
potentially many historical runs never eagerly generates signed URLs for artifacts other than the
one a caller's own `commit` call just produced. A failure to generate or extract a usable signed
URL for any output — including a signing-client response that carries none of its expected URL
keys — SHALL fail the whole `commit` call (surfacing as `CommitFailedError`, following the same
best-effort-cleanup path an upload failure already takes) rather than committing with a partial or
`None` URL. None of `output_links` SHALL be persisted into the manifest `VersionEntry` — it is
computed at request time from data already in hand (the freshly hashed staged bytes, the freshly
uploaded key) and a fresh signing call, so existing manifest/provenance fields and cross-backend
manifest-byte-identity are unaffected.

Before signing any output, `commit` SHALL verify that every key it is about to sign falls within
the prefix `commit` itself computed for this run (`{output_root}/{tool_class}_{stem}/
{version_dir}/`) — the same prefix its own `key_for` closure used to build every `output_keys`
entry and to upload the corresponding bytes moments earlier. A key outside that prefix indicates a
structural bug (never a caller-input condition, since `outputs` names only relative paths within
the run's own staging directory) and SHALL fail the whole `commit` call via the same
`CommitFailedError` fail-closed/cleanup path a signing failure already takes — never a bare
signed URL for an unverified key. This guarantee SHALL hold identically for `FakeResultStore`,
which SHALL compute and check the equivalent prefix from its own `key_for` construction, so a test
against the fake exercises the same structural guarantee the real adapter provides.

#### Scenario: Commit returns a signed link per output

- **WHEN** a consumer writes outputs into the run's staging directory and calls
  `commit(run, outputs)`
- **THEN** the returned `StoredRun.output_links` has one entry per `outputs` entry, each
  carrying a non-empty `url`, the same `sha256` as `output_sha256` for that name, and a
  non-negative `size_bytes`

#### Scenario: get_run and list_runs do not carry signed links

- **WHEN** `get_run(experiment, tool_class, run_ref)` or `list_runs(experiment, tool_class)` is
  called for a previously committed run — including a legacy run recorded before this
  capability existed (e.g. a v2 manifest entry with no `output_sha256`/`output_keys`)
- **THEN** the returned `StoredRun`(s) have `output_links == {}`, regardless of how many
  historical versions or outputs exist

#### Scenario: A signing failure fails the whole commit

- **WHEN** the active backend's `create_signed_url` raises, or returns a response with no
  extractable URL, for any one output during `commit`
- **THEN** `commit` raises `CommitFailedError`, best-effort cleans up any objects already
  uploaded for this call, and records no new version — mirroring an upload failure

#### Scenario: The fake store returns a shape-equivalent link without touching a real backend

- **WHEN** `FakeResultStore.commit(...)` is called
- **THEN** the returned `StoredRun.output_links` has the same keys, `sha256`, and `size_bytes` a
  real commit would produce, with a synthesized (non-network) URL — no call to
  `storage_backend.active_backend()` is made

#### Scenario: Manifest bytes are unaffected

- **WHEN** a run commits and `output_links` is populated on the returned `StoredRun`
- **THEN** the written `manifest.json`'s `VersionEntry` for this run contains no `output_links`,
  URL, or size key, and every other field matches the same commit's pre-change golden/fixture
  manifest byte-for-byte (no schema version change)

#### Scenario: A key outside this run's own prefix is never signed

- **WHEN** `commit` is (by test injection — no legitimate call path produces this) about to sign a
  key that does not start with this run's own `{output_root}/{tool_class}_{stem}/{version_dir}/`
  prefix
- **THEN** `commit` raises (never calling `create_signed_url` for that key), the failure surfaces
  as `CommitFailedError` via the same fail-closed/cleanup path a signing failure already takes,
  and no version is recorded

#### Scenario: Every real call site's keys satisfy the scoping check

- **WHEN** any of the 8 consumer tools (`qc_clean`, `qc_inspect`, `pca_analysis`,
  `remove_outliers`, `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`,
  `clustering`) commits a run through either `SupabaseResultStore` or `FakeResultStore`
- **THEN** the scoping check passes for every output with no behavior change from before this
  requirement — the existing test suite for each tool requires no modification

## ADDED Requirements

### Requirement: Cyl ingest command reads an envelope from a path or stdin

The `bloomctl` CLI SHALL provide a `cyl ingest` command that reads a single per-scan
`ResultEnvelope` (JSON) from a filesystem path argument, or from standard input when the
argument is `-`, and writes it to Bloom by calling the `insert_cyl_result_envelope(jsonb)` RPC
(capability `cyl-trait-writeback`) as `client.rpc("insert_cyl_result_envelope", {"envelope":
<envelope>})`. The command SHALL accept a `--profile` option (defaulting like the other
commands) and authenticate through the existing credentials profile.

#### Scenario: Ingest from a file path

- **WHEN** the user runs `bloomctl cyl ingest path/to/scan.result.json` with a valid envelope
  and a profile whose scan is resolvable
- **THEN** the command reads and parses the file, calls the RPC with the envelope under the
  `envelope` argument, and exits zero

#### Scenario: Ingest from stdin

- **WHEN** the user runs `bloomctl cyl ingest -` and pipes a valid envelope on stdin
- **THEN** the command reads the envelope from stdin and ingests it identically to the file path
  case

#### Scenario: Unreadable or malformed input

- **WHEN** the path does not exist, the content is not valid JSON, or stdin (`-`) is empty
- **THEN** the command prints a readable error, exits non-zero, and makes no RPC call

#### Scenario: Source-only envelope (no traits or blobs)

- **WHEN** a valid envelope whose `traits` and `blobs` are both empty is ingested and the RPC
  writes only the source row (`trait_count = 0`, `blob_count = 0`)
- **THEN** the command reports success with zero trait/blob counts and exits zero

### Requirement: Envelope is validated before the write and sent unchanged

The command SHALL validate the envelope against `sleap-roots-contracts`
(`ResultEnvelope.model_validate`) as a fail-fast gate before any network call, and SHALL send
the original parsed JSON object to the RPC unchanged (it SHALL NOT re-serialize the envelope
through the contract model), so the producer's `provenance.idempotency_key` is preserved exactly.

#### Scenario: Valid envelope is sent verbatim

- **WHEN** a schema-valid envelope is ingested
- **THEN** the object passed to the RPC equals the originally parsed JSON (same
  `provenance.idempotency_key`, no model-derived substitution)

#### Scenario: Schema-invalid envelope fails fast

- **WHEN** the envelope does not conform to `ResultEnvelope`
- **THEN** the command reports a readable validation error and exits non-zero **before** any
  authentication or RPC call

#### Scenario: Validation gate is stricter than the RPC

- **WHEN** an envelope omits a provenance field that `ResultEnvelope` requires but the RPC does
  not read (e.g. `provenance.inputs.images_checksum` or `provenance.params`)
- **THEN** the command rejects it at the validation gate with a readable message and exits
  non-zero **before** authenticating or calling the RPC

### Requirement: Re-ingest is a benign, distinctly-reported no-op

The command SHALL report the RPC's first-writer-wins no-op — `was_noop=true`, which the RPC
returns without raising for an already-ingested envelope — as a success distinct from a real
error, exiting zero. Re-ingesting the same envelope therefore MUST NOT be reported as a failure.

#### Scenario: First ingest of an envelope

- **WHEN** the RPC returns `was_noop=false`
- **THEN** the command prints a summary indicating the envelope was ingested (including the
  `source_id`) and exits zero

#### Scenario: Re-ingest of the same envelope

- **WHEN** the RPC returns `was_noop=true` (with a null `scan_id`, per `cyl-trait-writeback`)
- **THEN** the command prints an "already ingested" message (naming the `source_id`) that is
  visibly not an error, does not depend on `scan_id` being present, and exits zero

### Requirement: RPC validation failures map to actionable messages

The command SHALL translate the RPC's `RAISE EXCEPTION` validation errors into readable,
actionable CLI messages and exit non-zero, and SHALL surface unrecognized RPC errors verbatim
rather than suppressing them.

#### Scenario: Image ids do not resolve to exactly one scan

- **WHEN** the RPC rejects the envelope because `inputs.image_ids` resolve to no scan or to
  more than one scan (e.g. `no image_ids…`, `unresolvable image_ids: matched X of Y…`, or
  `image_ids resolve to N scans, expected exactly 1`)
- **THEN** the command prints an actionable message that names the profile/server in use and
  explains that the scan's images must already exist in `cyl_images` on that Bloom, and exits
  non-zero

#### Scenario: Contract version mismatch

- **WHEN** the RPC rejects the envelope's `contract_version`
- **THEN** the command reports the version the server expects and the version the envelope
  carried, and exits non-zero

#### Scenario: Other validation failures

- **WHEN** the RPC raises for an empty `idempotency_key`, a `scan_key` disagreement, or other
  documented validation
- **THEN** the command surfaces the failure with field context and exits non-zero

#### Scenario: Unknown RPC error

- **WHEN** the RPC returns an error the command does not specifically recognize
- **THEN** the command surfaces the original message and exits non-zero (no silent success)

### Requirement: Authentication uses an existing profile and requires write access

The command SHALL authenticate using an existing `bloomctl` credentials profile (interactive
login), and SHALL surface a clear error when credentials are missing/invalid or when the
authenticated role lacks `EXECUTE` on the RPC (granted to `bloom_writer` / `bloom_admin` /
`service_role`). Non-interactive/scoped credentials are out of scope for this capability.

#### Scenario: Missing or invalid credentials

- **WHEN** the selected profile has no stored credentials or they fail sign-in
- **THEN** the command prints guidance to run `bloomctl login` and exits non-zero

#### Scenario: Authenticated role lacks write access

- **WHEN** the authenticated profile lacks `EXECUTE` on `insert_cyl_result_envelope`
- **THEN** the resulting permission error is surfaced clearly and the command exits non-zero

### Requirement: Machine-readable result output

The command SHALL support a `--json` flag that writes the RPC's return summary — the `jsonb`
object defined by the `cyl-trait-writeback` capability (`source_id`, `scan_id`, `trait_count`,
`blob_count`, `was_noop`; `scan_id` is null on a no-op re-delivery) — to standard output as JSON
for programmatic consumption (e.g. the A4 write-back step capturing `source_id`); without the
flag it SHALL print a human-readable summary. The command SHALL NOT redefine or reshape the
RPC's return object.

#### Scenario: JSON output on first ingest

- **WHEN** the user passes `--json` on a first ingest
- **THEN** stdout contains the parseable RPC result object, including `source_id` and
  `was_noop=false`

#### Scenario: JSON output on re-ingest

- **WHEN** the user passes `--json` re-ingesting an already-ingested envelope
- **THEN** stdout contains the parseable RPC result object with `was_noop=true` and `source_id`
  (a null `scan_id` is tolerated), and the command exits zero

#### Scenario: Default human-readable output

- **WHEN** the user omits `--json`
- **THEN** stdout contains a human-readable summary line rather than raw JSON

### Requirement: Blobs pass through without upload

The command SHALL forward the envelope's `blobs` array to the RPC unchanged, and SHALL NOT
upload blob bytes to object storage (MinIO/Box) in this capability. The blob byte-upload is a
tracked, separate follow-up.

#### Scenario: Envelope carrying blobs

- **WHEN** an envelope with a non-empty `blobs` array is ingested
- **THEN** the `blobs` entries are included in the RPC payload as-is and no object-storage upload
  is attempted

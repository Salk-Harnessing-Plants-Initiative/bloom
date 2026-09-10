## MODIFIED Requirements

### Requirement: Cyl ingest command reads an envelope from a path or stdin

The `bloomctl` CLI SHALL provide a `cyl ingest-result` command that reads a single per-scan
`ResultEnvelope` (JSON) from a filesystem path argument, or from standard input when the
argument is `-`, and writes it to Bloom by calling the `insert_cyl_result_envelope(jsonb, text)` RPC
(capability `cyl-trait-writeback`) as `client.rpc("insert_cyl_result_envelope", {"envelope":
<envelope>, "p_argo_workflow_name": <value or omitted>})`. The `p_argo_workflow_name` key SHALL be
included, set to `os.environ["ARGO_WORKFLOW_NAME"]`, only when that environment variable is set and
non-empty; when unset, the command SHALL omit the key entirely (relying on the RPC's `DEFAULT NULL`)
rather than sending an empty string, preserving the existing manual/ad-hoc invocation shape exactly.
The command SHALL accept a `--profile` option (defaulting like the other commands) and authenticate
through the existing credentials profile. When `p_argo_workflow_name` was supplied and the RPC's
returned `status_update_matched` is `false` — a delivery that wrote its trait/blob data correctly
but whose per-scan status linkage was silently skipped by an already-permanent guard, because the
matching `cyl_pipeline_run_scans` row was already `'failed'` — both this command and the shared
per-envelope batch helper (`ingest_one_envelope`, used by `cyl batch-ingest-result`) SHALL report it
as a failure rather than a plain success, explaining that the failure is already reflected in the
run's `failed_count` and is not a new one.

#### Scenario: A status linkage mismatch is reported as a failure, not a silent success

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` set, the envelope's trait/source/blob rows
  are written successfully (`was_noop: false`), but the RPC's returned `status_update_matched` is
  `false`
- **THEN** the command still prints/emits the real, successful write outcome, but then reports a
  failure explaining the status-linkage mismatch, and exits non-zero

#### Scenario: Ingest from a file path

- **WHEN** the user runs `bloomctl cyl ingest-result path/to/scan.result.json` with a valid envelope
  and a profile whose scan is resolvable
- **THEN** the command reads and parses the file, calls the RPC with the envelope under the
  `envelope` argument, and exits zero

#### Scenario: Ingest from stdin

- **WHEN** the user runs `bloomctl cyl ingest-result -` and pipes a valid envelope on stdin
- **THEN** the command reads the envelope from stdin and ingests it identically to the file path
  case

#### Scenario: Unreadable or malformed input

- **WHEN** the path does not exist, the content is not valid JSON, or stdin (`-`) is empty
- **THEN** the command prints a readable error, exits non-zero, and makes no RPC call

#### Scenario: Source-only envelope (no traits or blobs)

- **WHEN** a valid envelope whose `traits` and `blobs` are both empty is ingested and the RPC
  writes only the source row (`trait_count = 0`, `blob_count = 0`)
- **THEN** the command reports success with zero trait/blob counts and exits zero

#### Scenario: ARGO_WORKFLOW_NAME set threads through to the RPC call

- **WHEN** the command runs with the `ARGO_WORKFLOW_NAME` environment variable set (as Argo sets it
  inside the write-back container), ingesting a valid envelope
- **THEN** the RPC call includes `p_argo_workflow_name` equal to that environment variable's value

#### Scenario: ARGO_WORKFLOW_NAME unset omits the parameter, unchanged from prior behavior

- **WHEN** the command runs with `ARGO_WORKFLOW_NAME` unset (the existing manual/local invocation
  shape, e.g. a human running `cyl ingest-result` directly against a scan)
- **THEN** the RPC call omits `p_argo_workflow_name` entirely, and ingestion behaves exactly as it
  did before this parameter existed

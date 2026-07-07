## Why

The A4 sleap-roots pipeline emits a per-scan `{scan}.result.json` `ResultEnvelope`
(`sleap-roots-contracts` `0.1.0a3`), and the `insert_cyl_result_envelope(jsonb)` RPC
(capability `cyl-trait-writeback`) can now ingest it — the a3 re-pin (PR #399, closing #393)
made the RPC's `contract_version` check accept the emitters' bare `0.1.0a3`. But nothing
writes the envelope back to Bloom: `bloomctl` (`bloomcli/src/bloomctl/`) has `login` +
`download` and no ingest path. This change adds the client half of the per-scan write-back —
the roadmap's A2 "CLI" change and A4's write-back step (closes #397).

## What Changes

- Add a **`bloomctl cyl ingest-result <envelope.json | ->`** command (new Click `cyl` group,
  matching the `@cli.group(...)` pattern introduced by the in-flight `list` command, PR #385):
  read a `ResultEnvelope` from a path or stdin, authenticate via an existing credentials
  profile, and call `client.rpc("insert_cyl_result_envelope", {"envelope": <dict>})`.
- **Validate** the envelope against `sleap-roots-contracts` (`ResultEnvelope.model_validate`)
  as a fail-fast gate, then send the **original parsed JSON** to the RPC unchanged (preserves
  the producer's exact `idempotency_key`; no model re-serialization).
- **Idempotent UX**: surface the RPC's first-writer-wins no-op (`was_noop=true`) as a distinct
  "already ingested" success (exit 0), not a scary error.
- **Actionable error surface**: map the RPC's `RAISE EXCEPTION` messages to readable CLI text —
  especially the most likely real-world failure, `inputs.image_ids` not resolving to exactly one
  scan on this server/profile.
- **`--json`** flag emits the RPC's return object (`{source_id, scan_id, trait_count,
  blob_count, was_noop}`) to stdout so the A4 write-back step can capture `source_id`.
- Add `sleap-roots-contracts>=0.1.0a3` to `bloomcli/pyproject.toml` (no `[pandas]` extra).
- **Blobs pass through** to the RPC as-is; the MinIO/Box blob byte-upload (uploading the `.slp`
  bytes + populating the refs) is deferred to a tracked follow-up that will **extend this same
  command** in a later slice (issue filed with this PR; number backfilled here — see tasks
  7.1/9.4).
- Implementation lands in the **same PR** as this proposal (bundled proposal + first phase).

## Impact

- **New capability**: `cyl-ingest-cli` (the CLI surface for per-scan write-back). Consumes the
  existing `cyl-trait-writeback` RPC contract (which owns the RPC's return-object shape and the
  `RAISE EXCEPTION` messages this command maps); no server/RPC/schema changes. **No
  `_WIKI/SUPABASE` change** — the server contract is unchanged.
- **Affected code**: `bloomcli/src/bloomctl/cli.py` (new `cyl` group + `_authed_client` helper,
  shared with #385), new `bloomcli/src/bloomctl/ingest.py`, `bloomcli/pyproject.toml`
  (new dependency + `integration` marker), new tests under `bloomcli/tests/`, a committed
  real-envelope fixture, `bloomcli/README.md` + `bloomcli/CHANGELOG.md`, and the two CI
  invocations + prettier hook (`.github/workflows/{pr-checks,release-bloomcli}.yml`,
  `.pre-commit-config.yaml`).
- **Out of scope** (separate follow-ups): non-interactive scoped auth for the A4 pod (#398);
  `bloom_workflows` role grants (#404); the blob byte-upload to MinIO/Box (new issue filed with
  this PR). Consumer: the A4 write-back step (EPIC `talmolab/sleap-roots-pipeline#10`).
- **Coordination**: touches @blm3886's `bloomctl`/workflows area and overlaps PR #385 on the
  `_authed_client` helper — request his review; branch protection also requires a non-author
  reviewer.

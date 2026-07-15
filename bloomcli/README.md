# bloomctl

Python command-line tool for the Bloom server — download cylinder experiments
(metadata + images), write per-scan pipeline results back, and manage
credentials. Successor to the Node `@salk-hpi/bloom-cli`. Tracked by issue #347.

## Commands

`login` is flat; assay-specific commands are grouped by data type (`cyl`). Each
command is tagged **[read]** or **[write]** — see [Access & roles](#access--roles).

- `bloomctl login` — bootstrap client config from the Bloom server and store
  credentials per profile.
- **[read]** `bloomctl cyl download <out_dir> …` — download a cylinder experiment
  or single scan (metadata `scans.csv` + per-frame images).
- **[write]** `bloomctl cyl ingest-result <envelope>` — write a per-scan pipeline
  `ResultEnvelope` back to Bloom (see below).
- **[read]** `bloomctl cyl datasets list` — list cylinder trait datasets
  (`--experiment-id` to scope to one experiment, `--json` for machine-readable output).
- **[read]** `bloomctl cyl datasets get <name>` — show one dataset's details and the
  unique traits it contains, via the `cyl_dataset_trait_names` view (`--json` output).
- **[write]** `bloomctl cyl datasets create <name> <experiment_id> <trait_source_name>` —
  create a trait dataset (`--qc-set-name` to exclude a QC set, `--timepoints`).

(Full `login`/`cyl download` usage docs are still forthcoming; run any command
with `--help` in the meantime.)

## Access & roles

Commands run **as the logged-in user** — every query and mutation is RLS-enforced
under the caller's role, not a service key. So the role your `bloomctl login`
profile maps to determines what works:

| Command tag | Required role | Intended user |
|---|---|---|
| **[read]** (`download`, `datasets list`) | `bloom_user` (any authenticated user) | anyone with a Bloom account |
| **[write]** (`ingest-result`, `datasets create`) | `bloom_writer` / `bloom_admin` | automated pipelines (e.g. the trait-extraction write-back), or users granted write access |

A read-only `bloom_user` can `list` datasets but **cannot** `create` one — the
write path (the `create_cyl_dataset` / `insert_cyl_result_envelope` RPCs and the
underlying table inserts) is granted to `bloom_writer`/`bloom_admin`. Point the
**[write]** commands at a profile with write access (e.g. the pipeline's service
account); a `bloom_user` login will get a clear permission error.

## `bloomctl cyl ingest-result`

Ingest one per-scan `ResultEnvelope` (emitted by the sleap-roots trait extractor)
into Bloom by calling the `insert_cyl_result_envelope` RPC.

```
bloomctl cyl ingest-result <envelope.json | ->   [-p/--profile PROFILE] [--json]
```

- Reads the envelope from a file path, or from **stdin** when the argument is `-`.
- **Validates** it against `sleap-roots-contracts` before the call (fails fast with
  a readable message) and sends the original JSON unchanged.
- **Idempotent:** re-ingesting the same envelope is a no-op (first-writer-wins on
  the envelope's `idempotency_key`), reported as "already ingested" — not an error.
- `--json` prints the RPC's result object (including `source_id`) to stdout for
  scripting; without it, a human-readable summary line.

The most common real-world error is `inputs.image_ids` not resolving to exactly
one scan on the target server — the command explains that the scan's images must
already exist in `cyl_images` on the Bloom you're pointed at.

Auth: uses your saved login profile, which must have write access
(`bloom_writer` / `bloom_admin`). Non-interactive / scoped credentials for
cluster/CI use are tracked separately (#398).

Examples:

```
bloomctl cyl ingest-result path/to/scan.result.json
cat scan.result.json | bloomctl cyl ingest-result - --json
```

## Dev-stack smoke test

`tests/test_dev_stack_smoke.py` verifies the local Supabase stack is serving and
that `bloomctl cyl ingest-result` can round-trip against it (gateway `/rest`+`/auth`
= 200, the write-back RPC is migrated, and a seed → ingest → no-op → cleanup cycle
succeeds). It self-skips unless `BLOOMCTL_DEV_SMOKE` is set, and is marked
`integration` so the default suite and CI never run it. After `make dev-up`:

```
set -a; . ./.env.dev; set +a
BLOOMCTL_DEV_SMOKE=1 uv run --extra test --with psycopg \
  --project bloomcli pytest bloomcli/tests/test_dev_stack_smoke.py -v
```

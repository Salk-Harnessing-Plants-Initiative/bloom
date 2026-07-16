# bloomctl

Python command-line tool for the Bloom server — download cylinder experiments
(metadata + images), write per-scan pipeline results back, and manage
credentials. Successor to the Node `@salk-hpi/bloom-cli`. Tracked by issue #347.

## Commands

`login` is flat; assay-specific commands are grouped by data type (`cyl`):

- `bloomctl login` — bootstrap client config from the Bloom server and store
  credentials per profile.
- `bloomctl cyl download <out_dir> …` — download a cylinder experiment or single
  scan (metadata `scans.csv` + per-frame images).
- `bloomctl cyl download-for-predict <scan-id> <out>` — stage one scan into the
  predict-ready layout (see below); produces a **different** output tree than
  `cyl download` — use this only for A4 pipeline stage-in.
- `bloomctl cyl ingest-result <envelope>` — write a per-scan pipeline
  `ResultEnvelope` back to Bloom (see below).

(Full `login`/`cyl download` usage docs are still forthcoming; run any command
with `--help` in the meantime. `cyl ingest-result` and `cyl download-for-predict`
are documented in full below.)

## `bloomctl cyl download-for-predict`

Stage one cylinder scan into the layout the warm-predict container expects.
Unlike `cyl download` (which writes `images/Wave{n}/…` + `scans.csv`), this
command co-locates frames with a `scan_metadata.json` sidecar so
`sleap_roots_predict.discover_scans` can find them — use it for A4 per-scan
pipeline stage-in, not as a replacement for `cyl download`.

```
bloomctl cyl download-for-predict <scan-id> <out>   [-p/--profile PROFILE]
```

- Writes frames to `<out>/scan_<scan_id>/<frame_number><ext>`.
- Authors `<out>/scan_<scan_id>/scan_<scan_id>.scan_metadata.json` with:
  - `scan_key` — `scan_<scan_id>` (matches the filename stem).
  - `params` — `{species, mode, age}`, resolved via `sleap-roots-contracts`
    (`mode` is always `"cylinder"`).
  - `image_ids` — real `cyl_images.id` values, required for the write-back RPC
    to resolve the scan (see rationale in design.md / bloom#411).
  - `images_checksum` — `sha256:<hex>` over the downloaded frame bytes.
- Exits non-zero with a readable message if the scan isn't found, has no
  frames, or any frame fails to download — on a frame-download failure, no
  sidecar is written (successfully-downloaded frames remain on disk).
- A successful re-run reconciles away any stray frame file left by an earlier
  failed attempt, so the directory always matches the written sidecar exactly.

Auth: same saved login profile as other `cyl` commands.

Example:

```
bloomctl cyl download-for-predict 1 ./staged
```

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

# bloommcp storage backends — where analysis outputs go

bloommcp analysis tools produce versioned artifacts (the cleaned CSV, a
`cleanup_log.json`, plots, and a `manifest.json` catalog per experiment/tool).
This doc explains **where those bytes actually land**, why the folder you might
expect stays empty, and how to opt into writing them as **real files on disk**.

## Default: Supabase Storage (nothing on your local disk)

By default (`BLOOM_STORAGE_BACKEND` unset or `supabase`), every output is uploaded
to **Supabase Storage**, in the `bloommcp-data` bucket under
`bloommcp_output/<tool_class>_<stem>/v<N>_<date>/…`. There is **no local-file
branch** on the default path.

In local dev, Supabase Storage is backed by **MinIO**, so the bytes live inside
MinIO's object store (under `MINIO_DATA_PATH`, in MinIO's own object format) —
**not** as files under `./bloommcp/data/ANALYSIS_OUTPUT`. That folder is mounted
into the container as `BLOOM_OUTPUT_DIR`, but on the default path nothing writes
new outputs there, which is why it stays empty.

### `BLOOM_OUTPUT_DIR` and `BLOOM_USE_LOCAL` do NOT produce local CSVs

Two env vars look like they'd control this and don't:

- **`BLOOM_OUTPUT_DIR`** — post-migration this only feeds a startup dir-existence
  check and a *legacy read* fallback for pre-migration cleaned CSVs. Nothing
  writes new outputs there. (It *is* reused as the default local root — but only
  when you opt into the `local` backend below.)
- **`BLOOM_USE_LOCAL`** — dead/commented-out, and it was only ever about CLI login
  credentials, never about outputs.

### How to reach outputs on the default path (local dev)

- **MinIO console** — `http://localhost:${MINIO_CONSOLE_PORT}` (default `9001`),
  bucket `bloom-storage` (the underlying MinIO S3 bucket).
- **Supabase Studio** — `http://localhost:${STUDIO_PORT}` (default `55323`),
  Storage → bucket `bloommcp-data`.
- **The bloommcp MCP read tools** (`list_existing_analyses`, `load_experiment_data`, …).

## Opt-in: the `local` backend (real files on disk)

Set `BLOOM_STORAGE_BACKEND=local` to write outputs as real files, laid out by
storage key under a root directory:

```
BLOOM_STORAGE_BACKEND=local
# optional — defaults to BLOOM_OUTPUT_DIR when unset:
BLOOM_STORAGE_LOCAL_ROOT=/app/data/ANALYSIS_OUTPUT
```

Resulting on-disk layout (the storage key becomes the path under the root):

```
<root>/bloommcp_output/qc_<stem>/manifest.json
<root>/bloommcp_output/qc_<stem>/v1_2026-07-02/_cleaned.csv
<root>/bloommcp_output/qc_<stem>/v1_2026-07-02/cleanup_log.json
```

- **Root resolution:** `BLOOM_STORAGE_LOCAL_ROOT` if set, otherwise
  `BLOOM_OUTPUT_DIR` (already required and, in dev, mounted at
  `./bloommcp/data/ANALYSIS_OUTPUT`) — so in dev, `BLOOM_STORAGE_BACKEND=local`
  needs no second variable and finally populates that folder.
- **Same semantics as Supabase:** manifest/versioning are unchanged; the backend
  writes atomically (temp file + rename), overwrites in place, copies bytes
  verbatim (so the recorded `output_sha256` matches the file on disk), and reads
  resolve back through the same manifest/versioned-cleaned path.
- **Read paths work too:** manifest resolution, the versioned-cleaned lookup, and
  the MCP read tools all resolve against the local files.

To enable it in dev, uncomment the two lines in the `bloommcp` service env block
of `docker-compose.dev.yml` and restart the service.

### ⚠️ Do not mix backends for one experiment

A backend is **not a migration** — the two stores are independent catalogs with
no cross-store view. If you run some versions of an experiment under `supabase`
and then flip to `local` (or vice-versa), the second store starts a fresh catalog
and re-allocates `v1`: version ids collide and each store's `latest` points at a
different lineage. A later read sees only the store the current backend points at
and is blind to the other's versions. **Pick one backend per experiment and keep
it stable** for the life of that experiment's analysis history.

## Scope

This backend selection governs **object storage only**. PostgREST/table reads
(`get_postgrest_client`, `read_input_csv`) and raw-experiment-input reads from the
local `BLOOM_TRAITS_DIR` are unaffected by `BLOOM_STORAGE_BACKEND`. Production and
staging stay on Supabase Storage; `local` is opt-in for local/dev.

Related: this reshapes the same `supabase_client.py` storage boundary that #388
(user-facing upload/download of bloommcp files) will build signed-URL downloads
on; the two are independent.

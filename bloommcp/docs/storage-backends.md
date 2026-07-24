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

> `./bloommcp/data/{TRAITS_DIR,ANALYSIS_OUTPUT,PLOTS_DIR}` are provisioned
> automatically by `make dev-up` — no manual `mkdir`/`chmod` needed. See
> [DEV_SETUP.md](../../DEV_SETUP.md#bloommcp-data-directories).

### `BLOOM_OUTPUT_DIR` and `BLOOM_USE_LOCAL` do NOT produce local CSVs

Two env vars look like they'd control this and don't:

- **`BLOOM_OUTPUT_DIR`** — post-migration this only feeds a startup dir-existence
  check and a _legacy read_ fallback for pre-migration cleaned CSVs. Nothing
  writes new outputs there. (It _is_ reused as a fallback local root — but only
  when you opt into the `local` backend below, and only as the last-resort tier;
  see the precedence table there.)
- **`BLOOM_USE_LOCAL`** — dead/commented-out, and it was only ever about CLI login
  credentials, never about outputs.

### How to reach outputs on the default path (local dev)

- **MinIO console** — `http://localhost:${MINIO_CONSOLE_PORT}` (default `9001`),
  bucket `bloom-storage` (the underlying MinIO S3 bucket).
- **Supabase Studio** — `http://localhost:${STUDIO_PORT}` (default `55323`),
  Storage → bucket `bloommcp-data`.
- **The bloommcp MCP read tools** (`list_existing_analyses`, `load_experiment_data`, …).

## Opt-in: the `local` backend (real files on disk)

Set `BLOOM_STORAGE_BACKEND=local` to run fully offline — local input, local
output, no Supabase boot gate. Three subpaths resolve independently, each with
the same 3-tier precedence (highest wins):

| Subpath | 1. Explicit override          | 2. `BLOOM_LOCAL_ROOT`-derived | 3. Legacy fallback                     |
| ------- | ----------------------------- | ----------------------------- | -------------------------------------- |
| Input   | `BLOOM_EXPERIMENT_LOCAL_ROOT` | `<BLOOM_LOCAL_ROOT>/input`    | `BLOOM_TRAITS_DIR`                     |
| Output  | `BLOOM_STORAGE_LOCAL_ROOT`    | `<BLOOM_LOCAL_ROOT>/output`   | `BLOOM_OUTPUT_DIR` (deprecated bridge) |
| Plots   | `BLOOM_PLOTS_DIR`             | `<BLOOM_LOCAL_ROOT>/plots`    | _(none — required)_                    |

`BLOOM_LOCAL_ROOT` is inert unless `BLOOM_STORAGE_BACKEND=local`; tier 3 is
unchanged if you never set it — this is purely additive, for anyone who wants
the split, three-variable setup this replaces for the common case.

**Two ways to use it:**

Inside `docker-compose.dev.yml` (container paths — needs its own bind mount if
you point `BLOOM_LOCAL_ROOT` somewhere not already mounted, or the
auto-created subfolders below won't survive `docker compose down`):

```
BLOOM_STORAGE_BACKEND: local
BLOOM_LOCAL_ROOT: /app/data/LOCAL_ROOT
```

Running `bloommcp` directly — e.g. Claude Desktop / Claude Code offline, no
Docker (host paths):

```
BLOOM_STORAGE_BACKEND=local
BLOOM_LOCAL_ROOT=/Users/you/bloommcp-data
```

Drop input CSVs in `bloommcp-data/input/`; outputs and plots appear under
`bloommcp-data/output/` and `bloommcp-data/plots/` — one folder to create by
hand, nothing else to pre-create (see auto-create below).

Resulting on-disk output layout (the storage key becomes the path under the
output root):

```
<output root>/bloommcp_output/qc_<stem>/manifest.json
<output root>/bloommcp_output/qc_<stem>/v1_2026-07-02/_cleaned.csv
<output root>/bloommcp_output/qc_<stem>/v1_2026-07-02/cleanup_log.json
```

- **Auto-create:** only the top-level `BLOOM_LOCAL_ROOT` folder must pre-exist
  and be writable — boot fails fast if it doesn't. Its three subfolders
  (`input/`, `output/`, `plots/`) are created automatically at boot if missing.
  An **explicitly-set** granular var (`BLOOM_EXPERIMENT_LOCAL_ROOT` /
  `BLOOM_STORAGE_LOCAL_ROOT` / `BLOOM_PLOTS_DIR`) keeps the stricter
  "must already exist" contract — auto-create applies only to the
  `BLOOM_LOCAL_ROOT`-derived default, so a typo'd override still fails loudly
  instead of silently creating a directory at the wrong path.
- **Same output semantics as Supabase:** manifest/versioning are unchanged; the
  backend overwrites in place, copies bytes verbatim (so the recorded
  `output_sha256` matches the file on disk), and reads resolve back through the
  same manifest/versioned-cleaned path.
- **Atomic writes (POSIX):** on POSIX filesystems the backend writes a temp file,
  `fsync`s it, then `os.replace`s it into place, so a crash mid-write never leaves
  a truncated `manifest.json`. **On Windows/NTFS** `os.replace` over an existing
  file is **not** guaranteed atomic (and can fail if a reader holds the target
  open) — acceptable for this dev-only backend, but don't rely on crash-atomicity
  there. (Crash-atomic, not power-loss-durable beyond a best-effort dir `fsync`.)
- **Inputs via `LocalReader`.** `LocalReader` implements the same
  `ExperimentReader` contract as the Supabase path (same declared roles, same
  `pd.read_csv` config, same resolution order), reaches no Supabase, and
  rejects any experiment name that escapes its input root.
- **Backend-aware boot.** In `local` mode `server.main()` skips
  `validate_supabase_env()` and validates the local input root instead.
  `BLOOM_TRAITS_DIR` / `BLOOM_OUTPUT_DIR` / `BLOOM_PLOTS_DIR` are required
  **unless** `BLOOM_LOCAL_ROOT` is also set, in which case only
  `BLOOM_LOCAL_ROOT` itself must exist and be writable — an invalid
  `BLOOM_STORAGE_BACKEND` value still fails fast in every mode. Production and
  staging never set `local`, so their boot fail-fast is unchanged.
- **Reader/store are coupled.** `LocalReader` is wired only when the object-storage
  backend is also `local`, so a run can't read raw inputs locally while resolving
  cleaned outputs from Supabase (a split lineage). A mismatch is rejected at boot.
- **`require_clean` ignores the un-versioned legacy CSV.** The local reader will
  not satisfy a certified-clean consumer (e.g. PCA) from the un-versioned legacy
  `qc_<stem>/<stem>_cleaned.csv` — it has no manifest/hash lineage and may not match
  the current input; only a versioned, manifest-backed cleaned output qualifies.
- **Read paths work too:** manifest resolution, the versioned-cleaned lookup, and
  the MCP read tools all resolve against the local files.

To enable it in dev, uncomment the storage-backend lines in the `bloommcp` service
env block of `docker-compose.dev.yml` and restart the service.

### ⚠️ Do not mix backends for one experiment

A backend is **not a migration** — the two stores are independent catalogs with
no cross-store view. If you run some versions of an experiment under `supabase`
and then flip to `local` (or vice-versa), the second store starts a fresh catalog
and re-allocates `v1`: version ids collide and each store's `latest` points at a
different lineage. A later read sees only the store the current backend points at
and is blind to the other's versions. **Pick one backend per experiment and keep
it stable** for the life of that experiment's analysis history.

**This is a dev / power-user path, not a normal-user packaged distribution.**
Bench scientists use the deployed web product; fully-local mode is for driving
bloommcp directly from Claude Code / Claude Desktop offline. Packaging it for
non-technical users (a Claude Desktop bundle / installer) is a separate decision.

## Scope

PostgREST/table reads (`get_postgrest_client`, `read_input_csv`) remain **out of
scope** of `BLOOM_STORAGE_BACKEND` — they are the database, not the experiment-read
port. The fully-local `qc_clean → pca_analysis` path does not touch them (the store
commit path uses only object-storage helpers routed through the active backend), so
it is Supabase-free; a tool that reads a database table is not part of that path.
Production and staging stay on Supabase; `local` is opt-in for local/dev.

Related: this reshapes the same `supabase_client.py` storage boundary that #388
(user-facing upload/download of bloommcp files) will build signed-URL downloads
on; the two are independent.

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

## Downloading outputs: signed URLs (`output_links`)

Every consumer tool (`qc_clean`, `qc_inspect`, `pca_analysis`, `remove_outliers`,
`descriptive_stats`, `cross_experiment_correlations`, `umap_analysis`, `clustering`)
returns an `output_links` field alongside its existing `outputs` (object-key) field:
one entry per output, each carrying a downloadable `url`, the artifact's `sha256`
(matching the manifest's `output_sha256` — verify what you downloaded against it),
and its `size_bytes`. This is populated only on the result a tool call itself
returns — resolving or listing a prior run (`list_existing_analyses`, or reading
`get_run`/`list_runs` through the `ResultStore` port) never carries signed links for
artifacts other than the one just committed.

### Re-fetching a link for an already-committed run (`get_download_links`)

A signed URL expires after an hour, and a chat session can end before it's used.
The `get_download_links(experiment, tool_class, run_ref="latest")` MCP tool
(bloom#599) re-signs fresh links for a run you already know about — from a prior
`list_existing_analyses` call, or a tool response from a now-expired session —
for both the run's per-output `output_links` and a `manifest_url` for the run's
own `manifest.json` (bloom#600). Four things worth knowing before you reach for it:

- **It must be called by name for one already-known run** — it is not a browsing
  or discovery feature. There is still no way to list or browse every historical
  run across an experiment/tool_class beyond what `list_existing_analyses` already
  shows; a general file-explorer over Supabase Storage remains out of scope (the
  `#388` file-explorer third mentioned at the bottom of this doc).
- **A single output's lookup failure aborts the whole call** — you get either every
  output's link, or a clean error; never a partially-populated `output_links` that
  silently omits one output. A failure signing the manifest itself aborts the call
  identically.
- **A legacy run recorded before per-artifact keys existed** (a v2 manifest entry)
  has nothing to sign for its outputs — `output_links` comes back empty for it, not
  an error — but `manifest_url` is still populated: a run's manifest always exists
  once committed, regardless of whether per-artifact output keys were ever recorded
  for it, so this is never gated on `output_links` being non-empty.
- **`manifest_url` has no `sha256`/`size_bytes` counterpart** — unlike each
  `output_links` entry, it is a bare signed link (the manifest has no persisted
  self-hash to verify against); fetch and read the manifest itself if you need its
  contents.

Unlike the per-tool `output_links` above, `get_download_links`'s `size_bytes` is
resolved via a live storage lookup on every call — nothing about a run's size is
ever cached or persisted in the manifest. This relies on one assumption: that a
committed output's bytes are never mutated out-of-band after commit (true under
bloommcp's normal write path — each version directory is written once, never
overwritten). If an object were ever replaced behind bloommcp's back (a storage
incident, a manual admin fix), the freshly-queried `size_bytes` could describe
different bytes than the manifest's own `sha256` — always verify what you
download against the returned `sha256`, which is unaffected by this and comes
from the same immutable record `output_links` above already relies on.

Backed by `StorageBackend.create_signed_url(key, expires_in)` — a 3600-second
expiry (the `SIGNED_URL_EXPIRES_SECONDS` constant in
`bloom_mcp/result_store/_artifacts.py`), not configurable per call.

- **Supabase backend (default):** a real, time-limited signed URL from Supabase
  Storage's own signing call. Because `SUPABASE_URL` points at the internal Docker
  network host (`http://kong:8000` in staging/production — unreachable from
  outside it), the signed URL's host is rewritten onto `BLOOM_PUBLIC_SUPABASE_URL`
  (set to `${NEXT_PUBLIC_SUPABASE_URL}` in both compose files) before it's
  returned — the same internal-host-rewrite pattern `services/workflows/video.py`
  and `web/lib/supabase/storage-url.ts` already use for their own signed URLs. A
  missing `BLOOM_PUBLIC_SUPABASE_URL` is a silent no-op (the raw internal-host URL
  is returned unchanged) — harmless in local dev (nothing outside the Docker
  network needs to resolve it there), a real gap in a deployed environment.
- **Local backend (opt-in):** a served URL built from `BLOOM_STORAGE_URL`
  (`f"{BLOOM_STORAGE_URL}/{key}"`), ignoring the expiry — this is a dev-only
  convenience with no real credential enforcement, matching this backend's other
  documented caveats. **bloommcp does not itself serve `BLOOM_STORAGE_LOCAL_ROOT`
  over HTTP** — `BLOOM_STORAGE_URL` only supplies the base URL for something an
  operator has separately stood up to do that (mirroring how `BLOOM_PLOTS_URL`
  already assumes an existing server for `PLOTS_DIR`). Without it configured,
  `create_signed_url` raises rather than fabricate a `file://` URI.

**`create_signed_url` itself performs no ownership check.** It's a generic signing/
serving primitive — given a key, it signs it, with no concept of which experiment
or run that key belongs to. The guarantee that only a run's own freshly-uploaded
keys ever get signed lives one layer up, in `ResultStore.commit()`: before
building any `output_links` entry, `commit()` verifies every key falls within the
prefix it itself just computed for that run, rejecting (and cleaning up, same as
any other commit failure) anything outside it. Every current caller already only
ever passes correctly-scoped keys by construction — this guard is defense-in-depth
against a future bug, not a fix for a live gap.

**Inline-vs-link size threshold: 100 KB, documentation-only.** No bloommcp tool
inlines output content in its response regardless of size — every consumer
tool's docstring documents a deliberate "links, not blobs" contract, and this
threshold does not change that. It exists so a caller deciding whether to fetch
`output_links[...].url` and show its contents inline (versus just linking to it)
has both a concrete number and the `size_bytes` data needed to apply it
themselves.

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
  same manifest/versioned-cleaned path. One difference: a Supabase-backed
  `output_links[...].url` always resolves; the local backend's constructed URL
  only resolves if `BLOOM_STORAGE_URL`'s root is separately being served over
  HTTP (see "Downloading outputs" above) — bloommcp doesn't run that server.
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

To enable it in dev, run `make dev-up-local` — it brings the stack up in
fully-local mode for one invocation (`BLOOM_STORAGE_BACKEND=local` set only for
that run) without touching `.env.dev`. The `bloommcp` service in
`docker-compose.dev.yml` sources these vars via `${VAR:-}` interpolation — it no
longer needs to be edited to toggle this. You can also set
`BLOOM_STORAGE_BACKEND=local` directly in your own `.env.dev`, but prefer the
one-shot `make dev-up-local` form: a value left set in `.env.dev` (or exported in
a shell profile) silently applies to every subsequent plain `make dev-up` too,
with no prompt or confirmation.

`docker-compose.dev.yml` uses a single, fixed compose project name
(`bloom_v2_dev`) for the whole dev stack — the same one `make dev-up` uses. If
someone else (or another terminal) already has the dev stack running on this
machine, `make dev-up-local` recreates those same containers into fully-local
mode rather than starting an independent stack; it isn't isolated per-invocation.

### ⚠️ Do not mix backends for one experiment

A backend is **not a migration** — the two stores are independent catalogs with
no cross-store view. If you run some versions of an experiment under `supabase`
and then flip to `local` (or vice-versa), the second store starts a fresh catalog
and re-allocates `v1`: version ids collide and each store's `latest` points at a
different lineage. A later read sees only the store the current backend points at
and is blind to the other's versions. **Pick one backend per experiment and keep
it stable** for the life of that experiment's analysis history.

This can't be *prevented* from purely local information — the `local` backend
runs fully offline and has no way to check whether `supabase` already has
history for an experiment (and vice versa) without contacting it, which would
defeat the point. It is made **observable** instead (#395):

- Every `manifest.json` records a `storage_backend` field naming whichever
  backend most recently wrote it, so inspecting either store's file directly
  identifies which backend produced it.
- The first commit for an (experiment, tool_class) pair under a given
  backend — i.e. allocating a fresh catalog, `v1` — logs an info-level
  message naming the experiment, tool class, and active backend. It's `info`,
  not `warning`: this fires on every brand-new experiment's first commit too
  (the common, non-mixing case), and warning-level would page on-call for
  routine new-experiment onboarding.

**Known limitation:** the signal only fires when a backend's own catalog
doesn't exist yet. Flipping `supabase` → `local` → `supabase` logs on the
first flip (`local` starts fresh) but **not** on the return trip (`supabase`'s
manifest already exists), even though a `local`-backed run happened in
between and `supabase`'s catalog is now silently stale relative to it. Neither
the sentinel nor the log line can join the two catalogs — they only make the
*moment* of a potential split observable, not the mixing itself.

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

Related: this reshapes the same `supabase_client.py` storage boundary #388
(user-facing upload/download of bloommcp files) is scoped against. Its
"return output CSVs" third has landed — see "Downloading outputs" above; the
ad-hoc upload and file-explorer thirds remain open, tracked separately.

# Tool-call results storage

This doc explains how bloommcp saves analysis results (and how it reads
them back). If you are about to write a new workflow tool start here.

The storage layer lives under
[bloommcp/src/bloom_mcp/storage/](../../bloommcp/src/bloom_mcp/storage/). Everything below is a view
of those files plus the two callers that actually use them:
[`build_writer`](../../bloommcp/src/bloom_mcp/tools/workflows/_helpers.py) (write
side) and
[`list_existing_analyses`](../../bloommcp/src/bloom_mcp/tools/storage_tools.py) (read
side).

## Why this exists

A bloommcp workflow tool (say, `run_qc_workflow`) takes a CSV of plant traits, does something to it (QC, stats, PCA, clustering), and produces
output files. We want three things from those outputs:

1. **Persistence** - they outlive the container, so the agent can refer to "the QC run we did yesterday."
2. **Versioning** - every re-run gets its own `v<N>` folder. We never overwrite a previous run.
3. **Provenance** - for each run we record the tool, its params, the input file's SHA-256, and the bloommcp version that produced it.

All three live in Supabase Storage, in the `bloommcp-data` bucket. 

The storage layer is the thin Python wrapper that makes writing those three things feel like writing to a local folder.

## The big picture

The bucket has exactly two top-level prefixes:

```
bloommcp-data/                          <- Supabase Storage bucket
├── bloommcp_input/                     <- flat, LLM-supplied raw CSVs
│   └── plant_traits.csv
└── bloommcp_output/                    <- versioned analysis outputs
    └── qc_plant_traits/                <- one folder per (tool_class, experiment)
        ├── manifest.json               <- cumulative catalog
        ├── v1_2026-06-05_initial/
        │   └── _cleaned.csv
        ├── v2_2026-06-05_tighter/
        │   └── _cleaned.csv
        └── v3_2026-06-05_relabelled/
            └── _cleaned.csv
```

Two rules to internalise:

- **Inputs are flat.** A raw CSV
  `bloommcp_input/<filename>`. No subfolders, no versions.
- **Outputs are nested by `(tool_class, experiment_stem)`.**
- All QC runs on `plant_traits.csv` live in `bloommcp_output/qc_plant_traits/`. All
  stats runs on the same file live in `bloommcp_output/stats_plant_traits/`.
- They never mix.

`tool_class` is one of the strings in `CANONICAL_TOOL_CLASSES` in
[`storage/__init__.py`](../../bloommcp/src/bloom_mcp/storage/__init__.py): `qc`, `stats`, `dimred`, `clustering`, `outlier`, `viz`, `correlation`,
`heritability`, `anova`. 

The experiment stem is `Path(filename).stem` — `plant_traits.csv` → `plant_traits`.

## Inside one analysis folder

Every `bloommcp_output/<tool_class>_<stem>/` folder has the same shape:

- One `manifest.json` at the top — the cumulative catalog of every run
  that has ever happened for this `(tool_class, experiment)` pair.
- One subfolder per run, named `v<N>_<YYYY-MM-DD>[_<slug>]/` (see
  `version_dir_name()` in [`versioning.py`](../../bloommcp/src/bloom_mcp/storage/versioning.py)).
- The optional slug is a 32-char-max lowercase `_`-separated version of the
  `user_label`.
- Inside each subfolder, whatever output files that run produced.
  `_cleaned.csv` is the convention for QC; other tool classes write
  their own files.

`v<N>` is monotonic and never reused. If you delete `v2/` from the
bucket by hand, the next run is still `v3`. That logic lives in
`next_version_id()` in
[`versioning.py`](../../bloommcp/src/bloom_mcp/storage/versioning.py) — it scans the
manifest, finds the max `N`, returns `v<max+1>`.

## The four schema models

`manifest.json` is a strict Pydantic-validated document. 

The models live in [`schema.py`](../../bloommcp/src/bloom_mcp/storage/schema.py) and all inherit from `_StrictModel`, which sets `extra="forbid"`.

That means if a writer accidentally adds a field that isn't in the schema, `model_validate`
raises a `ValidationError` instead of silently writing garbage.

**`Manifest`** is the whole JSON file. It has four fields: `manifest_schema_version` (currently `2`, the constant `CURRENT_SCHEMA_VERSION`), `experiment` (an `ExperimentBlock`), `versions` (a list of `VersionEntry`), and `latest` (the `id` of the most recent version, or `None` if there are no runs yet).

**`ExperimentBlock`** identifies *which* experiment this manifest catalogs.It has `filename` (e.g. `plant_traits.csv`), `source_path` and `input_sha256` (a stream-hashed digest of the source CSV, so we can detect if the input ever changed under us).

**`VersionEntry`** is one analysis run — one entry per tool call,
appended to `Manifest.versions`.

| Field | What it stores |
|---|---|
| `id` | `"v1"`, `"v2"`, … — the run's identifier |
| `created_at` | UTC timestamp, ISO-8601 ending in `Z` |
| `tool` | The tool function name (e.g. `"run_qc_workflow"`) |
| `params` | Dict of whatever args the tool was called with |
| `based_on_version` | Lineage pointer (hardcoded `"raw"` today) |
| `code_versions` | A `CodeVersions` block — package versions at write time |
| `outputs` | Dict mapping a short name (`"cleaned"`) to a path relative to the version dir (`"_cleaned.csv"`) |
| `user_label` | Optional human-readable tag the LLM passed in |
| `version_dir` | The on-disk folder name for this run (e.g. `"v3_2026-06-07_my_label"`) |

**`CodeVersions`** captures the installed package versions at write
time, so months later you can tell which release of the code produced a
given output. 

Today it has exactly two fields: `bloommcp` and `supabase` (defaulting to `"unknown"` if the package isn't installed).

Concrete example of a `manifest.json` after one run:

```json
{
  "manifest_schema_version": 2,
  "experiment": {
    "filename": "plant_traits.csv",
    "source_path": "/app/data/SLEAP_OUT_CSV/plant_traits.csv",
    "input_sha256": "abc123..."
  },
  "versions": [
    {
      "id": "v1",
      "created_at": "2026-06-05T12:34:56Z",
      "tool": "run_qc_workflow",
      "params": {"threshold": 0.1},
      "based_on_version": "raw",
      "code_versions": {"bloommcp": "0.1.0", "supabase": "2.31.0"},
      "outputs": {"cleaned": "_cleaned.csv"},
      "user_label": "initial_run",
      "version_dir": "v1_2026-06-07_initial_run"
    }
  ],
  "latest": "v1"
}
```

## The write flow

> **Adding a new workflow tool?** Use the guide in [writing-a-new-tool.md](./writing-a-new-tool.md). 
>
> This section documents what happens *under the hood* during a commit.

Every workflow tool goes through the same machinery:

[`build_writer`](../../bloommcp/src/bloom_mcp/tools/workflows/_helpers.py) constructs an `AnalysisWriter`, the tool calls `create_version()` to get a tmp
staging directory, writes outputs into it, then calls `commit({...})` to upload and register.

What `commit()` does, step by step (see
[`writer.py`](../../bloommcp/src/bloom_mcp/storage/writer.py)):

1. Hashes `source_csv` if it exists (cached on `AnalysisDir`, so re-runs
   don't re-hash).
2. Walks the `outputs` dict and calls `upload_file(key, local_path)` for
   each one. The key is composed via `AnalysisDir.key()` — e.g.
   `bloommcp_output/qc_plant_traits/v1_2026-06-05_initial/_cleaned.csv`.
3. Builds a `VersionEntry` with `code_versions` from
   `get_code_versions()` and `created_at` set to the current UTC time.
4. Reads the existing manifest (or constructs a fresh `Manifest` if
   absent), appends the new entry, updates `latest`.
5. Calls `write_manifest(prefix, manifest)`, which serializes to JSON
   and uploads with `upsert: "true"`.
6. `shutil.rmtree`s the tmp staging dir. Each `AnalysisWriter` commits
   exactly once — calling `commit()` twice is a bug; calling it before
   `create_version()` raises `RuntimeError`.

If the process dies between `create_version()` and `commit()`, the
staging dir is best-effort cleaned by `__del__`. Nothing has been
uploaded to Supabase yet, so the bucket is unchanged.

## The read flow

Reads only touch the manifest. The two main read entry points are
`AnalysisDir.list_versions()` and `AnalysisDir.get_version(version_id)`
in [`analysis_dir.py`](../../bloommcp/src/bloom_mcp/storage/analysis_dir.py).

When the agent calls the MCP tool
`list_existing_analyses(experiment_filename)` (in
[`storage_tools.py`](../../bloommcp/src/bloom_mcp/tools/storage_tools.py)):

1. The tool iterates over every `tool_class` in its registry.
2. For each, it constructs an
   `AnalysisDir(OUTPUT_DIR, experiment_filename, tool_class)`.
3. `AnalysisDir.list_versions()` calls `read_manifest(self.path)`,
   which:
   - calls `list_prefix(prefix)` to check whether `manifest.json` exists
     at all (absence is normal — not every tool class has been run on
     every experiment),
   - calls `read_json(key)` to download the JSON,
   - calls `validate_schema(raw)` to reject manifests with a newer
     `manifest_schema_version` than this code understands,
   - parses with `Manifest.model_validate(raw)`.
4. Returns the versions sorted by `created_at`.
5. The tool aggregates everything and returns one JSON blob to the LLM.

A `ManifestSchemaError` from any tool class is caught and reported in an
`errors` field — one bad manifest doesn't take down the whole listing.
A missing manifest (no runs yet) returns an empty list, not an error.

`load_experiment_data` in
[`source/experiment_utils.py`](../../bloommcp/src/bloom_mcp/experiment_utils.py)
is the other big reader. It uses `AnalysisDir.get_version("latest")` to
resolve the most recent cleaned CSV without the caller having to know
its version id.

## The single-writer assumption

> **Warning.** `AnalysisWriter` has no `fcntl.flock` and no
> compare-and-swap on the manifest. The whole storage layer assumes
> there is exactly **one** bloommcp container per environment (one in
> `staging`, one in `prod`) and **one** FastMCP process inside it. If
> you run two writers against the same Supabase project at the same
> time, they can both allocate the same `v<N>` and the second
> `commit()` will silently clobber the first's manifest entry.

This is OK today because:

- bloommcp is deployed once per env via docker-compose. There is no
  horizontal scaling.
- FastMCP serialises tool calls inside one process, so within one
  container two workflows can't race.
- The Supabase Storage API does support `If-Match` ETags for
  compare-and-swap, but we don't use them yet.

This assumption would break if:

- We ever ran two bloommcp containers against the same Supabase project
  (e.g. blue/green deploy without separating the buckets).
- Someone added a background worker that also writes to
  `bloommcp_output/`.
- A human runs the writer from a notebook against prod while the
  container is also serving tool calls.

If any of those become realistic, add ETag-based CAS in
`write_manifest()` and a retry loop around `commit()`.

## Extending the schema

There are two distinct kinds of change. Don't confuse them.

**Adding a field to `CodeVersions`.** Do this when you start depending
on a new pip-installed package whose version actually changes the
output. Add the field with a `= "unknown"` default in
[`schema.py`](../../bloommcp/src/bloom_mcp/storage/schema.py), then add a
`_version_or_unknown("<package>")` call to `get_code_versions()` in
[`code_versions.py`](../../bloommcp/src/bloom_mcp/storage/code_versions.py). Because
the default is `"unknown"`, old manifests still parse — no schema bump
needed.

Do **not** add a field for something vendored or not pip-installed.
That's why `sleap_roots_analyze` was removed: it always read as
`"unknown"`, which is noise.

**Bumping `CURRENT_SCHEMA_VERSION`.** Bump it when:

- you remove or rename a field that already exists in old manifests on
  disk,
- you add a required field with no default,
- or you want to mark a semantic boundary (the storage-migration bump
  from `1` to `2` is the precedent).

Do **not** bump for:

- adding an optional field with a default,
- changing a docstring,
- internal refactors that don't touch the JSON shape.

Decision tree:

```
Did the JSON shape change in a way that an old reader would mis-parse?
  ├── No  -> do not bump
  └── Yes -> bump CURRENT_SCHEMA_VERSION, and write a migration note in the PR.
```

The `validate_schema()` function in
[`manifest.py`](../../bloommcp/src/bloom_mcp/storage/manifest.py) rejects manifests
whose version is *newer* than this code knows about — so bumping the
constant is also the signal that this code can read the new shape.

## Conventions

A short reference of the naming and composition rules you will trip
over otherwise.

| Concept                 | Rule                                                                | Source                                                          |
| ----------------------- | ------------------------------------------------------------------- | --------------------------------------------------------------- |
| Bucket name             | `bloommcp-data` (hardcoded constant)                              | [`supabase_client.py`](../../bloommcp/src/bloom_mcp/supabase_client.py) |
| Input prefix            | `bloommcp_input/` (flat)                                          | same                                                            |
| Output prefix           | `bloommcp_output/`                                                | same                                                            |
| Analysis folder         | `<output_root>/<tool_class>_<stem>/`                              | `AnalysisDir.__init__`                                        |
| Version folder          | `v<N>_<YYYY-MM-DD>[_<slug>]`                                      | `version_dir_name()`                                          |
| Slug                    | lowercase,`[a-z0-9_]` only, max 32 chars, edges stripped of `_` | `slugify()`                                                   |
| Version id              | `v<N>`, monotonic, never reused                                   | `next_version_id()`                                           |
| `created_at`          | UTC, ISO-8601, seconds precision, ends in `Z`                     | `AnalysisWriter.commit`                                       |
| Object key              | `AnalysisDir.key(rel)` → `<path><rel>`                         | `analysis_dir.py`                                             |
| Manifest path           | `<analysis_dir>/manifest.json`                                    | `manifest.py`                                                 |
| `tool_class` registry | `CANONICAL_TOOL_CLASSES` in `storage/__init__.py`               | `__init__.py`                                                 |
| Upsert flag             | the literal**string** `"true"`, not the bool `True`       | required by the Supabase SDK's `file_options`                 |

## Where to look next

- **Writing a new tool that uses this layer?** Follow the recipe in
  [writing-a-new-tool.md](./writing-a-new-tool.md). This doc is the
  reference; that doc is the action.
- The role + RLS policies that gate writes to `bloommcp-data` live in
  [`_WIKI/SUPABASE/README.md`](../SUPABASE/README.md).

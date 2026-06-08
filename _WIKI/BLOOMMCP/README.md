# bloommcp

Reference for the bloommcp service:

What bloommcp is

A Python-based [Model Context Protocol](https://modelcontextprotocol.io) server, implemented on top of [FastMCP](https://github.com/jlowin/fastmcp).
It exposes plant-phenotyping analysis tools to LLM clients (today: the Langchain-agent running in this same stack) over MCP's streamable-HTTP transport on port 8811.

Concretely it ports analysis routines from Elizabeth Berrigan's  `sleap-roots-analyze` workflow into MCP tools (QC, outlier detection, descriptive stats, PCA/UMAP, clustering, correlation, heritability, ANOVA).

The LLM picks a workflow tool, bloommcp runs the analysis, and returns a structured payload — manifest path, summary stats, and a plot URL when applicable.

## Repository layout

```text
bloommcp/
├── Dockerfile
├── pyproject.toml             # dependencies, including supabase, fastmcp,
│                              # pandas, scipy, scikit-learn, statsmodels,
│                              # matplotlib, umap-learn
├── uv.lock
├── server.py                  # FastMCP entry point. Validates env,
│                              # registers tools, exposes /health.
├── data/                      # runtime artifacts (gitignored bind mount)
│   ├── SLEAP_OUT_CSV/         # input CSVs from upstream pipelines
│   ├── ANALYSIS_OUTPUT/       # versioned output of each workflow tool
│   └── PLOTS_DIR/             # plots served at /plots by langchain-agent
├── source/                    # analysis primitives (port of sleap-roots-analyze)
│   
│   
│   
│   ├── outlier_visualization.py
│   ├── pca.py
│   ├── trait_statistics.py
│   ├── umap_embedding.py
│   ├── cluster_visualization.py
│   ├── clustering.py
│   ├── cross_experiment_correlations.py
│   ├── visualization.py
│   ├── experiment_utils.py
│   └── supabase_client.py   
├── storage/   
└── tools/
    ├── qc_tools.py
    ├── viz_tools.py
    ├── correlation_tools.py
    ├── storage_tools.py       # list_existing_analyses (always-on)
    └── workflows/             # the consolidated workflow tools (in flight)
        ├── qc.py
        ├── stats.py
        ├── clustering.py

        ├── dimred.py
        └── outlier.py
```

## Storage

bloommcp uses a dedicated S3 bucket on Supabase, `bloommcp-data`, for
CSV exchange with the tools. It has two folders:

```text
bloommcp-data/
├── bloommcp_input/    ← input CSVs the tools consume
└── bloommcp_output/   ← output CSVs the tools produce
```

Any new tool that reads or writes a CSV should use this bucket.

## File reading and writing

Use the helper in `bloommcp/source/supabase_client.py` — don't call
`supabase.create_client()` directly:

```python
from source.supabase_client import read_input_csv, write_output_csv

df = read_input_csv("plant_traits.csv")
# reads bloommcp-data/bloommcp_input/plant_traits.csv

write_output_csv("results.csv", df)
# writes bloommcp-data/bloommcp_output/results.csv
```

Pass a basename — no slashes. The helper prepends the right folder, so
the input/output split is enforced in code.

## Supabase data access

bloommcp is signed in as the `bloom_agent` Postgres role via the JWT in
`BLOOM_AGENT_KEY`. The role can read every `public.*` table but cannot
write to any of them — writes go through the storage bucket above.

The helper's `get_postgrest_client()` returns a fully authenticated
PostgREST client:

```python
from source.supabase_client import get_postgrest_client

client = get_postgrest_client()

# Read any public.* table
species = client.table("species").select("*").execute()
plants = client.table("plants").select("id, accession_id, sown_at").eq("experiment_id", 42).execute()
traits = client.table("cyl_scan_traits").select("trait_id, value").limit(1000).execute()
```

See [`_WIKI/SUPABASE/README.md`](../SUPABASE/README.md) for the full
role / RLS picture.

## Coding style for tool calls

Every workflow tool writes using the heper functions fom the AnalysisWriter Class. 

 `bloommcp/storage/AnalysisWriter` —
that's the versioned write contract. Each `(experiment, tool_class)`
pair gets one directory with a `manifest.json` cataloging every run on
that experiment, plus a subdirectory per version:

```text
ANALYSIS_OUTPUT/
└── qc_my_experiment/
    ├── manifest.json
    ├── v1_2026-06-05_initial_run/
    │   └── _cleaned.csv
    └── v2_2026-06-05_relabelled/
        └── _cleaned.csv
```

Tool classes are the canonical set in
`bloommcp.storage.CANONICAL_TOOL_CLASSES`: `qc`, `stats`, `dimred`,
`clustering`, `outlier`, `viz`, `correlation`, `heritability`, `anova`.
Pick one when adding a new tool.

### The write flow

```python
from pathlib import Path
from bloommcp.storage import AnalysisWriter

writer = AnalysisWriter(
    output_root=Path("/app/data/ANALYSIS_OUTPUT"),
    experiment_filename="my_experiment.csv",
    tool_class="qc",
    source_csv=Path("/app/data/SLEAP_OUT_CSV/my_experiment.csv"),
)

version_dir = writer.create_version(
    tool_name="run_qc_workflow",
    params={"threshold": 0.1},
    user_label="initial_run",
)

# write outputs into version_dir
(version_dir / "_cleaned.csv").write_text(...)

entry = writer.commit({"cleaned": "_cleaned.csv"})
# entry.id == "v1"
```

The writer handles four things so the tool doesn't have to:

- **Versioning** — `v<N>` ids increase monotonically and are never
  reused. Directory name is `v<N>_<YYYY-MM-DD>[_<slug>]`.
- **Concurrency** — `fcntl.flock` on the experiment dir between
  `create_version()` and `commit()`. Parallel runs (in-process or
  cross-process) serialize safely.
- **Atomicity** — `manifest.json` is written via tempfile + rename, so
  a crash mid-write leaves the prior manifest intact.
- **Provenance** — each entry records `code_versions` (bloommcp +
  sleap-roots-analyze), input CSV sha256, params, and timestamp.

### Rules

1. One `AnalysisWriter` per tool call. It commits exactly once;
   construct a fresh one for the next run.
2. Paths in the `outputs` dict are **relative to the version
   directory**, not absolute. Absolute paths break the manifest.
3. Don't write into `ANALYSIS_OUTPUT/` outside this pattern —
   `list_existing_analyses` walks manifests to find prior runs.
4. Files that need to leave the container (for the agent or downstream
   tools) go through the `bloommcp-data` Supabase bucket above, not
   the local tree.

Manifest schema lives in `bloommcp/storage/schema.py`.

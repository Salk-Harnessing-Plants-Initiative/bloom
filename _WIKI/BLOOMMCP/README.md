# bloommcp

Reference for the bloommcp service:

What bloommcp is

A Python-based [Model Context Protocol](https://modelcontextprotocol.io) server, implemented on top of [FastMCP](https://github.com/jlowin/fastmcp).
It exposes plant-phenotyping analysis tools to LLM clients (today: the Langchain-agent running in this same stack) over MCP's streamable-HTTP transport on port 8811.

Concretely it ports analysis routines from Elizabeth Berrigan's `sleap-roots-analyze` workflow into MCP tools (QC, outlier detection, descriptive stats, PCA/UMAP, clustering, correlation, heritability, ANOVA).

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

Use the helper in `bloommcp/src/bloom_mcp/supabase_client.py` — don't call
`supabase.create_client()` directly:

```python
from bloom_mcp.supabase_client import read_input_csv, write_output_csv

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
from bloom_mcp.supabase_client import get_postgrest_client

client = get_postgrest_client()

# Read any public.* table
species = client.table("species").select("*").execute()
plants = client.table("plants").select("id, accession_id, sown_at").eq("experiment_id", 42).execute()
```

**Source-aware cyl trait reads.** A scan can carry multiple `cyl_trait_sources`
(one per pipeline run — reprocessing mints a new `source_id`), so reading
`cyl_scan_traits` **directly returns duplicate/cross-source rows**. Read the
source-disambiguated views instead:

```python
# Latest source per scan (the default you almost always want)
traits = client.table("cyl_scan_traits_latest").select("trait_id, value").limit(1000).execute()

# Full source/run dimension when you need it: source_id, source_name,
# pipeline_run_id (the batch key), and an is_latest flag. Group/filter by
# pipeline_run_id for experiment-level "as of run X" analyses.
runs = (
    client.table("cyl_scan_traits_source")
    .select("scan_id, trait_name, value, source_id, pipeline_run_id, is_latest")
    .execute()
)
```

The `get_scan_traits(experiment_id_, trait_name_, source_id_, run_id_)` RPC
exposes the same selection (latest by default; pin a `source_id_`; group by
`run_id_`). "Latest" = `max(source_id)` per scan; the rule lives once in
`cyl_scan_traits_source` — see the `cyl-trait-read` spec and its migration for
the definition (not restated here).

See [`_WIKI/SUPABASE/README.md`](../SUPABASE/README.md) for the full
role / RLS picture.

---

## Coding style for tool calls

**Every workflow tool writes its outputs through the `AnalysisWriter`
class** (from [`bloommcp/src/bloom_mcp/storage/writer.py`](../../bloommcp/src/bloom_mcp/storage/writer.py)), constructed via the `build_writer` factory in
[`_helpers.py`](../../bloommcp/src/bloom_mcp/tools/workflows/_helpers.py).

`AnalysisWriter` implements a versioned write contract: each `(experiment, tool_class)` pair gets one folder in the `bloommcp-data` bucket containing a `manifest.json` that catalogs every run for that
pair.

Each tool call appends a new `VersionEntry` to the same manifest and a new `v<N>_<date>_<slug>/` subfolder for its outputs.

```text
bloommcp-data/bloommcp_output/
└── qc_my_experiment/                  ← one folder per (tool_class, experiment) pair
    ├── manifest.json                  ← cumulative catalog
    ├── v1_2026-06-05_initial_run/
    │   └── _cleaned.csv
    └── v2_2026-06-05_relabelled/
        └── _cleaned.csv
```

Each tool's outputs land in a folder named after its `tool_class`.
`tool_class` is one of the 9 canonical classes — `qc`, `stats`,
`dimred`, `clustering`, `outlier`, `viz`, `correlation`,
`heritability`, `anova` — registered in
[`CANONICAL_TOOL_CLASSES`](../../bloommcp/src/bloom_mcp/storage/__init__.py).

For the step-by-step guide to write a new workflow tool, see
[writing-a-new-tool.md](./writing-a-new-tool.md).

For the underlying schema and the manifest's data model, see
[storage-workflow.md](./storage-workflow.md).

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
│   ├── TRAITS_DIR/            # input CSVs from upstream pipelines
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
├── manifest/                   # versioned-run bookkeeping (renamed from storage/, #487)
├── tools/                      # shared helpers only — every tool lives in sections/
│   ├── _ports.py                # composition seam: injected reader/store
│   ├── _qc_shared.py             # canonical QC thresholds shared by qc_clean/qc_inspect
│   └── _consumer_utils.py        # RunLinks / output-frame helpers shared by consumers
└── sections/                   # every MCP tool lives here (devendor-bloommcp-analysis P2)
    ├── core/                    # list_available_experiments, load_experiment_data,
    │                            # list_existing_analyses (not sleap-roots-analyze wrappers)
    ├── sleap_roots/             # umbrella for the sleap-roots pipeline family
    │   ├── analysis/             # pca_analysis, qc_clean, qc_inspect, remove_outliers,
    │   │                         # clustering, + 5 plot_*.py — one file per tool,
    │   │                         # each delegating to sleap_roots_analyze
    │   └── extraction/           # reserved for future sleap-roots tools (empty)
    └── phenotyping_segmentation/ # Lin's segmentation tools
```

(The Phase-1 `run_*_workflow` tools, `tools/correlation_tools.py` + the 8 correlation
tools, and the 9 vendored analysis/plotting modules under `source/`/`src/bloom_mcp/`
were retired/dropped by `devendor-bloommcp-analysis`; see that OpenSpec change for why.
This diagram predates the `source/` → `src/bloom_mcp/` package move in other respects
too — a fuller refresh of the top-level layout is tracked separately.)

`data/` is auto-provisioned by `make dev-up` — see
[DEV_SETUP.md](../../DEV_SETUP.md#bloommcp-data-directories).

## Storage

bloommcp uses a dedicated S3 bucket on Supabase, `bloommcp-data`, for
CSV exchange with the tools. It has two folders:

```text
bloommcp-data/
├── bloommcp_input/    ← input CSVs the tools consume
└── bloommcp_output/   ← output CSVs the tools produce
```

Any new tool that reads or writes a CSV should use this bucket.

### Storage backend (`local` opt-in)

Analysis **outputs** go to Supabase Storage by default — in local dev that means
MinIO, not files under `./bloommcp/data/ANALYSIS_OUTPUT`. `BLOOM_STORAGE_BACKEND=local`
opts into a fully-local (offline) mode instead — input, output, and boot. See
[storage-backends.md](../../bloommcp/docs/storage-backends.md) for the full
precedence table (including the single `BLOOM_LOCAL_ROOT` var) and setup details —
not duplicated here to avoid the two docs drifting out of sync. (This is the same
`supabase_client.py` boundary that #388's user-facing downloads build on.)

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
`BLOOM_AGENT_KEY`. The role has a schema-wide `SELECT` grant covering virtually every `public.*`
table by default (including tables created later), but row-level security can and does carve out
per-table exceptions to that default — `gene_patents` is a confirmed one, not readable by
`bloom_agent` despite the blanket grant, because its own policy targets a different role. Writes
go through the `bloommcp-data` storage bucket above (insert, update, and a narrowly-scoped delete —
previously-uploaded outputs only, never input CSVs), plus one narrow database-table exception:
`INSERT`/`UPDATE` on `public.bloommcp_usage`, an internal per-caller usage aggregate added by
`bloommcp-caller-identity` (#406, PR #563) — no other table is writable.
See [connecting-claude-code.md](../../bloommcp/docs/connecting-claude-code.md) for the
researcher-facing version of this same disclosure.

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
traits = client.table("cyl_scan_traits_latest").select("scan_id, trait_name, value").limit(1000).execute()

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

**Loading a whole experiment.** `get_scan_traits` is per-trait — one call per
trait name. For a wide-pivot read (all of an experiment's traits at once),
call `get_experiment_traits(experiment_id_, source_id_, run_id_)` instead: same
latest/`source_id`/`run_id` selection as `get_scan_traits`, but returns every
trait for the experiment in a single round trip. Use
`list_experiment_trait_sources(experiment_id_)` to see which sources/runs are
available before pinning one.

```python
traits = client.rpc("get_experiment_traits", {"experiment_id_": 42}).execute()
sources = client.rpc("list_experiment_trait_sources", {"experiment_id_": 42}).execute()
```

See [`_WIKI/SUPABASE/README.md`](../SUPABASE/README.md) for the full
role / RLS picture.

---

## Coding style for tool calls

**Every persistence-writing tool writes through the `ResultStore` port**
(`bloom_mcp.tools._ports.store()`), never `AnalysisWriter` or `supabase` directly —
see [`qc_clean.py`](../../bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_clean.py)
for a worked example (`store().create_run()` → write outputs into the staging dir →
`store().commit()`). (The Phase-1 workflow tools used to write through
`AnalysisWriter` via a `build_writer` factory in `tools/workflows/_helpers.py`;
both are gone — retired by `devendor-bloommcp-analysis`, which also moved this
file from `tools/qc_clean_tool.py` to its current path.)

The port's real (`SupabaseResultStore`) implementation gives the same versioned write
contract the diagram below shows: each `(experiment, tool_class)` pair gets one folder in the `bloommcp-data` bucket containing a `manifest.json` that catalogs every run for that
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
[`CANONICAL_TOOL_CLASSES`](../../bloommcp/src/bloom_mcp/manifest/__init__.py).

To add a tool to a **section** (the current pattern — e.g. phenotyping), see
[adding-a-section-tool.md](./adding-a-section-tool.md).

The older **workflow-tool** style ([writing-a-new-tool.md](./writing-a-new-tool.md))
is retired — kept as historical record only.

For the underlying schema and the manifest's data model, see
[storage-workflow.md](./storage-workflow.md).

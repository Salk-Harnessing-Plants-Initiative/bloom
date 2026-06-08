# Writing a new workflow tool

This doc is the guide for adding a new MCP workflow tool to bloommcp.

If you're trying to understand how the storage layer works under the
hood, read [storage-workflow.md](./storage-workflow.md) first.

## What is a workflow tool?

A workflow tool is one MCP call the LLM can make that:

1. Loads an experiment's data from the `bloommcp-data` bucket's
   `bloommcp_input/` prefix via `read_input_csv()` in
   `source/supabase_client.py`
2. Runs some analysis (QC, clustering, dimensionality reduction, etc.)
3. Writes the result into a versioned folder under the bucket's
   `bloommcp_output/` prefix through `AnalysisWriter`
4. Returns a structured payload to the LLM (`version_id`,
   `manifest_path`, `summary`, paths to output files)

Every workflow tool follows the same shape: read the input CSV from
the bucket, run the analysis, build a writer, allocate a version,
write outputs, commit.

Input and output both live in the same
`bloommcp-data` bucket — `bloommcp_input/<file>.csv` goes in, and the
tool's writes land under `bloommcp_output/<tool_class>_<stem>/v<N>_*/`.

The five existing workflow tools are all in [bloommcp/tools/workflows/](../../bloommcp/tools/workflows/):

- [`qc.py`](../../bloommcp/tools/workflows/qc.py) — clean a CSV (legacy: bind-mount input)
- [`stats.py`](../../bloommcp/tools/workflows/stats.py) — descriptive
  statistics (legacy: bind-mount input)
- [`dimred.py`](../../bloommcp/tools/workflows/dimred.py) — PCA / UMAP (legacy: bind-mount input)
- [`clustering.py`](../../bloommcp/tools/workflows/clustering.py) —
  k-means / GMM (legacy: bind-mount input)
- [`outlier.py`](../../bloommcp/tools/workflows/outlier.py) — outlier
  detection across five methods (legacy: bind-mount input)

These five still read CSVs from the `BLOOM_TRAITS_DIR` bind mount
through `load_experiment_data()`.

That is the OLD path. **New tools
should use `read_input_csv()` from the bucket** — the pattern shown in
Step 3 below. The legacy tools will be migrated over time, but the
input side of the contract has already moved to the bucket.

## Before you start

You need three things in your head:

1. **What inputs** your tool takes from the LLM (filename, params)
2. **What outputs** it produces (one CSV? a CSV + a JSON log? a plot?)
3. **What `tool_class`** it belongs to

Step 1: Pick a tool_class

`tool_class` is a short label like `"qc"`, `"stats"`, `"dimred"`.

It controls **where the output lands** in the bucket and **which folder
the agent reads from** when asking about prior runs.

The canonical set lives in [`bloommcp/storage/__init__.py`](../../bloommcp/storage/__init__.py)
as `CANONICAL_TOOL_CLASSES`:

```python
CANONICAL_TOOL_CLASSES = (
    "qc",
    "stats",
    "dimred",
    "clustering",
    "outlier",
    "viz",
    "correlation",
    "heritability",
    "anova",
)
```

Pick the one that best describes your tool.

The agent's discovery tool (`list_existing_analyses`) walks a hard-coded subset of these, so a
new class needs that registry updated too.

Rule of thumb:

- Your tool does an existing analysis kind → reuse the existing class
- Your tool is a genuinely new analysis kind → add a new class and
  open a PR that updates both `CANONICAL_TOOL_CLASSES` AND the
  `TOOL_CLASSES` tuple in
  [`bloommcp/tools/storage_tools.py`](../../bloommcp/tools/storage_tools.py)

## Step 2: Create the file

Workflow tools live in
[`bloommcp/tools/workflows/`](../../bloommcp/tools/workflows/), one
file per tool. The filename is the tool class (no `_` prefix), and
the function inside has a `run_*_workflow` name.

```
bloommcp/tools/workflows/<your_class>.py
```

So a clustering tool lives in `clustering.py` and exports `run_clustering_workflow`.

If you're adding a SECOND tool inside an existing class, name the file after the tool itself
(`run_specific_thing.py`).

Open the file. Top of file:

```python
"""run_my_workflow — one-line description of what this tool does."""
from __future__ import annotations

from typing import Optional

from source.supabase_client import read_input_csv
from source.experiment_utils import detect_columns
from ._helpers import build_writer

_TOOL_NAME = "run_my_workflow"
_TOOL_CLASS = "qc"    # ← whichever class fits
```

(No more `TRAITS_DIR` or `load_experiment_data` — those are the
legacy bind-mount imports the five existing tools still use.)

The two module-level constants are not strictly required, but every
existing tool has them and they make the rest of the file cleaner.

## Step 3: Write the function

Every workflow tool follows the **five-step recipe**.
Copy this template, then replace the analysis with whatever your tool
actually does.

```python
def run_my_workflow(
    filename: str,                              # required: which experiment
    some_param: float = 0.5,                    # tool-specific params
    another_param: int = 10,
    user_label: Optional[str] = None,           # optional human tag
) -> dict:
    """One-line summary the LLM sees as the tool description.

    Optional longer explanation of what the tool does, when to use it,
    and what comes back.

    Args:
        filename: CSV basename living under the bucket's bloommcp_input/
            prefix (e.g. "alfalfa_gwas_wave2.csv"). No slashes.
        some_param: ...
        another_param: ...
        user_label: Optional slug appended to the version directory name.

    Returns:
        Dict with version_id, manifest_path, summary, outputs.
    """

    # 1. Load the experiment data from the bucket. read_input_csv pulls
    #    `bloommcp_input/<filename>` out of the bloommcp-data bucket and
    #    parses it as a pandas DataFrame. Then detect_columns classifies
    #    each column as a numeric trait or a metadata column — that
    #    helper is pure DataFrame logic, so it works on bucket-loaded
    #    data just like it did on bind-mount data.
    try:
        df = read_input_csv(filename)
    except Exception as e:
        return {"error": f"Failed to load {filename!r} from bucket: {e}"}
    config = detect_columns(df)
    trait_cols = config["trait_cols"]
    source_label = f"bucket:{filename}"

    # 2. Do your analysis. df is a pandas DataFrame. trait_cols is the
    #    list of numeric trait columns. config also has metadata column
    #    names (genotype_col, replicate_col, sample_id_col, etc.).
    result_df, log = do_my_analysis(
        df, trait_cols, some_param=some_param, another_param=another_param,
    )

    # 3. Build the writer. source_csv is None because the input lives
    #    in the bucket, not on local disk — there's no local path to
    #    sha256. (Future enhancement: hash the bytes returned by
    #    read_input_csv if input provenance matters.)
    writer = build_writer(
        filename,
        _TOOL_CLASS,
        source_csv=None,
    )

    # 4. Allocate a version. Returns a tmp Path. Write outputs into it
    #    like it's a normal local folder.
    version_dir = writer.create_version(
        tool_name=_TOOL_NAME,
        params={
            "some_param": some_param,
            "another_param": another_param,
        },
        user_label=user_label,
    )

    result_df.to_csv(version_dir / "_result.csv", index=False)
    # ...write any other outputs (plots, JSON logs, etc.)

    # 5. Commit. outputs maps a stable short name to the path RELATIVE
    #    to version_dir (which is the same as the path inside the
    #    bucket's version directory).
    entry = writer.commit({
        "result": "_result.csv",
    })

    # Return a structured payload the LLM can use.
    return {
        "version_id": entry.id,
        "version_dir": entry.version_dir,
        "manifest_path": f"{writer.analysis_dir.path}manifest.json",
        "summary": {
            "n_rows_in": len(df),
            "n_rows_out": len(result_df),
            "source": source_label,
        },
        "outputs": {
            "result_csv": "_result.csv",
        },
    }
```

A few subtleties worth knowing:

- **The path in `outputs` is relative to `version_dir`.** If your
  output file is at `version_dir / "_result.csv"`, you pass
  `"_result.csv"` (no leading directory). The writer prepends the
  version directory name automatically when computing the storage key.
- **Writing nested folders works.** If your analysis writes
  `version_dir / "plots" / "fig.png"`, that's fine — pass
  `"plots/fig.png"` as the relative path.
- **The keys in `outputs` are stable short names**, not filenames.
  Convention: use what a downstream tool will look up by. The QC tool
  uses `"_cleaned.csv"` as the key so other tools resolve the cleaned
  CSV via `entry.outputs.get("_cleaned.csv")`.

## Step 4: Register with MCP

At the bottom of your file, add the registration hook:

```python
def register(mcp):
    """Register run_my_workflow with the MCP server."""
    mcp.tool()(run_my_workflow)
```

Then open [`bloommcp/tools/workflows/__init__.py`](../../bloommcp/tools/workflows/__init__.py) and add an import + call so the server picks up your tool at startup:

```python
from . import my_workflow

def register_all(mcp):
    qc.register(mcp)
    stats.register(mcp)
    # ...
    my_workflow.register(mcp)
```

## Step 5: Write tests

Two layers of tests, in this order:

**Unit tests** for the parts of your tool that are pure functions.
If `do_my_analysis()` is its own function, give it a pytest in
`tests/unit/test_my_workflow.py`. Use small fixture DataFrames; check
shape, edge cases (empty input, all-NaN column, etc.). No MCP, no
storage, no network.

**Integration tests** for the end-to-end MCP call. Add a file in
`tests/integration/test_workflow_my_workflow.py`. Pattern:

```python
def test_run_my_workflow_round_trip(mock_storage):
    """Full call goes through build_writer, lands an entry in the manifest."""
    result = run_my_workflow(filename="fixture.csv", some_param=0.5)

    assert "error" not in result
    assert result["version_id"] == "v1"
    assert "result_csv" in result["outputs"]
    # Read the manifest back and verify the entry is there.
    ...
```

`mock_storage` is a fixture that replaces the Supabase storage client
with an in-memory mock — same pattern as
[`tests/unit/test_supabase_client.py`](../../tests/unit/test_supabase_client.py).

**Minimum bar before the PR is reviewable:** at least one passing
integration test. Unit tests are nice-to-have.

## Step 6: Update docs

Two things:

1. If you added a new `tool_class`, update the storage layer's
   canonical registry AND the agent's discovery registry. See
   [Step 1](#step-1-pick-a-tool_class).
2. If your tool produces a NEW kind of output file the storage layer
   needs to know about (e.g., a `.parquet` instead of `.csv`), add the
   extension to `_CONTENT_TYPES` in
   [`bloommcp/source/supabase_client.py`](../../bloommcp/source/supabase_client.py)
   so it gets the right MIME type on upload.

If your tool just does another CSV-in-CSV-out kind of analysis, no
storage layer changes are needed.

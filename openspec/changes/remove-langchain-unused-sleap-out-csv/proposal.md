## Why

`langchain/SLEAP_OUT_CSV/` is 6.6MB of real CSV data (`cylinder_alfalfa_gwas_wave2.csv`,
`cylinder_amaranth_tis108_exp1.csv`, `cylinder_traits.csv`,
`turface_rice_treatment_exp1.csv`, `turface_traits.csv`) committed directly into the
`langchain-agent` service directory. It has been dead weight since the bloommcp roadmap
(approved 2026-06-15) moved the real read path to Supabase Storage (`bloommcp_input/`)
behind the `ExperimentReader` port: nothing in `langchain/` opens these files, and
`langchain/Dockerfile:35`'s `COPY --chown=bloom:bloom . .` (no `.dockerignore` exclusion)
bakes all 6.6MB into the deployed `langchain-agent` image in dev, staging, and prod alike,
for a directory the running service never touches (#475).

## What Changes

- Delete `langchain/SLEAP_OUT_CSV/` (all five CSVs) entirely.
- Update the two stale illustrative filename references that name specific files from the
  deleted directory:
  - `langchain/prompts/router.py:42` — a router few-shot training example
    (`"Run a QC report on cylinder_alfalfa_gwas_wave2.csv."`).
  - `langchain/tools/context_tools.py:87` — the `CONTEXT_MCP` system-prompt block
    (`"Files like cylinder_alfalfa_gwas_wave2, turface_rice_treatment_exp1 are CSV
    files..."`).
  - Both are replaced with `cylinder_traits.csv` / `turface_traits.csv` — the same
    illustrative dataset-name convention, but names that remain real and current
    elsewhere in the codebase (`bloommcp/src/bloom_mcp/tools/correlation_tools.py`'s
    `EXPERIMENTS` dict, `bloommcp/tests/tools/test_straggler_routing.py`) instead of
    names that only ever existed in the now-deleted dead data.
  - **Not in scope:** `context_tools.py`'s `CONTEXT_MCP` block also names Phase-1
    workflow tools (`run_qc_workflow`, `run_outlier_workflow`, etc.) that PR #438
    (open, "de-vendor analysis library + retire Phase-1 workflows") is retiring — that
    rewrite belongs to #438, not this change. This change only touches the two stale
    filenames, not the tool-name content.
- No dependency, migration, or runtime behavior change — this is dead-data removal plus
  two string edits in illustrative example content.

## Impact

- Affected specs: `langchain-agent-packaging` (**ADDED** — new capability; nothing
  existing today codifies what the `langchain-agent` image does/doesn't ship).
- Affected code: `langchain/SLEAP_OUT_CSV/` (deleted), `langchain/prompts/router.py`,
  `langchain/tools/context_tools.py`.
- **Confirmed no off-repo consumer:** grepped all of `langchain/` for reads of these
  files — zero hits; the only matches are the two illustrative-string references above.
  `bloommcp/src/bloom_mcp/tools/correlation_tools.py` and
  `bloommcp/tests/tools/test_straggler_routing.py` reference the *same filenames*
  (`cylinder_traits.csv`, `turface_traits.csv`) but read them from a separate runtime
  path — `bloommcp`'s own bind-mounted, gitignored `bloommcp/data/SLEAP_OUT_CSV`
  (`BLOOM_TRAITS_DIR`), a different container with no filesystem sharing with
  `langchain-agent`. This is a naming coincidence (both platforms use the same
  illustrative "cylinder"/"turface" dataset names), not a functional dependency —
  confirmed safe to delete `langchain/SLEAP_OUT_CSV/` without touching bloommcp.
- Net effect: ~6.6MB removed from the `langchain-agent` Docker image; no behavior change
  for the running service.
- **Data integrity check:** the 5 CSVs are real (non-synthetic) data, but `md5sum`
  confirms they are byte-identical duplicates of files whose actual home is
  `bloommcp/data/SLEAP_OUT_CSV/` (populated from upstream pipelines / bloommcp's own
  fixtures per `_WIKI/BLOOMMCP/README.md`). No unique data is destroyed. One soft,
  non-blocking finding: nothing documents or scripts seeding
  `bloommcp/data/SLEAP_OUT_CSV/{cylinder,turface}_traits.csv` the way `make
  bloommcp-smoke` seeds `turface_raw.csv` — these two files' byte-identical match to
  `langchain/SLEAP_OUT_CSV/` suggests a developer once manually copied them from here
  as an informal convenience source. Deleting `langchain/SLEAP_OUT_CSV/` removes that
  undocumented convenience copy (not a functional dependency).
- **#438 scope-gap risk:** #438 is currently an open draft PR whose stated scope is
  bloommcp-internal (vendored modules, `run_*_workflow` retirement) and does not
  concretely list `langchain/tools/context_tools.py` among its tasks, despite #475's
  own text assuming #438 will eventually rewrite `CONTEXT_MCP`'s stale Phase-1
  tool-name references. This change's PR will flag that gap on #438 so the deferred
  half of ask #2 has an explicit owner and doesn't fall through the crack between the
  two changes.
- Related: #477 (filed same day) independently tracks confirming
  `bloommcp/data/SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` are dead weight in staging/prod —
  a separate, bloommcp-side cleanup that corroborates this proposal's "naming
  coincidence, not a dependency" conclusion for ask #3.
- **Branch/PR:** branches off `origin/staging`; this branch
  (`egao28/remove-unused-langchain-sleap-out-csv-475`). Closes #475.

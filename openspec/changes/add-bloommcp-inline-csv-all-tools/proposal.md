## Why

`add-bloommcp-inline-csv-input` (PR #608, merged) shipped the ephemeral inline-content path for
**one** tool: `qc_clean`. Its shared helper (`bloom_mcp.tools._inline_input`) documents itself as
"the shared surface every consumer tool's own `csv_content` path imports" — and nothing imports
it except `qc_clean`. A Claude Code user can hand bloommcp a local CSV, get a QC summary, and then
hit a wall: they cannot PCA it, cluster it, UMAP it, describe it, inspect it, trim its outliers,
or correlate it against a registered experiment. This change completes #582's rollout for every
tool on `staging` that reads an experiment table.

## What Changes

Rationale, alternatives, and measurements for each numbered item live in `design.md` under the
matching Decision — this section states what changes, not why in full.

### 1. Ten tools accept inline content (`design.md` Decision 6, 7)

**ADD** `csv_content` as a mutually exclusive sibling of the registered-experiment parameter
(exactly one required) to the seven remaining contract-wrapped `ExperimentFrame` consumers —
`qc_inspect`, `remove_outliers`, `pca_analysis`, `umap_analysis`, `clustering`,
`descriptive_stats`, `cross_experiment_correlations` — and to the two legacy frame readers,
`core.load_experiment_data` (whose sibling is `filename`, not `experiment`) and
`phenotyping_segmentation.summarize_trait`. All parse through the existing
`parse_inline_csv_frame`, bypass the `ExperimentReader` port, and return `input_sha256`.

`cross_experiment_correlations` resolves **per side** (`csv_content_1` / `csv_content_2`); mixed
registered/inline calls are valid in both directions, and if either side is inline the whole call
is ephemeral.

`qc_clean` is refactored onto the same shared resolver in this change, making ten callers of one
implementation rather than nine plus a hand-rolled original.

### 2. **BREAKING (output schema)** — result models widen (`design.md` Decision 8)

`RunLinks.run_ref` / `.version_dir` / `.manifest_path` widen from required `str` to
`Optional[str]`, and every consumer result model's identity field (`experiment`, or
`experiment_1`/`experiment_2`/`source_1`/`source_2`) widens likewise. An inline call creates no
run, so there is no run reference, version directory, or manifest to name, and a placeholder
string would name an object that does not exist. This is visible in `tools/list` on the
registered path too, and it removes a Pydantic guarantee that a persisting tool populated its
run links — so each tool gains a test asserting its registered path still returns them.

### 3. The two producer tools gain an opt-in table return (`design.md` Decision 2)

`qc_clean.return_cleaned_csv` and `remove_outliers.return_trimmed_csv` (both `bool`, default
`false`, inline-only, rejected with a registered experiment). When set, the response carries the
produced table as CSV text plus its SHA-256, capped at `MAX_INLINE_CSV_BYTES` with an explicit
`\n` line terminator so the digest is platform-independent.

Without this, five of the seven new inline paths are unusable on real data: they require finite,
analysis-ready traits, and the inline path had no way to produce them. The text goes into the
response and nowhere else — no file, no manifest, no run, no server-side lineage. The chaining it
enables is the caller's own.

`qc_clean`'s inline `next_step` suppression is lifted: it now recommends `qc_inspect` **with the
same `csv_content`** and names the `input_sha256`. The suppression existed only because
`qc_inspect` had no inline support; that premise expires here.

### 4. Plots are rejected on the inline path (`design.md` Decision 1)

`include_plots=true` — and every plot-companion parameter — is rejected with `csv_content` on
`remove_outliers`, `pca_analysis`, `umap_analysis`, and `clustering`.

`qc_inspect` is **not** in that list: it has no `include_plots` parameter and renders its figures
unconditionally into the run's staging directory. Its inline path instead produces **no figures
at all**, returning the summary, per-trait diagnostics, and recommendation. That is a real
reduction in what the tool delivers and is stated in its docstring and field description rather
than left for a caller to discover.

### 5. `require_clean` becomes caller-asserted, per each tool's own policy (`design.md` Decision 3)

Inline content cannot be certified clean. Each consumer re-establishes the invariant locally,
**matching its own existing failure policy rather than a uniform one**:

- `pca_analysis`, `umap_analysis`, `clustering`, `cross_experiment_correlations` — all-or-nothing
  today; inline raises `invalid_input` (not `assumption_violated`, which blames a reader this path
  does not use) naming the offending columns, remedy naming
  `qc_clean(csv_content=..., return_cleaned_csv=true)`. Registered-path text unchanged.
- `descriptive_stats` — routes a non-finite trait to `failed_traits` by deliberate design, so one
  bad trait does not block hundreds. **Unchanged on both paths**; it gains no guard.
- `remove_outliers` — has no finiteness check today because `require_clean` made one unreachable.
  Gains one **scoped to the inline path** only.

`_validate_trait_subset` keeps an identical accepted-column set; only its wording changes, since
"certified-clean traits of `<experiment>`" is false when there is no experiment and no
certification.

### 6. Registered-only parameters are rejected, never dropped (`design.md` Decision 5)

Per the roster in the `bloommcp-inline-csv-input` delta: `version` (including `"latest"`, which
`remove_outliers` coerces to a real pin), `version_1`/`version_2` per side, `source_id`/`run_id`,
`user_label`, and the plot-companion parameters.

### 7. Resource guards, because the inherited caps do not bound the new tools (`design.md` Decision 9)

`MAX_INLINE_CSV_BYTES` (5 MiB) and `MAX_INLINE_CSV_COLUMNS` (2000) were sized for `qc_clean`,
whose work is linear in the payload. Measured on this machine: a compliant 5 MiB payload parses in
~0.03 s into **313,171 rows**; `clustering(method="hierarchical")` is O(n²) in time and memory
(n=6,000 → 1.7 s / +809 MiB RSS; n=12,000 → 7.2 s / +2.38 GiB), so that row count implies a
condensed distance matrix of hundreds of gibibytes. Separately,
`cross_experiment_correlations` costs ~326 µs per trait pair and defaults to *all* traits on both
sides — two inline sides at the column cap imply 4,000,000 pairs, ≈22 minutes of single-threaded
CPU for one 10 MiB request. Nothing sits in front of this path: no rate limiting, no proxy body
cap, no per-tool timeout, and **no container memory limit**, so an OOM is resolved by the host and
may kill an unrelated service. On staging, OAuth makes it reachable by any Bloom account.

**ADD** three guards, each firing before the expensive work and leaving the registered path
untouched: `MAX_INLINE_CSV_ROWS` (20,000 — ~100× the largest real fixture) in
`parse_inline_csv_frame`; an inline hierarchical-sample cap in `clustering`, plus an upper bound
on `max_clusters` (today it has a lower bound only, and it multiplies the quadratic silhouette
search); and an inline trait-pair-product cap in `cross_experiment_correlations`, set above the
widest existing oracle so no registered test is affected.

### 8. A kill switch (`design.md` Decision 10)

`BLOOMMCP_INLINE_CSV_ENABLED` (default enabled), read once in `resolve_inline_or_experiment`.
bloommcp has no feature flags today, and the deploy pipeline's automatic rollback covers only a
*failed* deploy — a successfully deployed bad build needs a new commit through a full multi-image
rebuild. This change turns on ten tools at once; one variable and a container restart is a
proportionate off switch.

### 9. Docs — the `tools/list` surface is the deliverable

Every touched tool's module docstring, tool docstring, and field descriptions are what the agent
reads on every session; a wrong docstring is a wrong behavior. The `csv_content` field text is
single-sourced as a constant in `_inline_input` (parameterized by field name so
`csv_content_1`/`_2` reuse it) and pinned by a schema-level test, preserving the predecessor's
choice to keep the "no history" caveat in exactly one canonical place instead of ten drifting
copies. `bloommcp/docs/connecting-claude-code.md`, `bloommcp/README.md`, `_WIKI/BLOOMMCP/`, and
the LangGraph agent's `CONTEXT_MCP` system prompt — which currently tells Bloom's own web chat
that experiments are *always* identified by an experiment identifier — are all updated.

## Explicitly Out of Scope

- **The five legacy plot tools** (`plot_trait_histograms`, `plot_trait_boxplots`,
  `plot_correlation_matrix`, `plot_heritability_bar`, `plot_variance_decomposition`). Their only
  output channel is a PNG in the shared plots directory; for §4's reasons there is no inline-safe
  way to give them a `csv_content` path.
- **`heritability_analysis`** — not on `staging`; it lands with #462. One-tool follow-up
  (tasks.md §13).
- **`compute_min` / `compute_median` / `compute_mode`** — demo tools reading numbers from a
  `.txt`. No `ExperimentFrame`, nothing to parse.
- **`list_available_experiments`, `list_existing_analyses`, `get_download_links`,
  `list_experiment_sources`** — they enumerate registered and persisted server-side state, from
  which inline content is absent by design.
- **Persistence, registration, lineage, or run history for inline calls** — unchanged from #582.
- **Container memory limits, rate limiting, and per-tool timeouts.** All three are missing today
  and affect the registered path too; §7's application-level caps are the in-scope mitigation.
  Follow-ups in tasks.md §13.
- **Raising `MAX_INLINE_CSV_BYTES`.** 5 MiB stays; the table returns reuse it rather than
  introducing a second number.

## Impact

- **Affected specs:**
  - `bloommcp-inline-csv-input` (**modified**) — the cross-tool ephemeral contract: resolver,
    registered-only rejection, never-persists, never-logged, caller-asserted-clean, plot
    rejection, resource guards, kill switch, and the two legacy tools (which have no capability
    spec of their own anywhere).
  - `bloommcp-tool-contract` (**modified**) — `RunLinks` widening. **BREAKING**; contradicts the
    current "RunLinks Base Model" requirement, so it is a MODIFIED delta carrying the full text.
  - `bloommcp-qc-clean-tool` (**modified**) — `return_cleaned_csv`; and the `next_step`
    suppression scenario removed via a MODIFIED block carrying the predecessor's full requirement
    text, so archiving cannot silently revert it.
  - `bloommcp-pca-analysis-tool`, `bloommcp-umap-analysis-tool`,
    `bloommcp-remove-outliers-tool` (**modified**) — each existing `require_clean`/persistence
    requirement scoped to the registered path, plus an inline requirement.
  - `bloommcp-qc-inspect-tool`, `bloommcp-clustering-tool`, `bloommcp-descriptive-stats-tool`,
    `bloommcp-cross-experiment-correlations-tool` (**added**) — these capabilities exist only in
    their own unarchived `add-*` changes. Deltas against an unarchived capability are this repo's
    established practice (three concurrent changes carry `bloommcp-clustering-tool` deltas
    today), and putting each tool's requirement in its own capability is what keeps
    `openspec show <tool>` complete after archiving.
- **Affected code:**
  - `bloommcp/src/bloom_mcp/contract/models.py` — `RunLinks` widening.
  - `bloommcp/src/bloom_mcp/tools/_inline_input.py` — `resolve_inline_or_experiment`,
    `InlineInput`, `reject_registered_only_params`, `serialize_table_csv`,
    `MAX_INLINE_CSV_ROWS`, the kill switch, and the canonical field-description constants.
  - `bloommcp/src/bloom_mcp/tools/_qc_shared.py` — `_validate_trait_subset` presentation label.
  - Ten tool modules: `sections/sleap_roots/analysis/{qc_clean,qc_inspect,remove_outliers,
    pca_analysis,umap_analysis,clustering,descriptive_stats,cross_experiment_correlations}.py`,
    `sections/core/load_experiment_data.py`,
    `sections/phenotyping_segmentation/summarize_trait.py` — each with docstrings and field
    descriptions updated for the two-path reality.
  - `bloommcp/src/bloom_mcp/server.py` (tool roster comment),
    `sections/sleap_roots/__init__.py`, `sections/core/__init__.py`.
  - `bloommcp/docs/connecting-claude-code.md`, `bloommcp/docs/local-validation.md`,
    `bloommcp/docs/roadmap.md`, `bloommcp/README.md`, `_WIKI/BLOOMMCP/README.md`,
    `_WIKI/BLOOMMCP/adding-a-section-tool.md`, `langchain/tools/context_tools.py`.
  - `bloommcp/tests/` — a dedicated inline section per tool test module; extensions to
    `test_inline_input.py`; **new** modules for the roster tests and for `summarize_trait`, which
    has no test module today.
- **Dependencies:** none new.
- **Sequencing.** Based on `origin/staging`. **Not independent of in-flight work**, contrary to an
  earlier draft of this line. Trial-merged with `git merge-tree` rather than eyeballed, because an
  earlier draft understated exactly this:
  1. **#777** (`egao28/bloommcp-heritability-analysis-462`) — **a real conflict, verified**:
     `git merge-tree` reports `CONFLICT (content)` in `bloommcp/docs/connecting-claude-code.md`.
     Both changes insert a new section at the same anchor. Whichever lands second resolves it by
     hand; it is a doc-prose conflict, so the resolution is mechanical but not automatic. #777
     also changes the tool roster this change's roster tests enumerate.
  2. **#683** (`egao28/bloommcp-converge-viz-tools-466`) — file-level overlap in
     `bloommcp/src/bloom_mcp/tools/_qc_shared.py`, which **auto-merges clean today** (verified).
     Do not read that as "no interaction": #683 rewrites `_validate_trait_subset`'s duplicate
     detection in the same function PR 1 adds the `certified` presentation flag to, so a textually
     clean merge can still produce an incoherent function. Whoever lands second should re-read
     that function whole rather than trusting the merge.
  3. `egao28/bloommcp-plot-guards-721` — rewrites `umap_analysis.py` (+295) and
     `test_umap_analysis_tool.py` (+337). No overlap with PR 1's files; this matters for PR 2,
     which touches `umap_analysis` directly.
  - PR 1 itself only collides with #777 (docs) and #683 (`_qc_shared.py`). PR 2 and PR 3 have the
    broader exposure.
  - **Archive order:** after `add-bloommcp-inline-csv-input`, for the reason recorded in the
    `bloommcp-qc-clean-tool` delta.
- **PR strategy.** Three sequential PRs to `staging`, not one — see `design.md` Decision 11.
  Only the last carries a closing keyword for #582; the earlier two say "Part of #582", because
  the repo auto-closes issues named with a closing keyword in a merged staging PR.

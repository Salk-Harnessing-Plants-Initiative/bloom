## ADDED Requirements

### Requirement: Granular Tools Organized as Section Sub-Servers

bloom-mcp's granular MCP tools SHALL be organized under the `sections/` layout — each
section a FastMCP sub-server, one file per tool, auto-mounted into the combined `/mcp`
surface (namespaced `<section>_<tool>`) and served at its own path — rather than as
loose `tools/*.py` modules wired by per-module `register(mcp)` calls in `server.py`. Adding
a tool SHALL require only a new file in a section package plus its `SECTIONS` wiring, with no
per-tool edit to `server.py`.

#### Scenario: A migrated tool appears namespaced in the combined surface

- **WHEN** the server boots and a FastMCP `Client` lists tools on `/mcp`
- **THEN** each migrated granular tool appears under its section namespace (e.g.
  `sleap_roots_pca_analysis`) and is also reachable on that section's own path

#### Scenario: Section is self-registering

- **WHEN** a section package exposes a `section` FastMCP instance and is added to `SECTIONS`
- **THEN** the server mounts it with no additional `server.py` change per tool in that section

### Requirement: sleap_roots Umbrella Section for Analysis Tools

The granular analysis tools that delegate to `sleap-roots-analyze` SHALL live in a section
named `sleap_roots`, organized as an umbrella for the sleap-roots family with an `analysis/`
subgroup (wrapping `sleap-roots-analyze`) and a reserved `extraction/` subgroup for future
`sleap-roots` trait-extraction tools. The `analysis/` subgroup SHALL contain `pca_analysis`,
`qc_clean`, `qc_inspect`, `remove_outliers`, `clustering` (the polymorphic kmeans/GMM/
hierarchical tool, #309/#422 — landed on `staging` after this change was first proposed; it
already delegates to `sleap_roots_analyze` with no vendored import, so only the Phase-2 move
applies to it), and the **three surviving plotting tools** (`plot_trait_histograms`,
`plot_trait_boxplots`, `plot_correlation_matrix`), each tool in its own file. It was
**five** when this change was written; `add-bloommcp-heritability-analysis-tool` (#462)
subsequently retired `plot_heritability_bar` and `plot_variance_decomposition` into the
`heritability_analysis` consumer, whose optional plots render those two figures from the
same computation that returns the numbers. Amended here rather than left to contradict
that change at archive time — whichever of the two archives second would otherwise
publish a requirement mandating deleted modules. The former `plot_dendrogram` and
`plot_outlier_comparison` tools SHALL NOT be carried over (`plot_dendrogram` computes
hierarchical clustering internally rather than consuming the `clustering` tool's persisted
output, and `plot_outlier_comparison` reads the retired outlier workflow's output); each may
return later as a plot consuming its owning granular tool's persisted result. The section
SHALL NOT be named `sleap_roots_traits` (collides with the separate `sleap-roots-traits`
pipeline repository, which these tools do not wrap). No tool in this section SHALL contain
analysis or plotting logic of its own; each SHALL delegate to `sleap_roots_analyze`.

#### Scenario: Analysis tools are namespaced under sleap_roots

- **WHEN** the tool surface is listed after migration
- **THEN** `pca_analysis`, `qc_clean`, `qc_inspect`, `remove_outliers`, `clustering`, and the
  plotting tools appear under the `sleap_roots` namespace, and each delegates to
  `sleap_roots_analyze`

#### Scenario: pca_analysis reports dropped constant traits across the migration

- **WHEN** `pca_analysis` (or its Phase-2 destination, `sleap_roots_pca_analysis`) drops one or
  more constant (zero-variance) trait columns from a certified selection before fitting
- **THEN** the result carries a `dropped_constant_traits` field naming them, rather than raising
  `assumption_violated` — this behavior (the #412 fix) SHALL be in place before the Phase-2 move
  and SHALL be unchanged by it

#### Scenario: Plotting tools are one file per tool

- **WHEN** the `sections/sleap_roots/analysis/` package is inspected
- **THEN** each of the five surviving plotting tools (formerly bundled in `viz_tools.py`) is
  defined in its own file, `plot_dendrogram` and `plot_outlier_comparison` are absent, and
  each surviving tool's behavior (inputs, outputs, produced artifacts) is unchanged from
  before the migration — **except** for the intentional, scoped path-safety and
  error-sanitization hardening in the next scenario (Phase 3 / P3.3), which is a deliberate
  narrowing of previously-accepted (unsafe) inputs, not a regression

#### Scenario: remove_outliers restricts trait_columns to certified-clean traits

- **WHEN** `remove_outliers` is called with a `trait_columns` entry that is numeric but not a
  member of the certified-clean trait set (`frame.trait_cols`) — e.g. a role column or
  QC-excluded metadata column
- **THEN** the call is rejected with `BloomMCPError(code="invalid_input")`, matching
  `pca_analysis`/`clustering`'s existing certified-set restriction (Phase 3 / P3.2 — the
  file's own local, drifted validator duplicate is removed in favor of the shared
  `_qc_shared._validate_trait_subset(require_certified=True)`)

#### Scenario: Plotting tools reject unsafe filenames and never leak raw exception text

- **WHEN** any of the surviving plotting tools is called with a `filename` containing a path
  traversal (`..`) or an absolute path, or the delegate call fails internally
- **THEN** the tool rejects the traversal/absolute filename before any file read (no content
  from outside `TRAITS_DIR` is read or returned), and any internal failure returns a
  sanitized string containing no raw exception text — while a normal, valid-but-nonexistent
  filename still returns the tool's pre-existing not-found message unchanged (Phase 3 / P3.3;
  an intentional, minimal stopgap — the full `@as_mcp_tool`/Pydantic convergence is deferred
  to #466 for these 3 tools; #462 already completed it for `plot_heritability_bar`/
  `plot_variance_decomposition` by retiring both into `heritability_analysis`)

### Requirement: Core Section for Cross-Cutting Discovery Tools

A `core` section SHALL hold the cross-cutting discovery tools that are not
`sleap-roots-analyze` wrappers (`list_available_experiments`, `load_experiment_data`, and
`list_existing_analyses`), distinct from the `sleap_roots` section, and these tools SHALL
remain always-available to the agent. The redundant `inspect_data_quality` tool SHALL be
removed (its function is covered by `qc_inspect`). The core discovery tools SHALL NOT import
any vendored analysis module.

#### Scenario: Discovery tools are in core and always available

- **WHEN** the tool surface is listed
- **THEN** `list_available_experiments`, `load_experiment_data`, and `list_existing_analyses`
  appear under the `core` namespace and are in the agent's always-included set

#### Scenario: Always-included selection tracks the core tools' registered names

- **WHEN** the agent's always-included tool selection (`ALWAYS_INCLUDE_MCP_TOOLS`) is resolved
  against the live mounted tool registry, after the core tools are namespaced
- **THEN** it includes exactly the three core discovery tools by their registered (namespaced)
  names and excludes the removed `inspect_data_quality`; a rename or re-namespacing of a core
  tool SHALL NOT silently drop it from the always-included set — the selection is matched
  prefix-aware and drift-guarded against the live registry, not by a stale hand-copied literal
  that no longer matches

#### Scenario: The web client's hidden-tools list and the routing prompt stay in sync with namespacing

- **WHEN** the web client's `HIDDEN_TOOLS` set (`web/components/mcp-chat-client.tsx`, which
  filters the three core discovery tools out of the user-visible "available tools" list) and the
  LangChain routing prompt's `CONTEXT_MCP` tool catalog (`langchain/tools/context_tools.py`) are
  checked after the core tools are namespaced
- **THEN** `HIDDEN_TOOLS` matches the three core tools by their namespaced names (prefix-aware,
  same guarantee as `ALWAYS_INCLUDE_MCP_TOOLS`) rather than the pre-namespacing unprefixed
  literals, and `CONTEXT_MCP` no longer names any retired workflow, retired correlation tool, or
  `inspect_data_quality` — it retains only routing guidance, not a hand-maintained per-tool catalog

#### Scenario: inspect_data_quality is removed

- **WHEN** the tool surface is listed after the change
- **THEN** `inspect_data_quality` SHALL NOT be present, and `qc_inspect` SHALL cover its use

### Requirement: Phase-1 Workflow Tools Retired

The Phase-1 legacy "workflow" tools SHALL be removed from bloom-mcp: `run_qc_workflow`,
`run_outlier_workflow`, `run_descriptive_stats_workflow`,
`run_dimensionality_reduction_workflow`, and `run_clustering_workflow`, along with their
modules under `tools/workflows/` and their `server.py` registrations. These tools duplicated
capabilities of the granular tools and/or the upstream library, some were broken, and they
were the sole consumers of the vendored analysis modules. `run_clustering_workflow`'s
capability is already covered by the surviving granular `clustering` tool (#309/#422); the
other capabilities not covered by a surviving granular tool (UMAP embedding, cross-method
outlier comparison, descriptive stats tables, cross-experiment correlations) are intentionally
dropped and MAY be re-added later only as thin section tools delegating to
`sleap_roots_analyze`.

#### Scenario: Retired workflow tools are absent from the surface

- **WHEN** a FastMCP `Client` lists tools after the change
- **THEN** none of the five `run_*_workflow` tools SHALL be present, and the `tools/workflows/`
  package SHALL NOT exist

#### Scenario: No dangling registration or import

- **WHEN** the server boots and CI imports the built wheel
- **THEN** boot and import SHALL succeed with no reference to a retired workflow module or a
  deleted vendored module

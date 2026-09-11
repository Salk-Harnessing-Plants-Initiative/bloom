## MODIFIED Requirements

### Requirement: Heritability and UMAP Analysis Delegated to sleap-roots-analyze

The shipped trait-statistics/heritability and UMAP-embedding paths SHALL source their
analysis from `sleap_roots_analyze` rather than vendored copies. The vendored
`src/bloom_mcp/umap_embedding.py` and `src/bloom_mcp/trait_statistics.py` SHALL be
deleted. The external behavior of the MCP tools that use them (parameter and output
schema exposed to the agent) SHALL be unchanged, and their numerical output SHALL match
the committed turface_19 golden values within the stated tolerance, asserted by the
cross-tier oracle. The heritability golden SHALL be labeled as either an independently
reconciled reference value or an explicit characterization snapshot **naming the
`sleap-roots-analyze` version it was recorded against** (a drift gate), and its `_source` SHALL
point at a real heritability artifact. The UMAP gate SHALL assert a structural invariant (not
merely output shape), and the **two affected tool paths — the shipped heritability consumer and
the UMAP tool —** SHALL have their delegated return keys/units asserted so a library key-rename
fails rather than silently zero-filling.

The heritability consumer whose delegated return keys this requirement pins is
`heritability_analysis` (`sections/sleap_roots/analysis/heritability_analysis.py`). It replaced
the retired `plot_heritability_bar` / `plot_variance_decomposition` wrappers, which are no longer
registered tools and no longer exist; the `var_genetic` / `var_residual` guard moved to the
replacement rather than being dropped. Because
`HeritabilityResult.from_heritability_dict` defaults a missing `var_genetic` / `var_residual` /
`n_genotypes` to `0`, key **presence** SHALL be validated on every code path — not only on the
plot path and not only for finiteness — so a renamed upstream key can never be emitted as a
zero-valued variance component.

The version literal in the first paragraph was previously the fixed string `0.1.0a2`. It is
generalized to "naming the version it was recorded against" because this change adds a second
heritability golden recorded at a later pinned version; the obligation to record a version is
strengthened, not relaxed.

#### Scenario: Delegated paths reproduce the golden within tolerance

- **WHEN** the shipped `bloom_mcp` heritability and UMAP paths run on the committed
  turface_19 fixture after delegation
- **THEN** their outputs SHALL match the committed golden values within the stated
  tolerance
- **AND** the same assertion SHALL hold for the external `sleap_roots_analyze` functions
  they delegate to
- **AND** the heritability golden SHALL be documented as an independently reconciled value
  or an explicit characterization snapshot **that records the `sleap-roots-analyze` version
  producing it**, with a `_source` pointing at a real heritability artifact (not a
  PCA-metadata file)

#### Scenario: UMAP delegation is gated on a structural invariant

- **WHEN** the UMAP oracle runs on the committed fixture after delegation
- **THEN** it SHALL assert a structural invariant against a recorded embedding (e.g.
  Procrustes-aligned coordinates or a kNN-overlap / trustworthiness check), not merely
  output shape plus within-process self-equality
- **AND** a delegation using the wrong `n_neighbors` / `min_dist` / `init` SHALL fail the
  gate even if it produces a same-shape deterministic embedding

#### Scenario: Tool wrappers assert the delegated return keys

- **WHEN** the `heritability_analysis` and UMAP MCP tools are exercised on the committed fixture
- **THEN** the test SHALL assert the delegated return contains the keys the tools consume —
  including `var_genetic` and `var_residual`, which `heritability_analysis` reads for its
  variance-decomposition figure
- **AND** a renamed or dropped key SHALL fail the test rather than silently defaulting to
  zero

#### Scenario: A dropped key is routed as a failure, not emitted as zero

- **WHEN** the delegate returns a per-trait entry carrying a heritability value but missing
  `var_genetic`, `var_residual`, or `n_genotypes`, on the default path with no figures requested
- **THEN** `heritability_analysis` SHALL route that trait to its failed set and SHALL NOT emit it
  with a zero-valued variance component inline, in the persisted per-trait table, or in the
  persisted result JSON

#### Scenario: Vendored modules removed without changing the tool surface

- **WHEN** the package is inspected after delegation
- **THEN** `src/bloom_mcp/umap_embedding.py` and `src/bloom_mcp/trait_statistics.py`
  SHALL NOT exist
- **AND** the UMAP and statistics/heritability MCP tools SHALL expose the same parameters
  and output schema to the agent as before delegation, except for `plot_heritability_bar`
  and `plot_variance_decomposition`, retired into `heritability_analysis` by
  `add-bloommcp-heritability-analysis-tool` (#462)

#### Scenario: A drift between the vendored copy and the library is caught before deletion

- **WHEN** the oracle is extended to the delegated paths while the vendored modules still
  exist
- **THEN** any numerical divergence beyond tolerance between the vendored copy and
  `sleap_roots_analyze` SHALL fail the gate
- **AND** delegation SHALL proceed only once the gate is green

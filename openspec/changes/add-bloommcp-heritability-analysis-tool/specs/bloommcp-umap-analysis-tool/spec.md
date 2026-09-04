## MODIFIED Requirements

### Requirement: Tool registration and discovery

The system SHALL register `umap_analysis` as a discoverable MCP tool in the `sleap_roots`
section, alongside its sibling `sleap-roots-analyze` consumers, namespaced
`sleap_roots_umap_analysis` on the combined server surface.

#### Scenario: Tool is discoverable with a valid schema

- **WHEN** the MCP server's tool list is queried
- **THEN** `sleap_roots_umap_analysis` appears with a non-null input schema

#### Scenario: Sibling analysis tools are unaffected

- **WHEN** the MCP server's tool list is queried after `umap_analysis` is added
- **THEN** every other `sleap_roots` analysis tool (`pca_analysis`, `qc_clean`,
  `qc_inspect`, `remove_outliers`, `clustering`, and the 3 surviving plotting tools —
  `plot_trait_histograms`, `plot_trait_boxplots`, `plot_correlation_matrix`) is still present
  and unchanged. `plot_heritability_bar` and `plot_variance_decomposition` are deliberately
  **not** in that set: they were retired into `heritability_analysis`.

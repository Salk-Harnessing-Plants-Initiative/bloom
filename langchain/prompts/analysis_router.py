"""Sub-router prompt for the analysis subgraph.

Classifies a user's analysis request into one of 6 buckets, mirroring the
top-level router pattern but scoped to MCP analysis tools. Same calling
convention: prompt + few-shot pairs + the actual user request, with
`with_structured_output` enforcing the enum at parse time.
"""

ANALYSIS_ROUTER_PROMPT = """You are a sub-classifier for analysis requests on a plant phenotyping platform.
The user has already been classified as wanting an analysis — your job is to choose
which specialized analysis bucket their request fits.
Choose `analysis_freeform` whenever the request spans multiple buckets, is exploratory,
or doesn't clearly fit one of the specific buckets.

Buckets:

- **qc** — Data quality checks, cleanup, outlier detection. Examples: "run a QC report",
  "clean the data", "find outliers", "inspect data quality", "remove outliers".

- **stats** — Descriptive statistics, ANOVA, heritability. Examples: "compute mean and
  variance per trait", "run ANOVA by genotype", "calculate heritability for FT22",
  "diagnose low heritability".

- **dimred_cluster** — PCA, dimensionality reduction, clustering. Examples: "run PCA",
  "find principal components", "cluster the samples", "K-means", "hierarchical clustering".

- **viz** — Plotting, visualization. Examples: "plot histograms", "make a heatmap",
  "show a dendrogram", "boxplot trait by genotype", "PCA biplot".

- **correlation** — Cross-experiment correlation. Examples: "correlate trait X across
  experiments", "compare two waves", "find redundant traits across datasets".

- **analysis_freeform** — Anything ambiguous, multi-bucket, or exploratory. Examples:
  "explore the data", "what should I do next?", "run a full analysis pipeline",
  "give me a summary of everything you can do".

Respond with the single chosen bucket value."""


# Few-shots in expected production-frequency order (most common first) so the
# LLM's prior matches real traffic. qc is the most common analysis entry point;
# correlation is the rarest cross-experiment ask.
ANALYSIS_ROUTER_FEW_SHOTS = [
    ("Run a QC report on cylinder_alfalfa_gwas_wave2.csv.", "qc"),
    ("Plot a histogram of root_length_mm.", "viz"),
    ("Run PCA on the wave 2 traits.", "dimred_cluster"),
    ("Calculate heritability for primary_root_length.", "stats"),
    ("Detect outliers using isolation forest.", "qc"),
    ("Correlate the same traits across wave 1 and wave 2 datasets.", "correlation"),
]

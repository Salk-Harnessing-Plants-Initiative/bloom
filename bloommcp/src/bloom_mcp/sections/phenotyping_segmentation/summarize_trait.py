"""summarize_trait — per-accession summary of one phenotyping trait.

One tool per file: define the input/output models and the ``@as_mcp_tool``
function here, then register it in this package's ``__init__``.
"""

from pydantic import BaseModel, Field

from bloom_mcp.contract import BloomMCPError, Provenance, as_mcp_tool
from bloom_mcp.tools import _ports


class SummarizeTraitParams(BaseModel):
    """Inputs for ``summarize_trait``."""

    experiment: str = Field(
        ..., description="CSV filename from list_available_experiments."
    )
    trait: str = Field(
        ..., description="Trait column to summarize (e.g. root_area_mean)."
    )


class AccessionTraitStats(BaseModel):
    accession: str = Field(..., description="Accession / genotype label.")
    n: int = Field(..., description="Number of plants summarized.")
    mean: float
    std: float


class SummarizeTraitResult(BaseModel):
    experiment: str
    trait: str
    grouped_by: str = Field(..., description="Column the stats are grouped by.")
    by_accession: list[AccessionTraitStats]


@as_mcp_tool(input_model=SummarizeTraitParams, output_model=SummarizeTraitResult)
def summarize_trait(
    params: SummarizeTraitParams, *, provenance: Provenance
) -> SummarizeTraitResult:
    """Summarize one phenotyping trait per accession for an experiment.

    Groups the experiment by its accession/genotype column and returns
    n / mean / std of the requested trait per accession. Use after
    list_available_experiments + load_experiment_data to pick the experiment
    and the trait column.
    """
    df, _trait_cols, config, source = _ports.load_frame(params.experiment)
    if df is None:
        raise BloomMCPError(
            code="invalid_input",
            message=f"Could not load experiment {params.experiment!r}: {source}",
            remedy="Use a filename from list_available_experiments.",
        )
    if params.trait not in df.columns:
        raise BloomMCPError(
            code="invalid_input",
            message=f"Trait {params.trait!r} is not a column in {params.experiment!r}.",
            remedy="Use a trait column from load_experiment_data.",
        )

    group_col = config.get("genotype_col") or config.get("sample_id_col")
    if not group_col or group_col not in df.columns:
        raise BloomMCPError(
            code="invalid_input",
            message=f"No accession/genotype column detected for {params.experiment!r}.",
            remedy="Ensure the experiment has a genotype or sample-id column.",
        )

    rows = [
        AccessionTraitStats(
            accession=str(name),
            n=int(values.count()),
            mean=float(values.mean()),
            std=float(values.std(ddof=1)) if values.count() > 1 else 0.0,
        )
        for name, values in df.groupby(group_col)[params.trait]
    ]
    return SummarizeTraitResult(
        experiment=params.experiment,
        trait=params.trait,
        grouped_by=group_col,
        by_accession=rows,
    )

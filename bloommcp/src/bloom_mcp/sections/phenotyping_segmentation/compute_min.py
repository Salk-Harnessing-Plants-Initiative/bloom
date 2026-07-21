"""compute_min — demo tool: minimum of a numbers .txt file.

One tool per file: define the input/output models and the ``@as_mcp_tool``
function here, then register it in this package's ``__init__``.
"""

from pydantic import BaseModel, Field

from bloom_mcp.contract import Provenance, as_mcp_tool

from . import _demo_stats


class ComputeMinParams(BaseModel):
    """Inputs for ``compute_min``."""

    filename: str = Field(
        ..., description="Numbers .txt file in BLOOM_TRAITS_DIR (or an absolute path)."
    )


class ComputeMinResult(BaseModel):
    source_file: str
    n: int = Field(..., description="Count of numbers read.")
    minimum: float
    result_path: str = Field(..., description="Result file written under the output dir.")


@as_mcp_tool(input_model=ComputeMinParams, output_model=ComputeMinResult)
def compute_min(
    params: ComputeMinParams, *, provenance: Provenance
) -> ComputeMinResult:
    """Read a numbers .txt file and write its minimum to the output dir.

    Demo tool: reads whitespace-separated numbers from the file, computes the
    minimum, and writes a result file into ``results/`` under BLOOM_OUTPUT_DIR.
    """
    numbers = _demo_stats.read_numbers(params.filename)
    minimum = min(numbers)
    result_path = _demo_stats.write_result("min", params.filename, str(minimum))
    return ComputeMinResult(
        source_file=params.filename,
        n=len(numbers),
        minimum=minimum,
        result_path=result_path,
    )

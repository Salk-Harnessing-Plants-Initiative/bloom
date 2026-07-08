"""compute_median — demo tool: median of a numbers .txt file.

One tool per file: define the input/output models and the ``@as_mcp_tool``
function here, then register it in this package's ``__init__``.
"""

from statistics import median

from pydantic import BaseModel, Field

from bloom_mcp.contract import Provenance, as_mcp_tool

from . import _demo_stats

#Declares what the tool accepts: a single filename string.
class ComputeMedianParams(BaseModel):
    """Inputs for ``compute_median``."""

    filename: str = Field(
        ..., description="Numbers .txt file in BLOOM_TRAITS_DIR (or an absolute path)."
    )

# the output model
# Declares the shape of what comes back: 
# the source file, n (how many numbers were read), 
# the median, and result_path (where the result file was written).
class ComputeMedianResult(BaseModel):
    source_file: str
    n: int = Field(..., description="Count of numbers read.")
    median: float
    result_path: str = Field(..., description="Result file written under the output dir.")


@as_mcp_tool(input_model=ComputeMedianParams, output_model=ComputeMedianResult)
def compute_median(
    params: ComputeMedianParams, *, provenance: Provenance
) -> ComputeMedianResult:
    """Read a numbers .txt file and write its median to the output dir.

    Demo tool: reads whitespace-separated numbers from the file, computes the
    median, and writes a result file into ``results/`` under BLOOM_OUTPUT_DIR.
    """
    numbers = _demo_stats.read_numbers(params.filename)
    value = float(median(numbers))
    result_path = _demo_stats.write_result("median", params.filename, str(value))
    return ComputeMedianResult(
        source_file=params.filename,
        n=len(numbers),
        median=value,
        result_path=result_path,
    )

"""compute_mode — demo tool: mode(s) of a numbers .txt file.

One tool per file: define the input/output models and the ``@as_mcp_tool``
function here, then register it in this package's ``__init__``.
"""

from statistics import multimode

from pydantic import BaseModel, Field

from bloom_mcp.contract import Provenance, as_mcp_tool

from . import _demo_stats


class ComputeModeParams(BaseModel):
    """Inputs for ``compute_mode``."""

    filename: str = Field(
        ..., description="Numbers .txt file in BLOOM_TRAITS_DIR (or an absolute path)."
    )


class ComputeModeResult(BaseModel):
    source_file: str
    n: int = Field(..., description="Count of numbers read.")
    modes: list[float] = Field(
        ..., description="Most frequent value(s); more than one if tied."
    )
    result_path: str = Field(..., description="Result file written under the output dir.")


@as_mcp_tool(input_model=ComputeModeParams, output_model=ComputeModeResult)
def compute_mode(
    params: ComputeModeParams, *, provenance: Provenance
) -> ComputeModeResult:
    """Read a numbers .txt file and write its mode(s) to the output dir.

    Demo tool: reads whitespace-separated numbers from the file, computes the
    mode(s) — all values tied for most frequent — and writes a result file into
    ``results/`` under BLOOM_OUTPUT_DIR.
    """
    numbers = _demo_stats.read_numbers(params.filename)
    modes = [float(m) for m in multimode(numbers)]
    result_path = _demo_stats.write_result(
        "mode", params.filename, ", ".join(str(m) for m in modes)
    )
    return ComputeModeResult(
        source_file=params.filename,
        n=len(numbers),
        modes=modes,
        result_path=result_path,
    )

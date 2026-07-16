"""Shared I/O for the demo min/median/mode tools.

Not a tool (leading underscore = not registered). The three demo tools read a
whitespace/newline-separated numbers .txt file from ``BLOOM_TRAITS_DIR`` and
write their result into a ``results/`` folder under ``BLOOM_OUTPUT_DIR`` — the
sample path Lin's demo exercises through the new per-section route.
"""

from __future__ import annotations

from pathlib import Path

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.experiment_utils import OUTPUT_DIR, TRAITS_DIR

# Where each tool drops its result file, under the configured output directory.
RESULTS_DIR = OUTPUT_DIR / "results"


def read_numbers(filename: str) -> list[float]:
    """Read whitespace/newline-separated numbers from ``filename``.

    Resolves an absolute path as-is, otherwise against ``BLOOM_TRAITS_DIR``.
    Raises ``BloomMCPError(invalid_input)`` on a missing file or non-numeric
    token so the agent gets a structured, actionable error.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = TRAITS_DIR / filename
    if not path.is_file():
        raise BloomMCPError(
            code="invalid_input",
            message=f"Numbers file {filename!r} not found (looked at {path}).",
            remedy="Provide a .txt file that exists in BLOOM_TRAITS_DIR.",
        )

    numbers: list[float] = []
    for token in path.read_text().split():
        try:
            numbers.append(float(token))
        except ValueError:
            raise BloomMCPError(
                code="invalid_input",
                message=f"Non-numeric value {token!r} in {filename!r}.",
                remedy="Ensure the file holds only whitespace-separated numbers.",
            ) from None
    if not numbers:
        raise BloomMCPError(
            code="invalid_input",
            message=f"No numbers found in {filename!r}.",
            remedy="Provide a file with at least one number.",
        )
    return numbers


def write_result(statistic: str, source_file: str, value: str) -> str:
    """Write ``<statistic>_<stem>.txt`` into the results dir; return its path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{statistic}_{Path(source_file).stem}.txt"
    out_path.write_text(f"{statistic}({source_file}) = {value}\n")
    return str(out_path)

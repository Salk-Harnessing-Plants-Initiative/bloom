"""Shared I/O for the demo min/median/mode tools.

Not a tool (leading underscore = not registered). The three demo tools read a
whitespace/newline-separated numbers .txt file from ``BLOOM_TRAITS_DIR`` and
write their result into a ``results/`` folder under ``BLOOM_OUTPUT_DIR`` — the
sample path Lin's demo exercises through the new per-section route.
"""

from __future__ import annotations

import os
from pathlib import Path

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.experiment_utils import OUTPUT_DIR, resolve_experiment_local_root
from bloom_mcp.storage_backend import is_local_backend


def _output_root() -> Path:
    """``BLOOM_OUTPUT_DIR``, falling back to ``<BLOOM_LOCAL_ROOT>/output`` in
    fully-local mode (#479) — this demo has no ``BLOOM_STORAGE_LOCAL_ROOT``
    -equivalent explicit override of its own, so it only needs the
    ``BLOOM_LOCAL_ROOT`` tier, not the full ``storage_backend._resolve_local_root``
    precedence. Computed fresh on each call — not a frozen module constant —
    so it reflects the env at call time, not at import time.
    """
    if os.getenv("BLOOM_OUTPUT_DIR"):
        return OUTPUT_DIR
    local_root = os.getenv("BLOOM_LOCAL_ROOT")
    if local_root and is_local_backend():
        return Path(local_root) / "output"
    return OUTPUT_DIR


def read_numbers(filename: str) -> list[float]:
    """Read whitespace/newline-separated numbers from ``filename``.

    Resolves an absolute path as-is, otherwise against the configured local
    input root (``BLOOM_EXPERIMENT_LOCAL_ROOT`` / ``BLOOM_LOCAL_ROOT`` /
    ``BLOOM_TRAITS_DIR`` — see ``resolve_experiment_local_root``). Raises
    ``BloomMCPError(invalid_input)`` on a missing file or non-numeric token so
    the agent gets a structured, actionable error.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = resolve_experiment_local_root() / filename
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
    results_dir = _output_root() / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"{statistic}_{Path(source_file).stem}.txt"
    out_path.write_text(f"{statistic}({source_file}) = {value}\n")
    return str(out_path)

"""Shared helpers used by every workflow."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from storage import AnalysisWriter

# Logical prefix inside the bloommcp-data bucket for all versioned analysis
# outputs. After the storage migration this replaces the BLOOM_OUTPUT_DIR
# bind-mounted local path.
_STORAGE_OUTPUT_PREFIX = "bloommcp_output"


def build_writer(
    experiment_filename: str,
    tool_class: str,
    source_csv: Optional[Path] = None,
) -> AnalysisWriter:
    """Construct an AnalysisWriter pointing at the canonical output prefix.

    Every workflow uses this helper so the prefix + writer construction
    logic lives in exactly one place. `source_csv` is the optional raw
    experiment CSV path — passed through for input-SHA256 capture in the
    manifest entry.
    """
    return AnalysisWriter(
        output_root=_STORAGE_OUTPUT_PREFIX,
        experiment_filename=experiment_filename,
        tool_class=tool_class,
        source_csv=source_csv,
    )

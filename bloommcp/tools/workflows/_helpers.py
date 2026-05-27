"""Shared helpers used by every workflow."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from storage import AnalysisWriter


def build_writer(
    experiment_filename: str,
    tool_class: str,
    source_csv: Optional[Path] = None,
) -> AnalysisWriter:
    """Construct an AnalysisWriter pointing at the canonical output root.

    Every workflow uses this helper so the OUTPUT_DIR resolution + writer
    construction logic lives in exactly one place. `source_csv` is the
    optional raw experiment CSV path — passed through for input-SHA256
    capture in the manifest entry.

    `BLOOM_OUTPUT_DIR` is read directly rather than via experiment_utils so
    this helper has no pandas import chain — keeps unit tests light.
    """
    output_root = Path(os.environ["BLOOM_OUTPUT_DIR"])
    return AnalysisWriter(
        output_root=output_root,
        experiment_filename=experiment_filename,
        tool_class=tool_class,
        source_csv=source_csv,
    )

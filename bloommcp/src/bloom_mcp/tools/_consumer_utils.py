"""Shared internal helpers for bloom-mcp consumer tools.

Not public API — import only from within ``bloom_mcp.tools``.

``_build_output_frame(frame, payload_df)``
    Prepend a frame's ``metadata_cols`` (sample-identity columns) to a payload
    DataFrame, reset the index, and return the combined result.  Mirrors what
    ``pca_analysis._scores_frame`` did in-line, and what the forthcoming
    ``clustering`` tool will need for its labels frame.

``snapshot_frame(df)``
    Context manager: write *df* to a temporary CSV (``index=False``) and yield
    its ``Path``.  The ``TemporaryDirectory`` is guaranteed to be cleaned up on
    block exit — even if an exception is raised — so the snapshot outlives the
    ``ResultStore.commit()`` call (which hashes the file) without the caller
    managing lifetime manually.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Generator

import pandas as pd

if TYPE_CHECKING:
    from bloom_mcp.reader.ports import ExperimentFrame

_SNAPSHOT_NAME = "source_snapshot.csv"


def _build_output_frame(
    frame: ExperimentFrame, payload_df: pd.DataFrame
) -> pd.DataFrame:
    """Prepend ``frame.metadata_cols`` identity columns to *payload_df*.

    Resets the index on the identity slice so positional alignment is
    guaranteed regardless of the original ``frame.df`` index.
    """
    if not frame.metadata_cols:
        return payload_df
    identity = frame.df[frame.metadata_cols].reset_index(drop=True)
    return pd.concat([identity, payload_df], axis=1)


@contextmanager
def snapshot_frame(df: pd.DataFrame) -> Generator[Path, None, None]:
    """Write *df* to a temporary CSV and yield its path.

    The temporary directory (and the CSV inside it) is removed on block exit,
    even if an exception propagates.  ``TemporaryDirectory`` is used over
    ``NamedTemporaryFile`` because the latter cannot be re-read on Windows
    while still open.
    """
    with tempfile.TemporaryDirectory(prefix="bloom_snapshot_") as tmp:
        path = Path(tmp) / _SNAPSHOT_NAME
        df.to_csv(path, index=False)
        yield path

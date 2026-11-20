"""Where to write each cell type's name on a UMAP, as the website places it (umap-labels.ts):
on the densest part of its largest patch, not in the empty space between two islands."""

from __future__ import annotations

import numpy as np

# The map is split into this many squares a side to find each group's densest patch.
LABEL_GRID = 40
# A group needs this many cells in its densest square to be named.
MIN_LABEL_POINTS = 5


def anchors(
    x: np.ndarray,
    y: np.ndarray,
    codes: np.ndarray,
    n_levels: int,
    shown: np.ndarray | None,
) -> dict[int, tuple[float, float]]:
    """Each named group's anchor: the mean position of its cells in the first grid square
    holding most of them, if that is at least MIN_LABEL_POINTS. Codes outside
    0..n_levels-1, and cells not `shown`, belong to no group."""
    if n_levels <= 0 or len(codes) == 0:
        return {}
    span_x = float(x.max() - x.min()) or 1.0
    span_y = float(y.max() - y.min()) or 1.0
    bx = np.minimum(LABEL_GRID - 1, np.floor((x - x.min()) / span_x * LABEL_GRID))
    by = np.minimum(LABEL_GRID - 1, np.floor((y - y.min()) / span_y * LABEL_GRID))
    square = (by * LABEL_GRID + bx).astype(int)
    counted = (codes >= 0) & (codes < n_levels)
    if shown is not None:
        counted &= shown
    out: dict[int, tuple[float, float]] = {}
    for level in range(n_levels):
        mine = counted & (codes == level)
        if not mine.any():
            continue
        counts = np.bincount(square[mine], minlength=LABEL_GRID * LABEL_GRID)
        best = int(np.argmax(counts))
        if counts[best] < MIN_LABEL_POINTS:
            continue
        inside = mine & (square == best)
        out[level] = (float(x[inside].mean()), float(y[inside].mean()))
    return out

"""Colours for a UMAP coloured by group, as the website gives them: cell types in their stored
colour (umap-packing.ts), genotypes and replicates in the Integrations map's palette
(integration-lib/joint-map.ts)."""

from __future__ import annotations

import colorsys
import re
from collections.abc import Sequence

import numpy as np
from matplotlib.colors import to_rgb

Rgb = tuple[float, float, float]

# A cell type whose stored colour is missing or not a 6-digit hex, and cells of no type.
MISSING_COLOUR: Rgb = (0.5, 0.5, 0.5)
NO_CELL_TYPE_COLOUR: Rgb = (0.6, 0.6, 0.6)
# Cells with no value for the genotype or replicate: faint grey.
NO_VALUE = (0.45, 0.45, 0.48, 0.3)
# Twenty colours that read on the map's dark ground; later values go round the colour
# wheel by the golden angle, so neighbours never look alike.
BASE_PALETTE = (
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f",
    "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
    "#a0cbe8", "#ffbe7d", "#ff9d9a", "#86bcb6", "#8cd17d",
    "#f1ce63", "#d4a6c8", "#fabfd2", "#d7b5a6", "#79706e",
)  # fmt: skip
GOLDEN_ANGLE = 137.508
_HEX6 = re.compile(r"#?[0-9a-fA-F]{6}")


def palette(n: int) -> list[Rgb]:
    """A colour per value, the same for a value whatever how many there are."""
    out = []
    for i in range(n):
        if i < len(BASE_PALETTE):
            out.append(to_rgb(BASE_PALETTE[i]))
        else:
            hue = ((i - len(BASE_PALETTE)) * GOLDEN_ANGLE) % 360 / 360
            out.append(colorsys.hls_to_rgb(hue, 0.62, 0.6))
    return out


def stored_colour(value: str | None) -> Rgb:
    """A cell type's stored colour, or grey when it is missing or not a 6-digit hex."""
    if not value or not _HEX6.fullmatch(value):
        return MISSING_COLOUR
    return to_rgb("#" + value.lstrip("#"))


def codes(values: Sequence[str | None]) -> tuple[np.ndarray, list[str]]:
    """Each cell's index into the values in name order, -1 for no value; and those names."""
    names = sorted({v for v in values if v}, key=lambda v: (v.casefold(), v))
    index = {name: i for i, name in enumerate(names)}
    return np.array([index.get(v, -1) if v else -1 for v in values]), names

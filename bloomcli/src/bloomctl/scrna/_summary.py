"""What a checked h5ad holds, as the lines `hdf5 upload` shows before it sends anything."""

from __future__ import annotations

from typing import Any

from ._format import Summary

# A long list of obs columns wraps under its label rather than running off the terminal.
LINE_WIDTH = 100


def describe(summary: Summary, name: str) -> list[str]:
    """One line per part of the file, under the file's name."""
    rows = [
        ("cells", f"{summary.n_cells:,}"),
        ("genes", f"{summary.n_genes:,}"),
        ("UMAP", _umap(summary)),
        ("layers", ", ".join(sorted(summary.layers)) or "none"),
        ("normalization", _normalization(summary.normalization)),
    ]
    width = max(len(label) for label, _ in rows + [("obs columns", "")])
    lines = [name, *(f"  {label.ljust(width)}  {value}" for label, value in rows)]
    columns = _wrap(list(summary.obs_columns), LINE_WIDTH - width - 4) or ["none"]
    lines.append(f"  {'obs columns'.ljust(width)}  {columns[0]}")
    lines.extend(" " * (width + 4) + more for more in columns[1:])
    return lines


def _wrap(names: list[str], width: int) -> list[str]:
    """Names joined by commas, broken between names so none is split across lines."""
    lines: list[str] = []
    for name in names:
        if lines and len(lines[-1]) + len(name) + 2 <= width:
            lines[-1] += f", {name}"
        else:
            if lines:
                lines[-1] += ","
            lines.append(name)
    return lines


def _umap(summary: Summary) -> str:
    if summary.umap_key:
        return f"obsm['{summary.umap_key}']"
    held = ", ".join(summary.obsm) or "nothing"
    return f"none — cells will have no position on the map (obsm holds: {held})"


def _normalization(block: dict[str, Any] | None) -> str:
    if block is None:
        return "none in the file — accepted only if a dataset loaded from it records one"
    parts = [str(block["transform"]), str(block["scaling"])]
    if block["scaling"] == "library_size":
        parts.append(f"target_sum {block['target_sum']:g}")
    if block.get("description"):
        parts.append(str(block["description"]))
    if block.get("counts_layer"):
        parts.append(f"raw counts in layers['{block['counts_layer']}']")
    return ", ".join(parts)

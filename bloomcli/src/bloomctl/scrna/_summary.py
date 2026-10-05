"""What a checked h5ad holds, as the lines `hdf5 upload` shows before it sends anything."""

from __future__ import annotations

from typing import Any

from ._format import Summary

# A long list of obs columns wraps under its label rather than running off the terminal.
LINE_WIDTH = 100


def describe(
    summary: Summary, name: str, *, recorded_on: dict[str, Any] | None = None
) -> list[str]:
    """One line per part of the file, under the file's name.

    Every string from the file is shown with its control characters escaped, so a name in the
    file cannot move the cursor or start a line of its own in the summary the user confirms.
    """
    rows = [
        ("cells", f"{summary.n_cells:,}"),
        ("genes", f"{summary.n_genes:,}"),
        ("UMAP", _umap_text(summary)),
        ("layers", ", ".join(visible(layer) for layer in sorted(summary.layers)) or "none"),
        ("normalization", _normalization_text(summary.normalization, recorded_on)),
    ]
    width = max(len(label) for label, _ in rows + [("obs columns", "")])
    lines = [visible(name), *(f"  {label.ljust(width)}  {value}" for label, value in rows)]
    columns = _wrap([visible(c) for c in summary.obs_columns], LINE_WIDTH - width - 4) or ["none"]
    lines.append(f"  {'obs columns'.ljust(width)}  {columns[0]}")
    lines.extend(" " * (width + 4) + more for more in columns[1:])
    return lines


def visible(text: str) -> str:
    """``text`` with every non-printable character written as its escape, e.g. ``\\x1b``."""
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text
    )


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


def _umap_text(summary: Summary) -> str:
    if summary.umap_key:
        return f"obsm['{visible(summary.umap_key)}']"
    held = ", ".join(visible(key) for key in summary.obsm) or "nothing"
    return f"none (obsm holds: {held})"


def _normalization_text(block: dict[str, Any] | None, recorded_on: dict[str, Any] | None) -> str:
    if block is None:
        if recorded_on is None:
            return "none in the file"
        dataset = visible(str(recorded_on.get("name") or "unnamed"))
        return f"none in the file; recorded on dataset {dataset} (id {recorded_on['id']})"
    parts = [visible(str(block["transform"])), visible(str(block["scaling"]))]
    if block["scaling"] == "library_size":
        parts.append(f"target_sum {block['target_sum']:g}")
    if block.get("description"):
        parts.append(visible(str(block["description"])))
    if block.get("counts_layer"):
        parts.append(f"raw counts in layers['{visible(str(block['counts_layer']))}']")
    return ", ".join(parts)

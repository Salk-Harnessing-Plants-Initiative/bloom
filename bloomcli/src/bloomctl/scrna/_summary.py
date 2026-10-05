"""What a checked h5ad holds and what loading it will do, as shown before anything is sent."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ._format import Summary

# A long list wraps under its label rather than running off the terminal.
LINE_WIDTH = 100

# The longest label, so every value starts in the same column.
LABEL_WIDTH = len("normalization")

# Two spaces before the label and two after it.
INDENT = 4


def describe(
    summary: Summary, name: str, *, recorded_on: dict[str, Any] | None = None
) -> list[str]:
    """One line per part of the file, under the file's name.

    Every string from the file is shown with its control characters escaped, so a name in the
    file cannot move the cursor or start a line of its own in the summary the user confirms.
    """
    return [
        visible(name),
        *_row("cells", f"{summary.n_cells:,}"),
        *_row("genes", f"{summary.n_genes:,}"),
        *_row("UMAP", _umap_text(summary)),
        *_row("layers", ", ".join(visible(layer) for layer in sorted(summary.layers)) or "none"),
        *_row("normalization", _normalization_text(summary.normalization, recorded_on)),
        *_list_row("obs columns", [visible(c) for c in summary.obs_columns]),
    ]


def describe_load(
    cells: dict, dataset: str, genotypes: list[dict], facet_columns: tuple[str, ...]
) -> list[str]:
    """What the load will write: the dataset, its cell types, samples, genotypes and labels."""
    samples = Counter(cells["samples"])
    levels = [visible(level) for level in cells["levels"]]
    levels[0] = f"{len(levels)}: {levels[0]}"
    lines = [
        *_row("dataset", dataset),
        *_list_row("cell types", levels),
        *_list_row("samples", [f"{visible(s)} {n:,}" for s, n in sorted(samples.items())]),
    ]
    if genotypes:
        lines += _list_row("genotypes", [_genotype_text(g) for g in genotypes])
    for i, column in enumerate(facet_columns):
        counts = Counter(f[column] for f in cells["facets"])
        values = ", ".join(f"{visible(v)} {n:,}" for v, n in sorted(counts.items()))
        lines += _row("labels" if i == 0 else "", f"{visible(column)}: {values}")
    if cells.get("sources"):
        per_source = Counter(cells["sources"].values())
        lines += _list_row("label sources", [
            f"{visible(s)} ({n} cell type{'' if n == 1 else 's'})"
            for s, n in sorted(per_source.items())
        ], sep=";")
    return lines


def visible(text: str) -> str:
    """``text`` with every non-printable character written as its escape, e.g. ``\\x1b``."""
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text
    )


def _row(label: str, value: str) -> list[str]:
    return [f"  {label.ljust(LABEL_WIDTH)}  {value}"]


def _list_row(label: str, names: list[str], sep: str = ",") -> list[str]:
    """Names under one label, wrapped between names so none is split across lines."""
    wrapped = _wrap(names, LINE_WIDTH - LABEL_WIDTH - INDENT, sep) or ["none"]
    return [*_row(label, wrapped[0]), *(" " * (LABEL_WIDTH + INDENT) + w for w in wrapped[1:])]


def _wrap(names: list[str], width: int, sep: str = ",") -> list[str]:
    """Names joined by ``sep``; a line break comes between names, after the separator."""
    lines: list[str] = []
    for name in names:
        # +3 counts the separator and space before the name, and the separator a break
        # would leave after it.
        if lines and len(lines[-1]) + len(name) + 3 <= width:
            lines[-1] += f"{sep} {name}"
        else:
            if lines:
                lines[-1] += sep
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


def _genotype_text(genotype: dict) -> str:
    name = visible(genotype["name"])
    if genotype["is_control"]:
        return f"{name} (control)"
    return f"{name} ({visible(genotype['construct'])})" if genotype["construct"] else name

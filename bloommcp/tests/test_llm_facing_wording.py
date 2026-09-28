"""Guard the LLM-facing experiment-identifier wording against regression (#552).

Tier 2 (#551) moved the default ``supabase`` backend's raw experiment read off
bucket CSVs onto direct Postgres reads. #571 then found that the tool text still
told the agent an experiment *is* a CSV file on disk, and #552 / PR #823 rewrote
it. Nothing stopped it drifting back: the corrected text was hand-verified prose
with no mechanism tying it to the code, and the sibling OpenSpec change's own
history is the cautionary tale — a task was checked off claiming a fix that
``git log -L`` later showed was never made, and it sat undetected until review.

This is the cheap first step that review asked for. It does not catch semantic
drift, only the literal phrasings that have actually regressed before — but that
is exactly the class #571 suffered, and it costs nothing to keep.

**Scoped to docstrings and Pydantic ``description=`` strings, via AST, rather
than grepping the whole source.** Those two are the LLM-facing surface (FastMCP
sends the tool docstring and each field's description to the model), and scoping
to them avoids false-positiving on legitimate code that genuinely does concern
CSV files — ``supabase_client.read_input_csv``'s own docstring, the ``local``
backend's CSV tier, ``_cleaned.csv`` output names, and the ``csv_content``
inline-input parameter all mention CSVs correctly and must keep doing so.
"""

from __future__ import annotations

import ast
from pathlib import Path

import bloom_mcp

_SRC = Path(bloom_mcp.__file__).resolve().parent

# Each entry regressed for real. Keep this list short and evidenced — a phrase
# with no incident behind it belongs in review, not in a hard gate.
FORBIDDEN_PHRASES = {
    # The Pydantic Field wording #552 replaced in 5 analysis tools with
    # "Experiment identifier (from list_available_experiments)".
    "CSV filename": "#552 — say 'experiment identifier', not a filename",
    # load_experiment_data's tool docstring, missed by #552's first pass and
    # caught in PR #823's re-review.
    "SLEAP experiment CSV": "#552 — the default backend reads a DB row, not a CSV",
    "experiment CSV file": "#552 — the default backend reads a DB row, not a CSV",
}


def _llm_facing_strings(tree: ast.AST) -> list[tuple[int, str]]:
    """Every docstring and ``description=`` literal in one module, with line numbers."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append((getattr(node, "lineno", 1), doc))
        if (
            isinstance(node, ast.keyword)
            and node.arg == "description"
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            found.append((node.value.lineno, node.value.value))
    return found


def test_no_tool_text_calls_an_experiment_a_csv_file():
    """No docstring or field description may describe an experiment as a CSV file.

    RED against the pre-#823 tree, where it reports exactly the 6 sites #552 and
    PR #823's re-review fixed: `load_experiment_data.py` plus the `CSV filename`
    field descriptions in clustering / descriptive_stats / heritability_analysis /
    pca_analysis / umap_analysis.
    """
    offenders: list[str] = []
    for py in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for lineno, text in _llm_facing_strings(tree):
            lowered = text.lower()
            for phrase, why in FORBIDDEN_PHRASES.items():
                if phrase.lower() in lowered:
                    offenders.append(
                        f"{py.relative_to(_SRC)}:{lineno} — {phrase!r} ({why})"
                    )

    assert not offenders, (
        "LLM-facing text describes an experiment as a CSV file. Under the default "
        "`supabase` backend the identifier is a numeric experiment id resolved "
        "against Postgres, not a file on disk (Tier 2, #551):\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_inspects_something():
    """Anti-vacuity: a refactor that moved the package or broke the AST walk would
    make the test above pass by scanning nothing at all."""
    total = 0
    modules = 0
    for py in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        strings = _llm_facing_strings(tree)
        total += len(strings)
        modules += 1
    assert modules > 50, f"only {modules} modules scanned — is _SRC right?"
    assert total > 300, f"only {total} docstrings/descriptions found — AST walk broken?"

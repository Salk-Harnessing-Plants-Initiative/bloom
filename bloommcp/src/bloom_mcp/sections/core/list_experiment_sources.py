"""core_list_experiment_sources — discovery tool: which raw sources back an experiment.

Not a ``sleap-roots-analyze`` wrapper — a thin, isinstance-gated wrapper over
``SourceSelectable.list_sources()``. Not always-included (see
``ALWAYS_INCLUDE_MCP_TOOLS`` in ``langchain/helpers/foundational_tools.py``) —
it is an occasional discovery aid, not a foundational read path (#626).
"""

from bloom_mcp.data_access import ExperimentReadError, SourceSelectable
from bloom_mcp.tools import _ports


def list_experiment_sources(experiment: str) -> str:
    """List the distinct raw DB sources/pipeline-runs backing ``experiment``.

    Use this after ``list_available_experiments`` when you want to pin
    ``qc_clean``/``qc_inspect``/``load_experiment_data`` to a specific raw
    source instead of the latest one. Not applicable on a non-Supabase
    backend (e.g. a fully-local deployment).

    Args:
        experiment: experiment identifier from list_available_experiments
    """
    reader = _ports.reader()
    if not isinstance(reader, SourceSelectable):
        return (
            "Source selection is not applicable for the active backend — "
            "this deployment has no source-versioned raw data to choose "
            "between."
        )

    # An invalid/nonexistent experiment id (or a transient DB failure —
    # SupabaseReader.list_sources already maps those to a caller-safe
    # ExperimentReadError, never a raw traceback) must return an error string,
    # like every sibling source-pinning tool (qc_clean/qc_inspect/
    # load_experiment_data all route ExperimentReadError through the
    # as_mcp_tool contract or their own string-error convention) — not raise
    # uncaught, which this bare string-returning tool has no contract wrapper
    # to convert into a structured response.
    try:
        sources = reader.list_sources(experiment)
    except ExperimentReadError as exc:
        return str(exc)

    if len(sources) == 0:
        return (
            f"No source choice to make for {experiment!r}: it has no "
            "source-versioned raw data on record (it may not exist, or its "
            "data predates source tracking)."
        )
    if len(sources) == 1:
        return (
            f"{experiment!r} has only one source on record — no meaningful "
            "choice to make. qc_clean/qc_inspect/load_experiment_data will "
            "use it by default."
        )

    lines = [f"{len(sources)} sources available for {experiment!r}:\n"]
    for s in sources:
        lines.append(
            f"  source_id={s.source_id}"
            f"  source_name={s.source_name!r}"
            f"  pipeline_run_id={s.pipeline_run_id!r}"
        )
    lines.append(
        "\nTo pin one, pass source_id or run_id to qc_clean, qc_inspect, or "
        "load_experiment_data."
    )
    return "\n".join(lines)

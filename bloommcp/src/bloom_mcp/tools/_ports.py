"""Composition seam: the injected persistence ports for the tools layer.

Tools depend on :func:`reader` / :func:`store` here — never on Supabase,
``AnalysisWriter``, or ``AnalysisDir`` directly. Defaults to the Supabase
adapters; :func:`configure` swaps them at server boot or in tests. This module
is the one place that knows the concrete adapters (the composition root).
"""

from __future__ import annotations

from typing import Optional

import bloom_mcp.experiment_utils as _eu
from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import (
    ExperimentReader,
    ExperimentReadError,
    SupabaseReader,
)
from bloom_mcp.result_store import ResultStore, RunHandle, SupabaseResultStore

_reader: ExperimentReader = SupabaseReader()
_store: ResultStore = SupabaseResultStore()


def configure(
    *,
    reader: Optional[ExperimentReader] = None,
    store: Optional[ResultStore] = None,
) -> None:
    """Inject concrete adapters (server boot / tests). Unset args keep current."""
    global _reader, _store
    if reader is not None:
        _reader = reader
    if store is not None:
        _store = store


def reader() -> ExperimentReader:
    """The injected experiment reader."""
    return _reader


def store() -> ResultStore:
    """The injected result store."""
    return _store


def load_frame(filename: str) -> tuple:
    """Legacy 4-tuple read adapter for the discovery + workflow tools.

    Returns ``(df, trait_cols, config, source)`` on success, or
    ``(None, None, None, error_message)`` when the experiment cannot be loaded —
    preserving the contract the tools were written against.
    """
    try:
        frame = _reader.load_experiment(filename)
    except ExperimentReadError as exc:
        return None, None, None, str(exc)
    config = {
        "trait_cols": frame.trait_cols,
        "metadata_cols": frame.metadata_cols,
        "genotype_col": frame.genotype_col,
        "replicate_col": frame.replicate_col,
        "sample_id_col": frame.sample_id_col,
    }
    return frame.df, frame.trait_cols, config, frame.source


def start_run(
    filename: str,
    tool_class: str,
    tool_name: str,
    params: dict,
    *,
    user_label: Optional[str] = None,
    seed: Optional[int] = None,
) -> RunHandle:
    """Stamp a :class:`Provenance` and open a run on the injected store.

    Write outputs into the returned handle's ``staging_dir``, then
    ``store().commit(run, outputs)``.
    """
    src = _eu.TRAITS_DIR / filename
    provenance = Provenance.stamp(tool=tool_name, params=params, seed=seed)
    return _store.create_run(
        experiment=filename,
        tool_class=tool_class,
        provenance=provenance,
        user_label=user_label,
        source_csv=src if src.exists() else None,
    )

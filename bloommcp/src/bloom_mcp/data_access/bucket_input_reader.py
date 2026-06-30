"""BucketInputReader — additive fallback so experiments in the Supabase
``bloommcp_input/`` bucket are readable and discoverable.

A temporary compatibility layer, not a rewrite: it wraps the existing reader and ONLY adds
``bloommcp_input/`` as a fallback. The existing read path (cleaned-from-bucket,
legacy cleaned, local raw mount) is unchanged, so nothing currently working
breaks. When the Tier-2 input migration (#307) folds bucket input into
``load_experiment_data`` and retires ``BLOOM_TRAITS_DIR``, delete this layer.
"""

from __future__ import annotations

from bloom_mcp.experiment_utils import detect_columns
from bloom_mcp.supabase_client import INPUT_PREFIX, list_prefix, read_input_csv

from .ports import (
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReader,
    ExperimentSummary,
)


class BucketInputReader:
    """Wrap a base reader; add ``bloommcp_input/`` as a fallback source.

    - ``load_experiment``: try the base reader first; only if it can't find the
      experiment, read it from ``bloommcp_input/``.
    - ``list_experiments``: the base reader's entries, plus any
      ``bloommcp_input/`` CSVs the base reader didn't already list.
    """

    def __init__(self, base: ExperimentReader) -> None:
        self._base = base

    def load_experiment(
        self, name: str, *, version: str = "latest", require_clean: bool = False
    ) -> ExperimentFrame:
        try:
            return self._base.load_experiment(
                name, version=version, require_clean=require_clean
            )
        except ExperimentNotFoundError:
            return self._load_bucket_input(name)

    def list_experiments(self) -> list[ExperimentSummary]:
        base_list = self._base.list_experiments()
        seen = {entry.filename for entry in base_list}
        extra = [s for s in self._list_bucket_inputs() if s.filename not in seen]
        return [*base_list, *extra]

    # -- bucket helpers --------------------------------------------------------

    def _load_bucket_input(self, name: str) -> ExperimentFrame:
        try:
            df = read_input_csv(name)
        except Exception as exc:  # storage 404 / download error
            raise ExperimentNotFoundError(
                f"Experiment {name!r} not found via the base reader or in "
                "bloommcp_input/."
            ) from exc
        return _frame_from_df(df)

    def _list_bucket_inputs(self) -> list[ExperimentSummary]:
        try:
            keys = list_prefix(INPUT_PREFIX)
        except Exception:
            return []
        summaries: list[ExperimentSummary] = []
        for key in keys:
            name = key.rsplit("/", 1)[-1]  # basename, whether or not prefixed
            if not name.endswith(".csv"):
                continue
            try:
                summaries.append(_summary_from_df(read_input_csv(name), name))
            except Exception:
                continue
        return summaries


def _frame_from_df(df) -> ExperimentFrame:
    cfg = detect_columns(df)
    return ExperimentFrame(
        df=df,
        trait_cols=cfg["trait_cols"],
        metadata_cols=cfg["metadata_cols"],
        genotype_col=cfg["genotype_col"],
        replicate_col=cfg["replicate_col"],
        sample_id_col=cfg["sample_id_col"],
        source="bucket_input",
    )


def _summary_from_df(df, name: str) -> ExperimentSummary:
    cfg = detect_columns(df)
    stem = name[:-4] if name.endswith(".csv") else name
    has_name = "experiment_name" in df.columns and len(df) > 0
    return ExperimentSummary(
        filename=name,
        stem=stem,
        rows=len(df),
        total_columns=len(df.columns),
        trait_columns=len(cfg["trait_cols"]),
        experiment_name=str(df["experiment_name"].iloc[0]) if has_name else stem,
        genotype_col=cfg["genotype_col"],
        sample_id_col=cfg["sample_id_col"],
    )

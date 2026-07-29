"""Supabase-backed :class:`ExperimentReader` — wraps the deployed read path.

Cleaned-output tiers (versioned ``qc_<stem>`` manifests, then the legacy
un-versioned cleaned CSV) are unchanged, resolved via
``experiment_utils._resolve_versioned_cleaned`` directly — that helper's own
docstring already documents it as backend-agnostic (``o_dir`` is accepted for
signature compatibility but ignored), so reusing it here (rather than the
higher-level ``load_experiment_data``, whose raw-tier branch this class no
longer wants) does not duplicate its manifest-resolution logic.

The **raw** tier queries Bloom's Postgres tables directly (Tier 2 of
bloommcp's data-access roadmap, `bloom#551`) instead of reading a local
``BLOOM_TRAITS_DIR`` CSV: ``name`` is parsed as ``str(experiment_id)`` — DB-only,
no local-disk fallback (bloom#551's Decision D1). Every raw read resolves and
pins exactly one concrete database source before fetching (Decision D2), so no
returned frame ever mixes rows from more than one source, and records that
source's identity for provenance in place of the file-hash content-address a
DB-backed read no longer has (Decision D3). A long→wide pivot that would
collide two plants under one ``sample_id`` is a structured error, not a
silent merge (Decision D5).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from bloom_mcp import experiment_utils as _eu
from bloom_mcp import supabase_client as _sc
from bloom_mcp.experiment_utils import detect_columns

from .ports import (
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReadError,
    ExperimentSummary,
    SourceInfo,
)

logger = logging.getLogger(__name__)

_GENOTYPE_COL = "genotype"
_SAMPLE_ID_COL = "sample_id"
_METADATA_COLS = ["wave", "plant_age_days", "date_scanned", "plant_id"]
# genotype + sample_id + the four metadata columns above, before any trait
# columns are added — used to report `total_columns` in a list_experiments()
# summary without re-deriving the wide frame's exact shape.
_FIXED_COLUMN_COUNT = 2 + len(_METADATA_COLS)


class SupabaseReader:
    """Reads experiment inputs via versioned-cleaned Storage + a DB-direct raw tier."""

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> ExperimentFrame:
        if source_id is not None and run_id is not None:
            raise AmbiguousSourceSelectionError(
                "source_id and run_id are mutually exclusive; supply at most one."
            )

        stem = Path(name).stem
        if version != "raw":
            cleaned_path, source_label, error = _eu._resolve_versioned_cleaned(
                _eu.OUTPUT_DIR, stem, version
            )
            if error:
                # `error` is the deployed loader's raw string, which can name a
                # storage path — do NOT surface it. Raise a caller-safe message.
                raise ExperimentNotFoundError(
                    f"Version {version!r} not found for experiment {name!r}."
                )
            if cleaned_path is not None:
                df = pd.read_csv(cleaned_path)
                config = detect_columns(df)
                return ExperimentFrame(
                    df=df,
                    trait_cols=config["trait_cols"],
                    metadata_cols=config["metadata_cols"],
                    genotype_col=config["genotype_col"],
                    replicate_col=config["replicate_col"],
                    sample_id_col=config["sample_id_col"],
                    source=source_label,
                )

        if require_clean:
            raise CleanedVersionRequiredError(
                f"No cleaned dataset found for {name!r}; run the QC workflow first."
            )

        experiment_id = _parse_experiment_id(name)
        source = self.resolve_source(name, source_id=source_id, run_id=run_id)

        rpc_params = {
            "experiment_id_": experiment_id,
            "source_id_": None,
            "run_id_": None,
        }
        if source is not None:
            rpc_params["source_id_"] = source.source_id
        rows = _sc.call_rpc("get_experiment_traits", rpc_params)

        if not rows and not self._experiment_exists(experiment_id):
            raise ExperimentNotFoundError(f"Experiment {name!r} could not be resolved.")

        return _pivot_wide(rows, name)

    def list_sources(self, name: str) -> list[SourceInfo]:
        experiment_id = _parse_experiment_id(name)
        rows = _sc.call_rpc(
            "list_experiment_trait_sources", {"experiment_id_": experiment_id}
        )
        return [
            SourceInfo(
                source_id=row["source_id"],
                source_name=row.get("source_name"),
                pipeline_run_id=row.get("pipeline_run_id"),
            )
            for row in rows
        ]

    def resolve_source(
        self,
        name: str,
        *,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> Optional[SourceInfo]:
        if source_id is not None and run_id is not None:
            raise AmbiguousSourceSelectionError(
                "source_id and run_id are mutually exclusive; supply at most one."
            )

        sources = self.list_sources(name)
        if not sources:
            if source_id is not None or run_id is not None:
                raise ExperimentNotFoundError(
                    f"No source/run matching the given pin for experiment {name!r}."
                )
            # Legacy-only data (source_id IS NULL for every trait row) — a
            # normal state, not an error; load_experiment falls back to an
            # unpinned fetch and records no source identity.
            return None

        if source_id is not None:
            match = next((s for s in sources if s.source_id == source_id), None)
            if match is None:
                raise ExperimentNotFoundError(
                    f"Source {source_id} not found for experiment {name!r}."
                )
            return match

        if run_id is not None:
            match = next((s for s in sources if s.pipeline_run_id == run_id), None)
            if match is None:
                raise ExperimentNotFoundError(
                    f"Run {run_id!r} not found for experiment {name!r}."
                )
            return match

        # Unpinned: latest is the experiment-wide max source_id — resolved once
        # here and pinned explicitly by the caller, rather than ever calling
        # get_experiment_traits unpinned and trusting its per-scan `is_latest`
        # disjunction to agree across every scan (see module docstring).
        return max(sources, key=lambda s: s.source_id)

    def _experiment_exists(self, experiment_id: int) -> bool:
        client = _sc.get_postgrest_client()
        response = (
            client.table("cyl_experiments")
            .select("id")
            .eq("id", experiment_id)
            .execute()
        )
        return bool(response.data)

    def list_experiments(self) -> list[ExperimentSummary]:
        client = _sc.get_postgrest_client()
        response = client.table("cyl_experiments").select("id,name").execute()

        summaries: list[ExperimentSummary] = []
        for row in response.data:
            experiment_id = row["id"]
            filename = str(experiment_id)
            try:
                # A per-experiment bulk fetch — the same call load_experiment
                # itself makes — is the only way to get an accurate trait
                # count; there is no per-experiment distinct-trait-name view
                # (see design.md's Decision D4). Reused here to also derive
                # `rows` (distinct plant count) from the same round trip
                # rather than a second, separately-guessed join query.
                rows = _sc.call_rpc(
                    "get_experiment_traits",
                    {
                        "experiment_id_": experiment_id,
                        "source_id_": None,
                        "run_id_": None,
                    },
                )
            except Exception:
                logger.warning(
                    "list_experiments: failed to fetch traits for experiment "
                    "%s; excluding it from the listing rather than failing "
                    "the whole call.",
                    experiment_id,
                    exc_info=True,
                )
                continue

            plant_ids = {r["plant_id"] for r in rows}
            trait_names = {r["trait_name"] for r in rows}
            summaries.append(
                ExperimentSummary(
                    filename=filename,
                    stem=filename,
                    rows=len(plant_ids),
                    total_columns=len(trait_names) + _FIXED_COLUMN_COUNT,
                    trait_columns=len(trait_names),
                    experiment_name=str(row.get("name") or filename),
                    genotype_col=_GENOTYPE_COL,
                    sample_id_col=_SAMPLE_ID_COL,
                )
            )
        return summaries


def _parse_experiment_id(name: str) -> int:
    try:
        return int(name)
    except (TypeError, ValueError):
        raise ExperimentNotFoundError(
            f"Experiment {name!r} could not be resolved; expected a numeric "
            "experiment id."
        ) from None


def _pivot_wide(rows: list[dict], name: str) -> ExperimentFrame:
    """Pivot `get_experiment_traits`'s long-format rows into a wide frame."""
    if not rows:
        return ExperimentFrame(
            df=pd.DataFrame(),
            trait_cols=[],
            metadata_cols=[],
            genotype_col=None,
            replicate_col=None,
            sample_id_col=None,
            source="raw",
        )

    long_df = pd.DataFrame(rows)

    scans_per_plant = long_df.groupby("plant_id")["scan_id"].nunique()
    ambiguous = scans_per_plant[scans_per_plant > 1]
    if not ambiguous.empty:
        raise ExperimentReadError(
            f"plant {ambiguous.index[0]!r} in experiment {name!r} has "
            f"{int(ambiguous.iloc[0])} distinct scans in the resolved source; "
            "multi-scan pivoting is not supported."
        )

    meta = (
        long_df.drop_duplicates("plant_id")
        .set_index("plant_id")[
            [
                "accession_name",
                "plant_qr_code",
                "wave_number",
                "plant_age_days",
                "date_scanned",
            ]
        ]
        .rename(
            columns={
                "accession_name": _GENOTYPE_COL,
                "plant_qr_code": _SAMPLE_ID_COL,
                "wave_number": "wave",
            }
        )
    )
    trait_wide = long_df.pivot_table(
        index="plant_id", columns="trait_name", values="trait_value", aggfunc="first"
    )
    trait_cols = sorted(trait_wide.columns.tolist())
    trait_wide = trait_wide[trait_cols]

    df = meta.join(trait_wide).reset_index()

    dupes = df[_SAMPLE_ID_COL][df[_SAMPLE_ID_COL].duplicated(keep=False)]
    if not dupes.empty:
        raise AmbiguousSampleIdentityError(
            f"sample_id {dupes.iloc[0]!r} is shared by more than one plant in "
            f"experiment {name!r} (qr_code is only unique within a wave)"
        )

    return ExperimentFrame(
        df=df,
        trait_cols=trait_cols,
        metadata_cols=list(_METADATA_COLS),
        genotype_col=_GENOTYPE_COL,
        replicate_col=None,
        sample_id_col=_SAMPLE_ID_COL,
        source="raw",
    )


# Re-exported so consumers that need ad-hoc role detection use the same source
# of truth the adapter declares roles from.
__all__ = ["SupabaseReader", "detect_columns"]

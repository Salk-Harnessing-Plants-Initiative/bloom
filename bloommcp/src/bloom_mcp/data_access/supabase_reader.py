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
    AmbiguousRunIdError,
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    CleanedVersionRequiredError,
    DuplicateTraitReadingError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReadError,
    ExperimentSummary,
    MultipleScansPerPlantError,
    SourceInfo,
    SourcePinNotFoundError,
)

logger = logging.getLogger(__name__)

_GENOTYPE_COL = "genotype"
_SAMPLE_ID_COL = "sample_id"
_METADATA_COLS = ["wave", "plant_age_days", "date_scanned", "plant_id", "scan_id"]
# genotype + sample_id + the four metadata columns above, before any trait
# columns are added — used to report `total_columns` in a list_experiments()
# summary without re-deriving the wide frame's exact shape.
_FIXED_COLUMN_COUNT = 2 + len(_METADATA_COLS)

# RPC/table names, named once so a rename or typo is a single-line fix.
_RPC_GET_EXPERIMENT_TRAITS = "get_experiment_traits"
_RPC_LIST_EXPERIMENT_TRAIT_SOURCES = "list_experiment_trait_sources"
_TABLE_CYL_EXPERIMENTS = "cyl_experiments"


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
                if source_id is not None or run_id is not None:
                    # A source/run pin only applies to the DB-backed raw tier.
                    # Silently returning the cleaned frame here would let a
                    # caller believe their pin was honored when it never was
                    # -- pass version="raw" to force the raw tier instead.
                    raise AmbiguousSourceSelectionError(
                        f"source_id/run_id pin cannot be honored: a cleaned "
                        f"version ({source_label}) resolved for {name!r} "
                        "before any database read; pass version='raw' to "
                        "force the raw tier instead."
                    )
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
            if version == "raw":
                # A genuinely contradictory request, not "no cleaned version
                # exists" — version="raw" above skipped the cleaned-tier lookup
                # entirely, so telling the caller to "run the QC workflow" would
                # misdiagnose the actual problem (the two arguments disagree).
                raise CleanedVersionRequiredError(
                    f"version='raw' and require_clean=True are contradictory "
                    f"for {name!r}: version='raw' explicitly forces the raw "
                    "tier, which is never a cleaned version."
                )
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
        rows = _safe_rpc(_RPC_GET_EXPERIMENT_TRAITS, rpc_params, name=name)

        if not rows and not self._experiment_exists(experiment_id):
            raise ExperimentNotFoundError(f"Experiment {name!r} could not be resolved.")

        return _pivot_wide(rows, name, source)

    def list_sources(self, name: str) -> list[SourceInfo]:
        experiment_id = _parse_experiment_id(name)
        rows = _safe_rpc(
            _RPC_LIST_EXPERIMENT_TRAIT_SOURCES,
            {"experiment_id_": experiment_id},
            name=name,
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
                # Distinguish "this experiment doesn't exist at all" from "it
                # exists but has no tracked sources to pin against" -- a
                # caller acting on the error (e.g. retry vs. give up) needs to
                # tell these apart programmatically, not just read the message.
                if not self._experiment_exists(_parse_experiment_id(name)):
                    raise ExperimentNotFoundError(
                        f"Experiment {name!r} could not be resolved."
                    )
                raise SourcePinNotFoundError(
                    f"No source/run matching the given pin for experiment {name!r}."
                )
            # Legacy-only data (source_id IS NULL for every trait row) — a
            # normal state, not an error; load_experiment falls back to an
            # unpinned fetch and records no source identity.
            return None

        if source_id is not None:
            match = next((s for s in sources if s.source_id == source_id), None)
            if match is None:
                raise SourcePinNotFoundError(
                    f"Source {source_id} not found for experiment {name!r}."
                )
            return match

        if run_id is not None:
            matches = [s for s in sources if s.pipeline_run_id == run_id]
            if not matches:
                raise SourcePinNotFoundError(
                    f"Run {run_id!r} not found for experiment {name!r}."
                )
            if len(matches) > 1:
                # pipeline_run_id carries no DB uniqueness constraint (only
                # idempotency_key is enforced) -- a run_id pin is not guaranteed
                # to resolve to exactly one source. Raise rather than silently
                # picking one of the matches, which would undermine "one source
                # per frame is structural, not asserted" (see module docstring).
                raise AmbiguousRunIdError(
                    f"run_id {run_id!r} matches {len(matches)} distinct sources "
                    f"for experiment {name!r}; pipeline_run_id does not "
                    "uniquely identify one."
                )
            return matches[0]

        # Unpinned: latest is the experiment-wide max source_id — resolved once
        # here and pinned explicitly by the caller, rather than ever calling
        # get_experiment_traits unpinned and trusting its per-scan `is_latest`
        # disjunction to agree across every scan (see module docstring).
        return max(sources, key=lambda s: s.source_id)

    def _experiment_exists(self, experiment_id: int) -> bool:
        try:
            client = _sc.get_postgrest_client()
            response = (
                client.table(_TABLE_CYL_EXPERIMENTS)
                .select("id")
                .eq("id", experiment_id)
                .execute()
            )
        except Exception as exc:
            raise ExperimentReadError(
                f"Could not verify experiment {experiment_id} exists: the "
                "database read failed."
            ) from exc
        return bool(response.data)

    def list_experiments(self) -> list[ExperimentSummary]:
        try:
            client = _sc.get_postgrest_client()
            response = client.table(_TABLE_CYL_EXPERIMENTS).select("id,name").execute()
        except Exception as exc:
            raise ExperimentReadError(
                "Could not list experiments: the database read failed."
            ) from exc

        summaries: list[ExperimentSummary] = []
        for row in response.data:
            # The whole per-row body is one try/except -- a malformed row (a
            # missing "id"/"plant_id"/"trait_name" key) must exclude only this
            # experiment from the listing, not crash the entire call, same as
            # an RPC failure below.
            try:
                experiment_id = row["id"]
                filename = str(experiment_id)
                # A per-experiment bulk fetch — the same call load_experiment
                # itself makes — is the only way to get an accurate trait
                # count; there is no per-experiment distinct-trait-name view
                # (see design.md's Decision D4). Reused here to also derive
                # `rows` (distinct plant count) from the same round trip
                # rather than a second, separately-guessed join query.
                rows = _sc.call_rpc(
                    _RPC_GET_EXPERIMENT_TRAITS,
                    {
                        "experiment_id_": experiment_id,
                        "source_id_": None,
                        "run_id_": None,
                    },
                )
                plant_ids = {r["plant_id"] for r in rows}
                trait_names = {r["trait_name"] for r in rows}
                summary = ExperimentSummary(
                    filename=filename,
                    stem=filename,
                    rows=len(plant_ids),
                    total_columns=len(trait_names) + _FIXED_COLUMN_COUNT,
                    trait_columns=len(trait_names),
                    experiment_name=str(row.get("name") or filename),
                    genotype_col=_GENOTYPE_COL,
                    sample_id_col=_SAMPLE_ID_COL,
                )
            except Exception:
                logger.warning(
                    "list_experiments: failed to process experiment row %r; "
                    "excluding it from the listing rather than failing "
                    "the whole call.",
                    row,
                    exc_info=True,
                )
                continue
            summaries.append(summary)
        return summaries


def _safe_rpc(function_name: str, params: dict, *, name: str) -> list[dict]:
    """Call ``supabase_client.call_rpc``, translating any failure into a
    caller-safe :class:`ExperimentReadError`.

    ``call_rpc`` itself documents that it re-raises whatever the Supabase
    client raises (a declared SQL ``RAISE EXCEPTION``, a network failure, an
    RLS denial) and leaves surfacing that as a structured error to the caller.
    A raw exception may embed backend detail (a SQL message, a connection
    string) that must never reach an agent-facing message.
    """
    try:
        return _sc.call_rpc(function_name, params)
    except Exception as exc:
        raise ExperimentReadError(
            f"Could not read {function_name} data for experiment {name!r}: "
            "the database read failed."
        ) from exc


def _parse_experiment_id(name: str) -> int:
    try:
        return int(name)
    except (TypeError, ValueError):
        raise ExperimentNotFoundError(
            f"Experiment {name!r} could not be resolved; expected a numeric "
            "experiment id."
        ) from None


def _pivot_wide(
    rows: list[dict], name: str, source: Optional[SourceInfo]
) -> ExperimentFrame:
    """Pivot `get_experiment_traits`'s long-format rows into a wide frame.

    Keys one output row per ``plant_id`` within the single resolved source
    `rows` was fetched for (cylinder data's "the replicate unit" semantics —
    see the module docstring). More than one ``scan_id`` for the same plant in
    that source is an explicit, structured error (`MultipleScansPerPlantError`)
    rather than a silent `(scan_id, plant_id)`-keyed pivot: supporting a real
    multi-scan layout is deferred future work, not assumed away.

    Three integrity checks run in a fixed order (multi-scan, then duplicate
    trait readings, then cross-wave sample_id collisions) and each raises
    immediately on its first violation. If more than one kind of violation is
    present in the same fetch, only the first-encountered one is ever
    reported — an intentional ordering choice (the read still fails loudly
    either way), not a guarantee about which violation a caller sees first
    when several co-occur.
    """
    if not rows:
        return ExperimentFrame(
            df=pd.DataFrame(),
            trait_cols=[],
            metadata_cols=[],
            genotype_col=None,
            replicate_col=None,
            sample_id_col=None,
            source="raw",
            resolved_source=source,
        )

    long_df = pd.DataFrame(rows)

    scans_per_plant = long_df.groupby("plant_id")["scan_id"].nunique()
    ambiguous = scans_per_plant[scans_per_plant > 1]
    if not ambiguous.empty:
        # Name the plant by its qr_code (sample_id), not the internal plant_id
        # -- consistent with AmbiguousSampleIdentityError's policy of never
        # leaking an internal DB id into an agent-facing message.
        bad_plant_id = ambiguous.index[0]
        bad_qr_code = long_df.loc[
            long_df["plant_id"] == bad_plant_id, "plant_qr_code"
        ].iloc[0]
        raise MultipleScansPerPlantError(
            f"plant {bad_qr_code!r} in experiment {name!r} has "
            f"{int(ambiguous.iloc[0])} distinct scans in the resolved source; "
            "multi-scan pivoting is not supported."
        )

    # A duplicate (plant_id, trait_name) pair within the single resolved source
    # has no DB constraint preventing it (cyl_scan_traits carries none). Refuse
    # to silently keep an arbitrary one via pivot_table's aggfunc -- the same
    # "fail loudly, don't guess" treatment this function already gives
    # multi-scan and cross-wave sample_id collisions two lines above/below.
    dup_trait_mask = long_df.duplicated(subset=["plant_id", "trait_name"], keep=False)
    if dup_trait_mask.any():
        dup_row = long_df.loc[dup_trait_mask].iloc[0]
        raise DuplicateTraitReadingError(
            f"trait {dup_row['trait_name']!r} has more than one value for "
            f"plant {dup_row['plant_qr_code']!r} in experiment {name!r} "
            "within the resolved source; the raw tier does not silently "
            "pick one."
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
                "scan_id",
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
        index="plant_id",
        columns="trait_name",
        values="trait_value",
        aggfunc="first",
        # pivot_table's default dropna=True silently drops a trait column
        # whose value is NaN/None for every plant in this fetch (e.g. a trait
        # not yet measured for anyone in the resolved source) -- the caller
        # never asked for that exclusion, so it must survive as an all-NaN
        # column instead of vanishing from trait_cols entirely.
        dropna=False,
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
        resolved_source=source,
    )


# Re-exported so consumers that need ad-hoc role detection use the same source
# of truth the adapter declares roles from.
__all__ = ["SupabaseReader", "detect_columns"]

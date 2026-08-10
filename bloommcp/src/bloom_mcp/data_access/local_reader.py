"""Supabase-free :class:`ExperimentReader` — the opt-in fully-local input adapter.

The input-side twin of the object-storage ``local`` backend: reads raw experiment
CSVs from a configurable local directory and resolves cleaned/versioned outputs
from the local output store (the ``local`` storage backend), returning the same
:class:`ExperimentFrame` contract as :class:`SupabaseReader`. It imports no
``supabase_client`` and makes no PostgREST/table or network call — a static guard
(``tests/data_access/test_local_reader.py``) enforces the absent import.

Selection is coupled to the object-storage backend: a ``LocalReader`` is only valid
when ``BLOOM_STORAGE_BACKEND=local``, so its cleaned tier (which resolves through the
active backend) can never read from Supabase while its raw tier reads local files —
a split lineage that would silently feed the wrong data into an analysis.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from bloom_mcp.experiment_utils import (
    list_experiments as _list_experiments,
    load_experiment_data as _load_experiment_data,
    resolve_experiment_local_root,
)
from bloom_mcp.storage_backend import is_local_backend, selected_backend_name

from .ports import (
    CleanedVersionRequiredError,
    ExperimentFrame,
    ExperimentNotFoundError,
    ExperimentReadError,
    ExperimentSummary,
    SourcePinningUnsupportedError,
)


class LocalReader:
    """Reads experiment inputs from the local filesystem, with no Supabase."""

    def __init__(self) -> None:
        # Reader/store coupling: refuse to run local-input reads while the object
        # store is Supabase (or unset). Otherwise the raw tier would read local
        # files while the cleaned tier resolved through the Supabase backend — a
        # split lineage. Kept structural (raised here, not merely documented).
        if not is_local_backend():
            raise RuntimeError(
                "LocalReader requires BLOOM_STORAGE_BACKEND=local so input and "
                "output stay on the same (local) backend; got "
                f"BLOOM_STORAGE_BACKEND={selected_backend_name()!r}."
            )
        self._root = resolve_experiment_local_root()
        # Resolve once: the root never changes after __init__, so _safe_name avoids
        # a symlink-canonicalization syscall on every load / raw_source_path call.
        self._resolved_root = self._root.resolve()

    # --- ExperimentReader --------------------------------------------------

    def load_experiment(
        self,
        name: str,
        *,
        version: str = "latest",
        require_clean: bool = False,
        source_id: Optional[int] = None,
        run_id: Optional[str] = None,
    ) -> ExperimentFrame:
        if source_id is not None or run_id is not None:
            # LocalReader has no source-versioned substrate — reject outright
            # rather than silently ignoring the pin (#626).
            raise SourcePinningUnsupportedError(
                "LocalReader has no source concept; source_id/run_id pinning "
                "is not supported for this backend."
            )
        safe = self._safe_name(name)
        # Same resolution + same pandas config as the deployed raw path (via the
        # shared loader), rooted at the local input dir, with the un-versioned
        # legacy cleaned tier disabled: it carries no manifest/hash lineage, so a
        # certified-clean consumer must not be satisfied by a possibly-stale
        # legacy CSV that may not correspond to the current input.
        df, _trait_cols, config, source_label = _load_experiment_data(
            safe,
            traits_dir=self._root,
            require_clean=require_clean,
            version=version,
            allow_legacy_cleaned=False,
        )
        if df is None:
            # `source_label` is the raw loader error (may name a path); do NOT
            # surface it — raise a caller-safe message instead.
            if require_clean:
                raise CleanedVersionRequiredError(
                    f"No cleaned dataset found for {name!r}; run the QC workflow first."
                )
            raise ExperimentNotFoundError(
                f"Experiment {name!r} (version={version!r}) could not be resolved."
            )

        # No DeprecationWarning here: under this adapter the local input path is a
        # supported, first-class path (SupabaseReader warns on its raw tier).
        return ExperimentFrame(
            df=df,
            trait_cols=config["trait_cols"],
            metadata_cols=config["metadata_cols"],
            genotype_col=config["genotype_col"],
            replicate_col=config["replicate_col"],
            sample_id_col=config["sample_id_col"],
            source=source_label,
        )

    def list_experiments(self) -> list[ExperimentSummary]:
        # The scan dicts' keys are exactly ExperimentSummary's fields, so splat them:
        # one place to update if a field is added, and identical to SupabaseReader.
        return [
            ExperimentSummary(**exp) for exp in _list_experiments(traits_dir=self._root)
        ]

    # --- optional provenance capability (used by tools._ports.start_run) ---

    def raw_source_path(self, name: str) -> Optional[Path]:
        """The on-disk raw input path for ``name``, for provenance hashing.

        Returns the local raw CSV path when it exists so a run's ``source_csv``
        content-addresses the real input (non-empty ``input_sha256``), or ``None``
        when there is no such file. A path-less adapter simply omits this method.
        """
        try:
            safe = self._safe_name(name)
        except ExperimentReadError:
            return None
        candidate = self._root / safe
        return candidate if candidate.is_file() else None

    # --- containment guard -------------------------------------------------

    def _safe_name(self, name: str) -> str:
        """Reject any ``name`` that is not a bare basename under the input root.

        bloommcp is LLM-driven, so an agent can be steered to request an arbitrary
        ``name``. Names are bare filenames (e.g. ``exp.csv``); reject path
        components, absolute paths, ``.``/``..``, and names that contain null bytes
        (which raise ``ValueError`` from ``Path()`` in CPython 3.11+). Then verify
        the resolved real path stays within the resolved real root (covers a symlink
        escape). The error leaks no host path.

        NOTE: ``_resolved_root`` is captured once at ``__init__``. If the root is
        replaced by a symlink after construction the canonical path is stale — an
        accepted risk for a dev/offline backend on a bind-mount.
        """
        try:
            bare = Path(name).name
        except ValueError:
            raise ExperimentReadError("experiment name must be a bare filename")
        if not name or name in (".", "..") or name != bare:
            raise ExperimentReadError("experiment name must be a bare filename")
        root = self._resolved_root
        target = (self._root / name).resolve()
        if target != root and root not in target.parents:
            raise ExperimentReadError("experiment name escapes the local input root")
        return name


__all__ = ["LocalReader"]

"""Live persistence smoke — drive granular tools through the REAL ports against the dev stack.

This runs on the **host** against the running dev stack (Supabase + storage-api +
MinIO), so it must override what ``.env.dev`` configures for in-container processes:

  * ``SUPABASE_URL`` — ``.env.dev`` points this at the in-container gateway
    ``http://kong:8000``; the host reaches Kong at ``http://localhost:$KONG_HTTP_PORT``.
    The ``make bloommcp-smoke`` target exports the host value before launching this
    script (it derives the port from ``.env.dev``); we fall back to localhost:8000 only
    for a bare ``python tests/smoke/live_persistence_smoke.py`` invocation.
  * ``BLOOM_TRAITS_DIR`` / ``BLOOM_OUTPUT_DIR`` / ``BLOOM_PLOTS_DIR`` — ``.env.dev`` points
    these at in-container ``/app/data/...`` paths; we override them with host temp dirs.
    ``BLOOM_TRAITS_DIR`` itself must still exist for boot validation, but its *contents*
    are no longer read for the raw tier — see ``BLOOM_SMOKE_EXPERIMENT_ID`` below
    (bloom#551: ``SupabaseReader``'s raw tier is DB-only, not a local-CSV read anymore).

``bloom_mcp.experiment_utils`` captures ``TRAITS_DIR`` / ``OUTPUT_DIR`` / ``PLOTS_DIR``
from the environment **at import time**, so the env must be set *before* ``import
bloom_mcp`` (and we hard-set the module globals afterwards as a belt-and-suspenders).

Before driving any tool, ``import bloom_mcp`` (incl. the ``_ports`` composition root) is
checked clean with no Supabase env — the Tier-0 lazy-validation contract — in a scrubbed
subprocess first.

A **Tier-3 ``qc_clean``** leg (#338) drives the granular cleanup tool through the real
ports against a raw experiment already resolvable from Postgres:

  * Set ``BLOOM_SMOKE_EXPERIMENT_ID`` to a numeric experiment id that already has trait
    rows in whatever Postgres this smoke run points at — ``SupabaseReader``'s raw tier is
    DB-only (bloom#551), so there is no local-CSV upload path for this script to seed the
    input from anymore. Seeding that experiment into the dev stack's Postgres is not
    automated by this script (no tracking issue filed yet for a smoke DB seeder);
  * ``qc_clean(experiment=BLOOM_SMOKE_EXPERIMENT_ID, max_nans_per_trait=0.1)`` commits a
    versioned ``qc`` run whose committed outputs include ``_cleaned.csv`` and
    ``cleanup_log.json``;
  * that run's manifest is schema v5 and every recorded ``output_sha256`` matches the bytes
    actually stored for **both** artifacts;
  * a fresh ``SupabaseReader().load_experiment(BLOOM_SMOKE_EXPERIMENT_ID, require_clean=True)``
    then resolves the committed **cleaned** version (source ``v<N>_cleaned``, not ``raw``) and
    that frame has zero NaN cells in its trait columns — the qc_clean → pca_analysis contract.

A ``remove_outliers`` leg (#378) trims the cleaned version through the same real ports. This
is also where the smoke's generic v5-provenance + version-advance guarantee (originally
proven on a now-retired ``run_clustering_workflow`` leg — devendor-bloommcp-analysis C11.8
repointed it here, since ``remove_outliers`` is the surviving seed-bearing consumer) lives:

  * ``remove_outliers(experiment="turface_raw.csv", method="isolation_forest", seed=42)``
    commits a versioned ``qc`` run (same class — its trimmed ``_cleaned.csv`` becomes the
    newest cleaned version) whose outputs include ``_cleaned.csv`` and ``outlier_report.json``,
    with a schema-v5 manifest recording the resolved ``seed``, ``tool == "remove_outliers"``
    (the composition anchor), and matching ``output_sha256`` for both artifacts.
    **method=isolation_forest, not mahalanobis (#419):** this leg exercises *persistence
    mechanics* (versioning, provenance, require_clean composition), none of which are
    mahalanobis-specific, and the #419 fit-trustworthiness gate now rejects a mahalanobis
    trim outright when the chi-squared fit is untrustworthy — both local reference fixtures
    (turface_19, cylinder) are untrustworthy under mahalanobis defaults at their canonical
    cleaning threshold (see ``tests/tools/test_remove_outliers_tool.py``), and this smoke's
    own cleaning threshold (``max_nans_per_trait=0.1``, see below) was never independently
    confirmed *not* to be, so isolation_forest sidesteps that uncertainty entirely rather than
    risking this leg (and the ~7 checks gated on it) going red on a live-data fit this script
    cannot control for;
  * the same manifest is also asserted against the generic v5-provenance contract: schema v5,
    non-null real ``seed`` (== 42), ``agent`` == ``bloom_agent``, populated ``environment``, and
    matching ``output_sha256`` / ``output_keys`` maps;
  * a fresh ``require_clean=True`` read then resolves the **trimmed** version (``v<N>_cleaned``)
    with *no more* rows than the pre-trim clean and zero NaN trait cells — proving
    qc_clean → remove_outliers → require_clean end-to-end. (The row bound is ``<=``, not a
    strict ``<``: the smoke cleans at its own threshold, so isolation_forest@seed42/
    contamination=0.1 may flag very few or many outliers depending on the frame's actual size —
    not a regression either way; the trim's persistence as the resolvable latest is anchored on
    the ``tool`` provenance check above, not the row delta.)
  * a second ``remove_outliers`` commit must advance ``latest`` from ``v<N>`` to ``v<N+1>``
    without clobbering the first — ``get_run(first_ref)`` still resolves it.

A third, granular **``clustering``** leg (#309) *consumes* that cleaned version through the
same real ports:

  * ``clustering(experiment="turface_raw.csv", method="kmeans", seed=42)`` resolves the latest
    cleaned version via ``require_clean=True`` (the trim if the leg above ran, else the qc_clean
    clean) and commits a versioned ``clustering`` run whose outputs are ``labels.csv`` +
    ``cluster_result.json``, with a schema-v5 manifest recording the resolved ``seed``,
    ``tool == "clustering"``, and matching ``output_sha256`` for both artifacts — the
    qc_clean → … → clustering(require_clean=True) composition, in parallel with pca_analysis.

A fourth, **hierarchical clustering** leg (#422) validates the deterministic arm:

  * ``clustering(experiment="turface_raw.csv", method="hierarchical")`` resolves the latest
    cleaned version via ``require_clean=True`` and commits a versioned ``clustering`` run whose
    outputs are ``labels.csv`` + ``cluster_result.json``, with a schema-v5 manifest recording
    ``seed=None`` (hierarchical is deterministic — no RNG), ``tool == "clustering"``, and
    matching ``output_sha256`` for both artifacts.

A fifth, **``descriptive_stats``** leg (#488) *consumes* the same latest cleaned version:

  * ``descriptive_stats(experiment="turface_raw.csv")`` resolves the latest cleaned version via
    ``require_clean=True`` and commits a versioned ``stats`` run (a new tool class — its output
    does not compose as another tool's input) whose outputs include ``stats.csv``, with a
    schema-v5 manifest recording ``seed=None`` (deterministic — no RNG), ``tool ==
    "descriptive_stats"``, and matching ``output_sha256``. Asserted **structurally** (one row per
    reported trait, ``n_failed == 0``) rather than against the unit golden's exact numeric values
    — the smoke's cleaned input uses the ``qc_clean`` leg's own threshold, which may differ from
    the unit golden's canonical-default clean.

Every failure mode (tool error, hash mismatch, read-after-write timeout, import leak)
routes through the per-check summary and a non-zero exit — never an unlabelled traceback.

Run via ``make bloommcp-smoke`` (preferred) or, with the dev stack up + migrated and
``BLOOM_AGENT_KEY`` exported, ``cd bloommcp && uv run python tests/smoke/live_persistence_smoke.py``.

See also: DEV_SETUP.md (§API Gateway, host vs container URLs) and the ``bloommcp-smoke``
target in the repo-root Makefile.
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, NamedTuple, Optional

# --- constants ----------------------------------------------------------------
# EXPECTED_SEED backs provenance_checks(), the generic v5-provenance assertion
# (schema/seed/agent/environment/output-keys) originally proven on the retired
# legacy run_clustering_workflow leg; C11.8 (devendor-bloommcp-analysis) moved
# that assertion onto the remove_outliers leg, which also resolves a fixed seed
# of 42 (see RO_SEED below) — same value, no numeric change.
EXPECTED_SEED = 42
EXPECTED_AGENT = "bloom_agent"

# --- Tier-3 qc_clean leg constants --------------------------------------------
# A SECOND experiment, cleaned through the granular ``qc_clean`` tool (#338).
# ``SupabaseReader``'s raw tier is DB-only (bloom#551) — this leg needs a REAL
# numeric experiment id that already has trait rows in whatever Postgres this
# smoke run points at, not a local CSV this script can seed itself. Set
# BLOOM_SMOKE_EXPERIMENT_ID to that id before running `make bloommcp-smoke`.
QC_EXPERIMENT = os.environ.get("BLOOM_SMOKE_EXPERIMENT_ID", "")
QC_TOOL_CLASS = "qc"
QC_MAX_NANS_PER_TRAIT = 0.1
CLEANED_CSV_NAME = (
    "_cleaned.csv"  # logical key qc_clean commits (and the reader resolves)
)
CLEANUP_LOG_NAME = "cleanup_log.json"  # logical key for the cleanup audit log

# --- remove_outliers leg constants --------------------------------------------
# The remove_outliers tool (#378) trims outlier samples from the CLEANED version
# qc_clean just committed, persisting under its own dedicated ``outliers`` tool
# class (#420): the reader prefers it over ``qc`` for a later ``require_clean``
# read — the qc_clean -> remove_outliers -> require_clean composition — while
# remove_outliers's own next read (version="latest_qc") stays pinned to the plain
# clean, so a later qc_clean re-run is never hidden from it.
RO_TOOL_CLASS = "outliers"
RO_REPORT_NAME = "outlier_report.json"  # logical key for the outlier report
RO_SEED = 42  # remove_outliers is stochastic — resolves this fixed seed

# --- clustering leg constants (#309) ------------------------------------------
# The granular clustering tool (#309) *consumes* the cleaned version qc_clean (then
# remove_outliers) committed, through the SAME real ports, persisting a versioned
# ``clustering`` run — proving qc_clean -> ... -> clustering(require_clean=True).
# clustering is stochastic (records the seed).
CL_TOOL_CLASS = "clustering"
CL_SEED = 42  # clustering/kmeans is stochastic — resolves this fixed seed
CL_LABELS_NAME = "labels.csv"  # logical key for the per-sample cluster labels
CL_RESULT_NAME = "cluster_result.json"  # logical key for the serialized typed result

# --- descriptive_stats leg constants (#488) -----------------------------------
# The granular descriptive_stats tool (#488) *consumes* the same latest cleaned
# version through the SAME real ports, persisting under its own ``stats`` tool
# class (deliberately not ``qc`` — its output does not compose as another tool's
# input). descriptive_stats is deterministic (records seed=None).
STATS_TOOL_CLASS = "stats"
STATS_CSV_NAME = "stats.csv"  # logical key for the full per-trait table

# Read-after-write can lag the storage-api; bound the wait so a real regression
# still fails fast (5 attempts, 1s apart, ≤5s ceiling) rather than hanging.
RETRY_ATTEMPTS = 5
RETRY_DELAY_S = 1.0


# --- pure, unit-testable helpers ----------------------------------------------
class Check(NamedTuple):
    """One named assertion: ``ok`` decides pass/fail, ``detail`` aids debugging."""

    name: str
    ok: bool
    detail: str = ""


def summarize(checks: list[Check]) -> tuple[str, int]:
    """Render a per-check OK/FAIL summary and an exit code (0 = all passed)."""
    lines: list[str] = []
    failed: list[str] = []
    for c in checks:
        prefix = "  OK   " if c.ok else "  FAIL "
        lines.append(prefix + c.name + (f" — {c.detail}" if c.detail else ""))
        if not c.ok:
            failed.append(c.name)
    if failed:
        lines.append(f"SMOKE FAILED: {failed}")
        return "\n".join(lines), 1
    lines.append(
        "SMOKE PASSED ✅ — the qc_clean cleaned run, remove_outliers trimmed run "
        "(incl. the generic v5-provenance + version-advance guarantee), AND the granular "
        "clustering(kmeans), clustering(hierarchical), and descriptive_stats consumers "
        "all persist full provenance through the real ports; the qc_clean → "
        "remove_outliers → {clustering,descriptive_stats}(require_clean=True) "
        "composition resolves and summarizes the trimmed table."
    )
    return "\n".join(lines), 0


def provenance_checks(
    *,
    schema_version: object,
    seed: object,
    agent: object,
    environment: object,
    output_keys: dict,
    output_sha256: dict,
) -> list[Check]:
    """Assert the v5 provenance fields on the committed run's latest entry."""
    return [
        Check(
            "manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check("seed non-null (B1)", seed is not None, f"seed={seed!r}"),
        Check(f"seed == {EXPECTED_SEED}", seed == EXPECTED_SEED, f"seed={seed!r}"),
        Check(
            f"agent == {EXPECTED_AGENT!r}",
            agent == EXPECTED_AGENT,
            f"agent={agent!r}",
        ),
        Check(
            "environment is an image-digest / uv.lock pointer",
            isinstance(environment, str)
            and ("sha256:" in environment or "uvlock:" in environment),
            f"environment={environment!r}",
        ),
        Check("output_keys present", bool(output_keys), f"output_keys={output_keys!r}"),
        Check(
            "output_sha256 present",
            bool(output_sha256),
            f"output_sha256={output_sha256!r}",
        ),
        Check(
            "output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def hash_checks(
    output_keys: dict,
    output_sha256: dict,
    read_bytes: Callable[[str], bytes],
) -> list[Check]:
    """Download each stored object and assert its SHA-256 matches the manifest."""
    checks: list[Check] = []
    for logical, key in sorted(output_keys.items()):
        name = f"sha256 matches stored bytes [{logical}]"
        try:
            actual = hashlib.sha256(read_bytes(key)).hexdigest()
        except Exception as exc:  # noqa: BLE001 - any download failure is a FAIL
            checks.append(Check(name, False, f"{key}: download failed: {exc}"))
            continue
        recorded = output_sha256.get(logical)
        checks.append(
            Check(
                name, actual == recorded, f"{key}: recorded={recorded} actual={actual}"
            )
        )
    return checks


def qc_persist_checks(
    *,
    schema_version: object,
    output_keys: dict,
    output_sha256: dict,
    expected_outputs: set,
) -> list[Check]:
    """Assert the persisted ``qc_clean`` run: v5 manifest + the cleaned-output catalog.

    The Tier-3 analogue of :func:`provenance_checks`. ``qc_clean`` is deterministic
    (threshold filters, no ``random_state``), so it records ``seed=None`` — there is
    no seed assertion here; what matters is a schema-v5 manifest whose committed
    outputs expose **both** cleaned artifacts under one key-set.
    """
    return [
        Check(
            "qc_clean: manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check(
            "qc_clean: committed outputs include _cleaned.csv + cleanup_log.json",
            expected_outputs <= set(output_keys),
            f"output_keys={sorted(output_keys)}",
        ),
        Check(
            "qc_clean: output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def qc_cleaned_read_checks(source: object, trait_nan_cells: object) -> list[Check]:
    """Assert a ``require_clean`` read resolves the CLEANED artifact with zero NaNs.

    This is the ``qc_clean`` → ``pca_analysis(require_clean=True)`` contract: the
    reader must resolve the committed ``v<N>_cleaned`` version (never the ``raw``
    input), and that cleaned frame must carry no NaN cells in its trait columns.
    """
    return [
        Check(
            "qc_clean: require_clean read resolves the cleaned artifact (not raw)",
            isinstance(source, str) and source != "raw" and source.endswith("_cleaned"),
            f"source={source!r}",
        ),
        Check(
            "qc_clean: cleaned frame has zero NaN trait cells",
            trait_nan_cells == 0,
            f"trait_nan_cells={trait_nan_cells!r}",
        ),
    ]


def ro_persist_checks(
    *,
    schema_version: object,
    seed: object,
    tool: object,
    output_keys: dict,
    output_sha256: dict,
    expected_outputs: set,
) -> list[Check]:
    """Assert the persisted ``remove_outliers`` run: v5 manifest, recorded seed, catalog.

    The #378 analogue of :func:`qc_persist_checks`. Unlike ``qc_clean``, outlier
    detection is *stochastic*, so the run records the resolved integer ``seed`` — asserted
    here — and its committed outputs expose the trimmed ``_cleaned.csv`` + the
    ``outlier_report.json`` under one key-set. The ``tool == "remove_outliers"`` check is
    the *provenance-based composition anchor*: it proves the trim actually persisted and
    became the newest ``outliers`` run (the one a later ``require_clean`` read resolves
    over ``qc``, per #420), independent of how many rows the trim happened to drop.
    """
    return [
        Check(
            "remove_outliers: manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check(
            f"remove_outliers: seed == {RO_SEED}",
            seed == RO_SEED,
            f"seed={seed!r}",
        ),
        Check(
            "remove_outliers: latest outliers run is the trim (tool == 'remove_outliers')",
            tool == "remove_outliers",
            f"tool={tool!r}",
        ),
        Check(
            "remove_outliers: committed outputs include _cleaned.csv + outlier_report.json",
            expected_outputs <= set(output_keys),
            f"output_keys={sorted(output_keys)}",
        ),
        Check(
            "remove_outliers: output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def ro_trimmed_read_checks(
    source: object, trait_nan_cells: object, n_output: object, n_pre_trim: object
) -> list[Check]:
    """Assert a ``require_clean`` read resolves the TRIMMED artifact: no NaN, no growth.

    The payoff of the qc_clean -> remove_outliers chain: after the trim commits, the
    reader must resolve the committed ``v<N>_cleaned`` version (never ``raw``), and that
    trimmed frame must be non-empty, carry no NaN trait cells, and have **no more** rows
    than the pre-trim clean.

    The row-count bound is ``<=`` (not a strict ``<``) on purpose: the smoke cleans at
    its own ``qc_clean`` threshold — a *different* frame than the unit golden's
    canonical-default 158 — so mahalanobis@seed42 may legitimately flag **zero** outliers
    on it. A strict ``<`` would false-fail that no-op trim as a regression. That the trim
    actually persisted and became the resolvable latest is proven separately by the
    provenance anchor in :func:`ro_persist_checks` (``tool == "remove_outliers"``).
    """
    no_growth = (
        isinstance(n_output, int)
        and isinstance(n_pre_trim, int)
        and 0 < n_output <= n_pre_trim
    )
    return [
        Check(
            "remove_outliers: require_clean read resolves the trimmed artifact (not raw)",
            isinstance(source, str) and source != "raw" and source.endswith("_cleaned"),
            f"source={source!r}",
        ),
        Check(
            "remove_outliers: trimmed frame has no more rows than the pre-trim clean",
            no_growth,
            f"n_output={n_output!r} n_pre_trim={n_pre_trim!r}",
        ),
        Check(
            "remove_outliers: trimmed frame has zero NaN trait cells",
            trait_nan_cells == 0,
            f"trait_nan_cells={trait_nan_cells!r}",
        ),
    ]


def clustering_persist_checks(
    *,
    schema_version: object,
    seed: object,
    tool: object,
    source: object,
    output_keys: dict,
    output_sha256: dict,
    expected_outputs: set,
) -> list[Check]:
    """Assert the persisted ``clustering`` run: v5 manifest, recorded seed, catalog, lineage.

    The #309 analogue of :func:`ro_persist_checks`. clustering is *stochastic*, so the run
    records the resolved integer ``seed`` — asserted here. Unlike ``remove_outliers`` it is a
    pure **consumer**: it produces no new cleaned version, so the composition payoff is that it
    resolved the committed cleaned source (``v<N>_cleaned``, not ``raw``) via
    ``require_clean=True`` and persisted a versioned ``clustering`` run whose per-sample labels
    + serialized typed result share one key-set. ``tool == "clustering"`` anchors that the run
    is the granular tool's, not the legacy workflow's.
    """
    return [
        Check(
            "clustering: manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check(
            f"clustering: seed == {CL_SEED}",
            seed == CL_SEED,
            f"seed={seed!r}",
        ),
        Check(
            "clustering: run tool == 'clustering'",
            tool == "clustering",
            f"tool={tool!r}",
        ),
        Check(
            "clustering: consumed a cleaned source (require_clean, not raw)",
            isinstance(source, str) and source != "raw" and source.endswith("_cleaned"),
            f"source={source!r}",
        ),
        Check(
            "clustering: committed outputs include labels.csv + cluster_result.json",
            expected_outputs <= set(output_keys),
            f"output_keys={sorted(output_keys)}",
        ),
        Check(
            "clustering: output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def hierarchical_clustering_persist_checks(
    *,
    schema_version: object,
    seed: object,
    tool: object,
    source: object,
    output_keys: dict,
    output_sha256: dict,
    expected_outputs: set,
) -> list[Check]:
    """Assert the persisted hierarchical ``clustering`` run: v5 manifest, seed=None, catalog.

    Hierarchical clustering is deterministic (no RNG), so provenance records ``seed=None``
    rather than the resolved integer seed. Otherwise mirrors :func:`clustering_persist_checks`.
    """
    return [
        Check(
            "hierarchical clustering: manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check(
            "hierarchical clustering: seed == None (deterministic)",
            seed is None,
            f"seed={seed!r}",
        ),
        Check(
            "hierarchical clustering: run tool == 'clustering'",
            tool == "clustering",
            f"tool={tool!r}",
        ),
        Check(
            "hierarchical clustering: consumed a cleaned source (require_clean, not raw)",
            isinstance(source, str) and source != "raw" and source.endswith("_cleaned"),
            f"source={source!r}",
        ),
        Check(
            "hierarchical clustering: committed outputs include labels.csv + cluster_result.json",
            expected_outputs <= set(output_keys),
            f"output_keys={sorted(output_keys)}",
        ),
        Check(
            "hierarchical clustering: output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def stats_persist_checks(
    *,
    schema_version: object,
    seed: object,
    tool: object,
    source: object,
    output_keys: dict,
    output_sha256: dict,
    expected_outputs: set,
) -> list[Check]:
    """Assert the persisted ``descriptive_stats`` run: v5 manifest, seed=None, catalog, lineage.

    The #488 analogue of :func:`hierarchical_clustering_persist_checks`.
    ``descriptive_stats`` is deterministic (no RNG), so provenance records ``seed=None``.
    A pure **consumer**: it produces no new cleaned version, so the payoff is that it
    resolved the committed cleaned source (``v<N>_cleaned``, not ``raw``) via
    ``require_clean=True`` and persisted a versioned ``stats`` run whose full per-trait
    table is under its own key-set.
    """
    return [
        Check(
            "descriptive_stats: manifest schema == 5",
            schema_version == 5,
            f"schema_version={schema_version!r}",
        ),
        Check(
            "descriptive_stats: seed == None (deterministic)",
            seed is None,
            f"seed={seed!r}",
        ),
        Check(
            "descriptive_stats: run tool == 'descriptive_stats'",
            tool == "descriptive_stats",
            f"tool={tool!r}",
        ),
        Check(
            "descriptive_stats: consumed a cleaned source (require_clean, not raw)",
            isinstance(source, str) and source != "raw" and source.endswith("_cleaned"),
            f"source={source!r}",
        ),
        Check(
            "descriptive_stats: committed outputs include stats.csv",
            expected_outputs <= set(output_keys),
            f"output_keys={sorted(output_keys)}",
        ),
        Check(
            "descriptive_stats: output_keys / output_sha256 share one key-set",
            set(output_keys) == set(output_sha256),
            f"keys={sorted(output_keys)} sha={sorted(output_sha256)}",
        ),
    ]


def stats_result_checks(n_traits_reported: object, n_failed: object) -> list[Check]:
    """Assert the tool's own result is structurally sound: traits reported, none failed.

    Checked against **structural** invariants, not the unit golden's exact numeric
    values — the smoke's cleaned input uses the ``qc_clean`` leg's own threshold, which
    may differ from the unit golden's canonical-default clean.
    """
    return [
        Check(
            "descriptive_stats: n_traits_reported > 0",
            isinstance(n_traits_reported, int) and n_traits_reported > 0,
            f"n_traits_reported={n_traits_reported!r}",
        ),
        Check(
            "descriptive_stats: n_failed == 0",
            n_failed == 0,
            f"n_failed={n_failed!r}",
        ),
    ]


def _version_num(ref: str) -> Optional[int]:
    """Parse the integer N from a ``v<N>`` run reference (else None)."""
    if isinstance(ref, str) and ref.startswith("v") and ref[1:].isdigit():
        return int(ref[1:])
    return None


def version_advance_check(first_ref: str, second_ref: str) -> Check:
    """Assert a second commit advances ``latest`` by exactly one version.

    Checked *relationally* (``N+1``), not against a hardcoded ``v1``/``v2`` — the
    smoke runs against a shared dev stack whose manifest may already hold prior
    versions (a local re-run or a CI retry without a bucket reset), so the
    starting version is not guaranteed to be ``v1``.
    """
    a, b = _version_num(first_ref), _version_num(second_ref)
    return Check(
        "second commit advances latest by one version",
        a is not None and b is not None and b == a + 1,
        f"first={first_ref!r} second={second_ref!r}",
    )


def import_clean_check() -> Check:
    """Assert ``import bloom_mcp`` is clean in a subprocess with Supabase env scrubbed.

    The Tier-2 ``_ports`` composition root constructs the Supabase adapters at module
    load; this proves that construction does not eagerly require Supabase (Tier-0).
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("SUPABASE_URL", "BLOOM_AGENT_KEY")
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import bloom_mcp; from bloom_mcp.tools import _ports"],
        env=env,
        capture_output=True,
        text=True,
    )
    detail = ""
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or [""])[-1]
        detail = f"exit={proc.returncode}: {tail}"
    return Check(
        "import bloom_mcp clean with no Supabase env", proc.returncode == 0, detail
    )


def retry(
    fn: Callable[[], object],
    *,
    attempts: int = RETRY_ATTEMPTS,
    delay: float = RETRY_DELAY_S,
):
    """Call ``fn`` up to ``attempts`` times, sleeping ``delay`` between tries.

    Absorbs read-after-write lag on the storage-api. Re-raises the last error if
    every attempt fails, so a genuine regression still surfaces.
    """
    last: Optional[BaseException] = None
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - bounded retry, last error re-raised
            last = exc
            print(f"  ... read-back attempt {i}/{attempts} failed ({exc}); retrying")
            if i < attempts:
                time.sleep(delay)
    assert last is not None
    raise last


# --- live wiring (exercised only with the dev stack up) -----------------------
def _configure_live_env() -> None:
    """Point BLOOM_*_DIR at host temp dirs before import.

    The dirs are registered for cleanup at interpreter exit so a smoke run leaves
    no host litter. Env is set here *before* the first ``import bloom_mcp`` in
    ``main`` because ``experiment_utils`` captures the dir globals at import time.
    ``BLOOM_TRAITS_DIR`` only needs to exist (boot validation still checks for the
    directory) — its contents are never read by ``SupabaseReader``'s DB-only raw
    tier, so unlike before this change there is no fixture to seed into it.
    """
    traits = Path(tempfile.mkdtemp(prefix="smoke_traits_"))
    out = Path(tempfile.mkdtemp(prefix="smoke_out_"))
    plots = Path(tempfile.mkdtemp(prefix="smoke_plots_"))
    for d in (traits, out, plots):
        atexit.register(shutil.rmtree, d, ignore_errors=True)
    os.environ["BLOOM_TRAITS_DIR"] = str(traits)
    os.environ["BLOOM_OUTPUT_DIR"] = str(out)
    os.environ["BLOOM_PLOTS_DIR"] = str(plots)
    os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost/plots")
    # The make target exports the host gateway; default only for bare invocations.
    os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")


def main() -> int:
    checks: list[Check] = []

    # 1) Tier-0 import-clean — BEFORE any live env is configured in this process.
    print(">>> checking import bloom_mcp is clean with no Supabase env ...")
    checks.append(import_clean_check())

    if not os.environ.get("BLOOM_AGENT_KEY"):
        checks.append(
            Check(
                "BLOOM_AGENT_KEY present",
                False,
                "unset — export it from .env.dev (the make target does this)",
            )
        )
        text, code = summarize(checks)
        print(text)
        return code

    if not QC_EXPERIMENT:
        checks.append(
            Check(
                "BLOOM_SMOKE_EXPERIMENT_ID present",
                False,
                "unset — set it to a numeric experiment id already seeded with trait "
                "rows in the target Postgres. SupabaseReader's raw tier is DB-only "
                "(bloom#551): there is no local-CSV upload path left for this script "
                "to fall back to, so this leg cannot invent a valid experiment itself.",
            )
        )
        text, code = summarize(checks)
        print(text)
        return code

    # 2) Configure the live env (before the first import) and import the adapters.
    _configure_live_env()

    from bloom_mcp import supabase_client as sc  # noqa: E402
    from bloom_mcp.data_access import SupabaseReader  # noqa: E402
    from bloom_mcp.result_store import SupabaseResultStore  # noqa: E402
    from bloom_mcp.tools import _ports  # noqa: E402

    _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # Bounded-retry each download too: if the manifest is visible but the object
    # still lags in storage-api, retry rather than record a spurious hard FAIL.
    def read_bytes(key: str) -> bytes:
        return retry(lambda: sc.get_storage_client().download(key))

    # === Tier-3 qc_clean leg ==================================================
    # Drive the granular qc_clean tool (#338) through the SAME real ports: clean
    # the raw turface input, persist a versioned `qc` run, then prove a
    # require_clean read resolves the committed *cleaned* artifact rather than the
    # raw input — the qc_clean -> pca_analysis(require_clean=True) composition.
    from bloom_mcp.contract import BloomMCPError  # noqa: E402
    from bloom_mcp.sections.sleap_roots.analysis.qc_clean import (  # noqa: E402
        QCCleanParams,
        qc_clean,
    )

    print(
        f">>> running qc_clean on {QC_EXPERIMENT} "
        f"(max_nans_per_trait={QC_MAX_NANS_PER_TRAIT}) through real ports ..."
    )
    qc_committed = False
    try:
        qc_clean(
            QCCleanParams(
                experiment=QC_EXPERIMENT,
                max_nans_per_trait=QC_MAX_NANS_PER_TRAIT,
            )
        )
        qc_committed = True
        checks.append(Check("qc_clean commits a cleaned run", True))
    except BloomMCPError as exc:
        checks.append(Check("qc_clean commits a cleaned run", False, f"error={exc!r}"))

    if qc_committed:
        # Read the committed qc run back through the port, then assert the v5
        # manifest + the cleaned-output catalog.
        qc_stored = retry(
            lambda: _ports.store().get_run(QC_EXPERIMENT, QC_TOOL_CLASS, "latest")
        )
        qc_manifest = retry(lambda: sc.read_json(qc_stored.manifest_path))
        checks.extend(
            qc_persist_checks(
                schema_version=qc_manifest.get("manifest_schema_version"),
                output_keys=qc_stored.output_keys,
                output_sha256=qc_stored.output_sha256,
                expected_outputs={CLEANED_CSV_NAME, CLEANUP_LOG_NAME},
            )
        )
        # Hash BOTH stored artifacts (_cleaned.csv + cleanup_log.json) and compare
        # to the manifest's recorded sha256 — the real-bytes integrity check.
        checks.extend(
            hash_checks(qc_stored.output_keys, qc_stored.output_sha256, read_bytes)
        )

        # The payoff: a fresh SupabaseReader().load_experiment(require_clean=True)
        # must now resolve the committed CLEANED version (not raw), and that frame
        # must be NaN-free across its trait columns.
        print(
            f">>> reading {QC_EXPERIMENT} back with require_clean=True "
            "(must resolve the cleaned run, not raw input) ..."
        )
        cleaned_frame = retry(
            lambda: SupabaseReader().load_experiment(QC_EXPERIMENT, require_clean=True)
        )
        qc_trait_nans = int(
            cleaned_frame.df[cleaned_frame.trait_cols].isna().sum().sum()
        )
        checks.extend(qc_cleaned_read_checks(cleaned_frame.source, qc_trait_nans))

        # === remove_outliers leg (#378) =======================================
        # Trim outlier samples from the cleaned version qc_clean just committed,
        # through the SAME real ports. remove_outliers persists under its own
        # dedicated `outliers` class (#420), which the reader prefers over `qc` for
        # a later require_clean read — proving qc_clean -> remove_outliers -> pca.
        from bloom_mcp.sections.sleap_roots.analysis.remove_outliers import (  # noqa: E402
            RemoveOutliersParams,
            remove_outliers,
        )

        n_pre_trim = len(cleaned_frame.df)  # the pre-trim clean row count
        print(
            f">>> running remove_outliers on {QC_EXPERIMENT} "
            # isolation_forest, not mahalanobis (#419) — see the module docstring's
            # remove_outliers leg note for why.
            f"(isolation_forest, seed={RO_SEED}) through real ports ..."
        )
        ro_committed = False
        try:
            remove_outliers(
                RemoveOutliersParams(
                    experiment=QC_EXPERIMENT, method="isolation_forest", seed=RO_SEED
                )
            )
            ro_committed = True
            checks.append(Check("remove_outliers commits a trimmed run", True))
        except BloomMCPError as exc:
            checks.append(
                Check("remove_outliers commits a trimmed run", False, f"error={exc!r}")
            )

        if ro_committed:
            ro_stored = retry(
                lambda: _ports.store().get_run(QC_EXPERIMENT, RO_TOOL_CLASS, "latest")
            )
            ro_manifest = retry(lambda: sc.read_json(ro_stored.manifest_path))
            checks.extend(
                ro_persist_checks(
                    schema_version=ro_manifest.get("manifest_schema_version"),
                    seed=ro_stored.seed,
                    tool=ro_stored.tool,
                    output_keys=ro_stored.output_keys,
                    output_sha256=ro_stored.output_sha256,
                    expected_outputs={CLEANED_CSV_NAME, RO_REPORT_NAME},
                )
            )
            # Generic v5 provenance (schema/seed/agent/environment/output-keys) —
            # the same contract the retired legacy clustering-workflow leg used to
            # prove, now anchored on remove_outliers (#412/devendor-bloommcp-analysis
            # C11.8: that leg drove run_clustering_workflow, retired in Phase 1).
            checks.extend(
                provenance_checks(
                    schema_version=ro_manifest.get("manifest_schema_version"),
                    seed=ro_stored.seed,
                    agent=ro_stored.agent,
                    environment=ro_stored.environment,
                    output_keys=ro_stored.output_keys,
                    output_sha256=ro_stored.output_sha256,
                )
            )
            checks.extend(
                hash_checks(ro_stored.output_keys, ro_stored.output_sha256, read_bytes)
            )

            print(
                f">>> reading {QC_EXPERIMENT} back with require_clean=True "
                "(must now resolve the TRIMMED run, fewer rows than the clean) ..."
            )
            trimmed_frame = retry(
                lambda: SupabaseReader().load_experiment(
                    QC_EXPERIMENT, require_clean=True
                )
            )
            ro_trait_nans = int(
                trimmed_frame.df[trimmed_frame.trait_cols].isna().sum().sum()
            )
            checks.extend(
                ro_trimmed_read_checks(
                    trimmed_frame.source,
                    ro_trait_nans,
                    len(trimmed_frame.df),
                    n_pre_trim,
                )
            )

            # A second remove_outliers commit must advance latest by one version
            # without clobbering the first: latest moves N -> N+1, and
            # get_run(first_ref) still resolves. (Moved here from the retired
            # legacy clustering-workflow leg — C11.8.)
            first_ro_ref = ro_stored.run_ref
            print(
                f">>> running remove_outliers on {QC_EXPERIMENT} a second time "
                "to advance latest ..."
            )
            try:
                remove_outliers(
                    RemoveOutliersParams(
                        experiment=QC_EXPERIMENT,
                        method="isolation_forest",
                        seed=RO_SEED,
                    )
                )
                checks.append(Check("remove_outliers run #2 succeeds", True))
                ro_stored2 = retry(
                    lambda: _ports.store().get_run(
                        QC_EXPERIMENT, RO_TOOL_CLASS, "latest"
                    )
                )
                checks.append(version_advance_check(first_ro_ref, ro_stored2.run_ref))
                ro_prior = retry(
                    lambda: _ports.store().get_run(
                        QC_EXPERIMENT, RO_TOOL_CLASS, first_ro_ref
                    )
                )
                checks.append(
                    Check(
                        "prior version still resolves (not clobbered)",
                        ro_prior.run_ref == first_ro_ref,
                        f"first_ref={first_ro_ref!r} resolved={ro_prior.run_ref!r}",
                    )
                )
            except BloomMCPError as exc:
                checks.append(
                    Check("remove_outliers run #2 succeeds", False, f"error={exc!r}")
                )

        # === clustering leg (#309) ============================================
        # Cluster the latest cleaned version (the trim if remove_outliers ran, else
        # the qc_clean clean) through the SAME real ports. clustering is a pure
        # consumer: require_clean resolves the committed cleaned source, and it
        # persists a versioned `clustering` run under its own class — the
        # qc_clean -> ... -> clustering(require_clean=True) composition, in parallel
        # with the pca_analysis consumer.
        from bloom_mcp.sections.sleap_roots.analysis.clustering import (  # noqa: E402
            ClusteringParams,
            clustering,
        )

        print(
            f">>> running clustering on {QC_EXPERIMENT} "
            f"(kmeans, seed={CL_SEED}) through real ports ..."
        )
        cl_committed = False
        cl_source: object = None
        try:
            cl_result = clustering(
                ClusteringParams(
                    experiment=QC_EXPERIMENT, method="kmeans", seed=CL_SEED
                )
            )
            cl_committed = True
            cl_source = cl_result.source
            checks.append(Check("clustering commits a run", True))
        except BloomMCPError as exc:
            checks.append(Check("clustering commits a run", False, f"error={exc!r}"))

        if cl_committed:
            cl_stored = retry(
                lambda: _ports.store().get_run(QC_EXPERIMENT, CL_TOOL_CLASS, "latest")
            )
            cl_manifest = retry(lambda: sc.read_json(cl_stored.manifest_path))
            checks.extend(
                clustering_persist_checks(
                    schema_version=cl_manifest.get("manifest_schema_version"),
                    seed=cl_stored.seed,
                    tool=cl_stored.tool,
                    source=cl_source,
                    output_keys=cl_stored.output_keys,
                    output_sha256=cl_stored.output_sha256,
                    expected_outputs={CL_LABELS_NAME, CL_RESULT_NAME},
                )
            )
            checks.extend(
                hash_checks(cl_stored.output_keys, cl_stored.output_sha256, read_bytes)
            )

        # === hierarchical clustering leg (#422) ==============================
        # Validate the deterministic arm: hierarchical clustering consumes the same
        # cleaned version through the real ports, recording seed=None in provenance.
        print(
            f">>> running clustering on {QC_EXPERIMENT} "
            f"(hierarchical) through real ports ..."
        )
        hier_source: object = None
        hier_committed = False
        try:
            hier_result = clustering(
                ClusteringParams(experiment=QC_EXPERIMENT, method="hierarchical")
            )
            hier_committed = True
            hier_source = hier_result.source
            checks.append(Check("hierarchical clustering commits a run", True))
        except BloomMCPError as exc:
            checks.append(
                Check("hierarchical clustering commits a run", False, f"error={exc!r}")
            )

        if hier_committed:
            hier_stored = retry(
                lambda: _ports.store().get_run(QC_EXPERIMENT, CL_TOOL_CLASS, "latest")
            )
            hier_manifest = retry(lambda: sc.read_json(hier_stored.manifest_path))
            checks.extend(
                hierarchical_clustering_persist_checks(
                    schema_version=hier_manifest.get("manifest_schema_version"),
                    seed=hier_stored.seed,
                    tool=hier_stored.tool,
                    source=hier_source,
                    output_keys=hier_stored.output_keys,
                    output_sha256=hier_stored.output_sha256,
                    expected_outputs={CL_LABELS_NAME, CL_RESULT_NAME},
                )
            )
            checks.extend(
                hash_checks(
                    hier_stored.output_keys, hier_stored.output_sha256, read_bytes
                )
            )

        # === descriptive_stats leg (#488) =====================================
        # Consume the same latest cleaned version through the SAME real ports,
        # persisting a versioned `stats` run under its own tool class — proving
        # qc_clean -> ... -> descriptive_stats(require_clean=True), in parallel with
        # the other consumers above.
        from bloom_mcp.sections.sleap_roots.analysis.descriptive_stats import (  # noqa: E402
            DescriptiveStatsParams,
            descriptive_stats,
        )

        print(
            f">>> running descriptive_stats on {QC_EXPERIMENT} through real ports ..."
        )
        ds_committed = False
        ds_source: object = None
        ds_n_traits_reported: object = None
        ds_n_failed: object = None
        try:
            ds_result = descriptive_stats(
                DescriptiveStatsParams(experiment=QC_EXPERIMENT)
            )
            ds_committed = True
            ds_source = ds_result.source
            ds_n_traits_reported = ds_result.n_traits_reported
            ds_n_failed = ds_result.n_failed
            checks.append(Check("descriptive_stats commits a run", True))
        except BloomMCPError as exc:
            checks.append(
                Check("descriptive_stats commits a run", False, f"error={exc!r}")
            )

        if ds_committed:
            checks.extend(stats_result_checks(ds_n_traits_reported, ds_n_failed))
            ds_stored = retry(
                lambda: _ports.store().get_run(
                    QC_EXPERIMENT, STATS_TOOL_CLASS, "latest"
                )
            )
            ds_manifest = retry(lambda: sc.read_json(ds_stored.manifest_path))
            checks.extend(
                stats_persist_checks(
                    schema_version=ds_manifest.get("manifest_schema_version"),
                    seed=ds_stored.seed,
                    tool=ds_stored.tool,
                    source=ds_source,
                    output_keys=ds_stored.output_keys,
                    output_sha256=ds_stored.output_sha256,
                    expected_outputs={STATS_CSV_NAME},
                )
            )
            checks.extend(
                hash_checks(ds_stored.output_keys, ds_stored.output_sha256, read_bytes)
            )

    text, code = summarize(checks)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())

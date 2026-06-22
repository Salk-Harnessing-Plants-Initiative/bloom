"""Live persistence smoke — drive a workflow through the REAL ports against the dev stack.

This runs on the **host** against the running dev stack (Supabase + storage-api +
MinIO), so it must override what ``.env.dev`` configures for in-container processes:

  * ``SUPABASE_URL`` — ``.env.dev`` points this at the in-container gateway
    ``http://kong:8000``; the host reaches Kong at ``http://localhost:$KONG_HTTP_PORT``.
    The ``make bloommcp-smoke`` target exports the host value before launching this
    script (it derives the port from ``.env.dev``); we fall back to localhost:8000 only
    for a bare ``python scripts/live_persistence_smoke.py`` invocation.
  * ``BLOOM_TRAITS_DIR`` / ``BLOOM_OUTPUT_DIR`` / ``BLOOM_PLOTS_DIR`` — ``.env.dev`` points
    these at in-container ``/app/data/...`` paths; we override them with host temp dirs,
    seeding the traits dir with the ``turface`` fixture.

``bloom_mcp.experiment_utils`` captures ``TRAITS_DIR`` / ``OUTPUT_DIR`` / ``PLOTS_DIR``
from the environment **at import time**, so the env must be set *before* ``import
bloom_mcp`` (and we hard-set the module globals afterwards as a belt-and-suspenders).

What it asserts — the provenance the Tier-2 persistence layer (#323) promises, against the
real `SupabaseResultStore`/`SupabaseReader` round-trip rather than in-memory fakes:

  * ``import bloom_mcp`` (incl. the ``_ports`` composition root) is clean with no Supabase
    env — the Tier-0 lazy-validation contract — checked in a scrubbed subprocess first;
  * the committed run's manifest is schema v3 with a non-null real ``seed`` (== 42 for a
    stochastic clustering/kmeans run), ``agent`` == ``bloom_agent``, populated
    ``environment``, and matching ``output_sha256`` / ``output_keys`` maps;
  * each recorded ``output_sha256`` equals the SHA-256 of the bytes actually stored;
  * ``get_run("latest")`` reads the committed run back, and a second commit advances
    ``latest`` from ``v1`` to ``v2``.

Every failure mode (workflow error, hash mismatch, read-after-write timeout, import leak)
routes through the per-check summary and a non-zero exit — never an unlabelled traceback.

Run via ``make bloommcp-smoke`` (preferred) or, with the dev stack up + migrated and
``BLOOM_AGENT_KEY`` exported, ``cd bloommcp && uv run python scripts/live_persistence_smoke.py``.

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
EXPERIMENT = "turface.csv"
TOOL_CLASS = "clustering"
EXPECTED_SEED = 42  # clustering/kmeans resolves a fixed RANDOM_STATE
EXPECTED_AGENT = "bloom_agent"
_HERE = Path(__file__).resolve().parent
FIXTURE = _HERE.parent / "tests" / "fixtures" / "turface_19_final_data.csv"

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
        "SMOKE PASSED ✅ — real write path persists full v3 provenance with a real seed."
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
    """Assert the v3 provenance fields on the committed run's latest entry."""
    return [
        Check(
            "manifest schema == 3",
            schema_version == 3,
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
    """Point BLOOM_*_DIR at host temp dirs (seeded with the fixture) before import.

    The dirs are registered for cleanup at interpreter exit so a smoke run leaves
    no host litter. Env is set here *before* the first ``import bloom_mcp`` in
    ``main`` because ``experiment_utils`` captures the dir globals at import time.
    """
    if not FIXTURE.exists():
        raise FileNotFoundError(f"fixture not found: {FIXTURE}")
    traits = Path(tempfile.mkdtemp(prefix="smoke_traits_"))
    out = Path(tempfile.mkdtemp(prefix="smoke_out_"))
    plots = Path(tempfile.mkdtemp(prefix="smoke_plots_"))
    for d in (traits, out, plots):
        atexit.register(shutil.rmtree, d, ignore_errors=True)
    shutil.copy(FIXTURE, traits / EXPERIMENT)
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

    # 2) Configure the live env (before the first import) and import the adapters.
    _configure_live_env()

    from bloom_mcp import supabase_client as sc  # noqa: E402
    from bloom_mcp.data_access import SupabaseReader  # noqa: E402
    from bloom_mcp.result_store import SupabaseResultStore  # noqa: E402
    from bloom_mcp.tools import _ports  # noqa: E402

    _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    from bloom_mcp.tools.workflows.clustering import (  # noqa: E402
        run_clustering_workflow,
    )

    # Bounded-retry each download too: if the manifest is visible but the object
    # still lags in storage-api, retry rather than record a spurious hard FAIL.
    def read_bytes(key: str) -> bytes:
        return retry(lambda: sc.get_storage_client().download(key))

    # 3) First run through the real ports.
    print(">>> running clustering(kmeans) on turface.csv through real ports ...")
    resp = run_clustering_workflow(EXPERIMENT, algorithm="kmeans")
    if not isinstance(resp, dict) or "error" in resp:
        checks.append(Check("workflow #1 succeeds", False, f"response={resp!r}"))
        text, code = summarize(checks)
        print(text)
        return code
    checks.append(Check("workflow #1 succeeds", True))

    # 4) Read the committed run back through the port (get_run), with bounded retry.
    stored = retry(lambda: _ports.store().get_run(EXPERIMENT, TOOL_CLASS, "latest"))
    first_ref = stored.run_ref
    checks.append(
        Check(
            "get_run('latest') resolves the committed run",
            bool(first_ref),
            f"run_ref={first_ref!r}",
        )
    )

    # 5) Read the manifest back from storage for the schema-version assertion.
    manifest = retry(lambda: sc.read_json(stored.manifest_path))
    checks.extend(
        provenance_checks(
            schema_version=manifest.get("manifest_schema_version"),
            seed=stored.seed,
            agent=stored.agent,
            environment=stored.environment,
            output_keys=stored.output_keys,
            output_sha256=stored.output_sha256,
        )
    )

    # 6) Hash each stored object and compare to the recorded sha256.
    checks.extend(hash_checks(stored.output_keys, stored.output_sha256, read_bytes))

    # 7) A second run must advance latest by one version without clobbering the
    #    first: latest moves N -> N+1, and get_run(first_ref) still resolves.
    print(">>> running clustering(kmeans) a second time to advance latest ...")
    resp2 = run_clustering_workflow(EXPERIMENT, algorithm="kmeans")
    if not isinstance(resp2, dict) or "error" in resp2:
        checks.append(Check("workflow #2 succeeds", False, f"response={resp2!r}"))
    else:
        checks.append(Check("workflow #2 succeeds", True))
        stored2 = retry(
            lambda: _ports.store().get_run(EXPERIMENT, TOOL_CLASS, "latest")
        )
        checks.append(version_advance_check(first_ref, stored2.run_ref))
        prior = retry(lambda: _ports.store().get_run(EXPERIMENT, TOOL_CLASS, first_ref))
        checks.append(
            Check(
                "prior version still resolves (not clobbered)",
                prior.run_ref == first_ref,
                f"first_ref={first_ref!r} resolved={prior.run_ref!r}",
            )
        )

    text, code = summarize(checks)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())

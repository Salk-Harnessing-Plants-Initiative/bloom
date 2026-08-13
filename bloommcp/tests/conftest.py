"""Shared fixtures + Supabase-free env for the bloom_mcp unit suite.

The whole point of Tier 0 is that this suite runs with **no live Supabase**:
``SUPABASE_URL`` / ``BLOOM_AGENT_KEY`` are explicitly removed so the lazy
validation in ``bloom_mcp.supabase_client`` is exercised, and the non-secret
data directories that ``bloom_mcp.experiment_utils`` requires at import are
pointed at a throwaway temp dir (mirrors ``tests/unit/test_workflow_scaffolding``
in the repo root).
"""

from __future__ import annotations

import os
import tempfile

# --- Guarantee Supabase is absent before any bloom_mcp import ---
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("BLOOM_AGENT_KEY", None)

# --- Non-secret data dirs experiment_utils reads (validated at startup, not import) ---
_TMP = tempfile.mkdtemp(prefix="bloom_mcp_tests_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://localhost/plots")


# --- In-memory Supabase Storage boundary (Tier 2 adapter tests) ---------------
#
# The storage stack funnels every read/write through the eight bloom_mcp.supabase_client
# helpers (+ the names re-bound into bloom_mcp.manifest.manifest). This fixture
# fakes that boundary in memory so SupabaseReader / SupabaseResultStore run with
# no live Supabase and no `supabase.create_client` call.

import dataclasses  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Optional  # noqa: E402

import pytest  # noqa: E402

from bloom_mcp.data_access import (  # noqa: E402
    AmbiguousSourceSelectionError,
    FakeReader,
    SourceInfo,
    SourcePinNotFoundError,
)


class _InMemoryObjectStore:
    """A dict-backed stand-in for the bloommcp-data bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def list_prefix(self, prefix: str) -> list[str]:
        norm = (prefix.rstrip("/") + "/") if prefix else ""
        names: set[str] = set()
        for key in self.objects:
            if key.startswith(norm):
                names.add(key[len(norm) :].split("/", 1)[0])
        return sorted(n for n in names if n)

    def read_json(self, key: str) -> dict:
        if key not in self.objects:
            raise KeyError(f"object not found: {key}")
        return json.loads(self.objects[key].decode("utf-8"))

    def write_json(self, key: str, payload: dict) -> None:
        self.objects[key] = json.dumps(payload, indent=2, sort_keys=True).encode(
            "utf-8"
        )

    def upload_file(self, key: str, local_path) -> None:
        self.objects[key] = Path(local_path).read_bytes()

    def download_file(self, key: str, local_path) -> None:
        if key not in self.objects:
            raise KeyError(f"object not found: {key}")
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.objects[key])

    def delete_files(
        self, keys: list[str], *, timeout_seconds: float | None = None
    ) -> None:
        del timeout_seconds  # in-memory: no network round-trip to bound
        for key in keys:
            self.objects.pop(key, None)

    def create_signed_url(self, key: str, expires_in: int) -> str:
        # Synthesized, not a real signed URL — this store never talks to a
        # real backend, so there is nothing to sign against (bloom#581).
        return f"fake://signed/{key}?expires_in={expires_in}"

    def get_object_size(self, key: str) -> int:
        # A real len() against the genuine in-memory bytes this store holds —
        # not synthesized, unlike create_signed_url above (bloom#599).
        if key not in self.objects:
            raise KeyError(f"object not found: {key}")
        return len(self.objects[key])


@pytest.fixture
def fake_supabase_storage(monkeypatch):
    """Patch the Supabase storage boundary with an in-memory object store.

    Returns the store so tests can seed/inspect objects directly.
    """
    import bloom_mcp.manifest.manifest as _manifest
    import bloom_mcp.supabase_client as _sc

    store = _InMemoryObjectStore()
    for name in (
        "list_prefix",
        "read_json",
        "write_json",
        "upload_file",
        "download_file",
        "delete_files",
        "create_signed_url",
        "get_object_size",
    ):
        monkeypatch.setattr(_sc, name, getattr(store, name))
    for name in ("list_prefix", "read_json", "write_json"):
        monkeypatch.setattr(_manifest, name, getattr(store, name))

    def _no_network(*_a, **_k):  # pragma: no cover - guard
        raise AssertionError("supabase.create_client called — test hit the network")

    monkeypatch.setattr(_sc.supabase, "create_client", _no_network)
    return store


# --- In-memory Postgres/PostgREST boundary (Tier 2 DB-direct raw tier) -------
#
# SupabaseReader's raw tier calls two module-level bloom_mcp.supabase_client
# functions: `call_rpc` (get_experiment_traits / list_experiment_trait_sources)
# and `get_postgrest_client` (a direct `cyl_experiments` table read for
# list_experiments()). This fake monkeypatches both so the DB-direct raw tier
# runs with no live Supabase/Postgres — a distinct boundary from
# `fake_supabase_storage` above (that one fakes object storage; this one fakes
# table/RPC reads).


class _FakeQueryResponse:
    def __init__(self, data: list[dict]) -> None:
        self.data = data


class _FakeTableQuery:
    """Minimal stand-in for a PostgREST fluent query — only what supabase_reader uses."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    def select(self, *_args, **_kwargs) -> "_FakeTableQuery":
        return self

    def eq(self, column: str, value) -> "_FakeTableQuery":
        self._rows = [r for r in self._rows if r.get(column) == value]
        return self

    def execute(self) -> _FakeQueryResponse:
        return _FakeQueryResponse(list(self._rows))


class _FakePostgrestClient:
    def __init__(self, tables: dict[str, list[dict]]) -> None:
        self._tables = tables

    def table(self, name: str) -> _FakeTableQuery:
        return _FakeTableQuery(self._tables.get(name, []))


class FakeSupabaseDB:
    """Seedable in-memory double for the RPC/table-read boundary.

    Seed with `seed_experiment`, `seed_traits`, and `seed_sources`; a call to a
    function/table with nothing seeded returns an empty result, matching a real
    PostgREST response for no matching rows.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {"cyl_experiments": []}
        self._traits: dict[int, list[dict]] = {}
        self._sources: dict[int, list[dict]] = {}
        # Keyed by (function_name, experiment_id); experiment_id=None fails
        # every call to that function regardless of experiment.
        self.rpc_errors: dict[tuple[str, Optional[int]], Exception] = {}
        # Every call_rpc invocation's function name, in order -- lets a test
        # assert call counts (e.g. "one bulk call, not one per experiment")
        # without a separate spy/monkeypatch layer.
        self.rpc_calls: list[str] = []

    def seed_experiment(self, experiment_id: int, name: str) -> None:
        self.tables["cyl_experiments"].append({"id": experiment_id, "name": name})

    def seed_traits(self, experiment_id: int, rows: list[dict]) -> None:
        self._traits.setdefault(experiment_id, []).extend(rows)

    def seed_sources(self, experiment_id: int, sources: list[dict]) -> None:
        self._sources.setdefault(experiment_id, []).extend(sources)

    def fail_rpc(
        self,
        function_name: str,
        exc: Exception,
        *,
        experiment_id: Optional[int] = None,
    ) -> None:
        self.rpc_errors[(function_name, experiment_id)] = exc

    def call_rpc(self, function_name: str, params: dict) -> list[dict]:
        self.rpc_calls.append(function_name)
        experiment_id = params.get("experiment_id_")
        for key in ((function_name, experiment_id), (function_name, None)):
            if key in self.rpc_errors:
                raise self.rpc_errors[key]

        if function_name == "list_experiment_trait_sources":
            return list(self._sources.get(experiment_id, []))
        if function_name == "get_experiment_traits":
            rows = list(self._traits.get(experiment_id, []))
            source_id = params.get("source_id_")
            if source_id is not None:
                rows = [r for r in rows if r.get("source_id") == source_id]
            run_id = params.get("run_id_")
            if run_id is not None:
                rows = [r for r in rows if r.get("pipeline_run_id") == run_id]
            return rows
        if function_name == "get_experiment_summary_counts":
            # Mirrors the real SQL function's shape: aggregate counts derived
            # from the same per-experiment trait rows `get_experiment_traits`
            # reads above, applying the same source_id_/run_id_ filtering. An
            # experiment_id_=None call (list_experiments()'s only use today)
            # covers every seeded experiment; one with no matching rows after
            # filtering is absent from the result, not zero-valued.
            source_id = params.get("source_id_")
            run_id = params.get("run_id_")
            ids = (
                [experiment_id]
                if experiment_id is not None
                else list(self._traits.keys())
            )
            result = []
            for eid in ids:
                rows = list(self._traits.get(eid, []))
                if source_id is not None:
                    rows = [r for r in rows if r.get("source_id") == source_id]
                if run_id is not None:
                    rows = [r for r in rows if r.get("pipeline_run_id") == run_id]
                if not rows:
                    continue
                result.append(
                    {
                        "experiment_id": eid,
                        "n_plants": len({r["plant_id"] for r in rows}),
                        "n_traits": len({r["trait_name"] for r in rows}),
                    }
                )
            return result
        raise AssertionError(f"unfaked RPC function: {function_name!r}")

    def get_postgrest_client(self) -> _FakePostgrestClient:
        return _FakePostgrestClient(self.tables)


@pytest.fixture
def fake_supabase_db(monkeypatch):
    """Patch the Postgres/PostgREST boundary with an in-memory double.

    Returns the double so tests can seed experiments/traits/sources directly.
    """
    import bloom_mcp.supabase_client as _sc

    fake = FakeSupabaseDB()
    monkeypatch.setattr(_sc, "call_rpc", fake.call_rpc)
    monkeypatch.setattr(_sc, "get_postgrest_client", fake.get_postgrest_client)
    return fake


def _seed_multi_source_experiment(
    fake_supabase_db,
    experiment_id: int,
    source_ids: list,
    *,
    name: str = "multi-source exp",
) -> int:
    """Seed one experiment with a distinct plant+trait row per ``source_id``,
    plus a matching ``seed_sources`` entry for each (#626).

    Most existing fixtures (e.g. ``_seed_two_plant_experiment`` in
    ``test_supabase_reader.py``) seed a single source; tool-layer tests for
    source discovery/pinning (``core_list_experiment_sources``, ``qc_clean``'s
    advisory note) need >1 *real* source against the monkeypatched
    ``SupabaseReader`` boundary — ``FakeReader`` deliberately has no source
    concept at all (see ``test_fake_reader_is_not_source_selectable``), so it
    cannot stand in for this.
    """
    fake_supabase_db.seed_experiment(experiment_id, name)
    rows = []
    sources = []
    for i, source_id in enumerate(source_ids, start=1):
        rows.append(
            {
                "scan_id": 1000 + i,
                "date_scanned": "2026-07-01",
                "plant_age_days": 10,
                "wave_number": 1,
                "plant_id": i,
                "germ_day": 0,
                "plant_qr_code": f"QR{i}",
                "accession_name": f"acc-{i}",
                "trait_name": "root_length",
                "source_id": source_id,
                "trait_value": float(i),
            }
        )
        sources.append(
            {
                "source_id": source_id,
                "source_name": f"run-{source_id}",
                "pipeline_run_id": f"p{source_id}",
            }
        )
    fake_supabase_db.seed_traits(experiment_id, rows)
    fake_supabase_db.seed_sources(experiment_id, sources)
    return experiment_id


@pytest.fixture
def seed_multi_source_experiment():
    """Factory fixture: ``seed_multi_source_experiment(fake_supabase_db, 42, [9, 10])``.

    A fixture (rather than a plain importable helper) so every test module
    under ``tests/`` can use it with no cross-module import — this package
    has no top-level ``tests/__init__.py``, so ``from tests.conftest import
    ...`` does not resolve.
    """
    return _seed_multi_source_experiment


class _MultiSourceFakeReader(FakeReader):
    """Test-only double: ``FakeReader`` + a bolted-on ``SourceSelectable`` surface.

    Local to the test tree only — the *shared* ``FakeReader`` class must stay
    non-``SourceSelectable`` (``test_fake_reader_is_not_source_selectable`` in
    ``test_supabase_reader.py`` locks that in). Exercises source-pin/source_note
    logic (``qc_clean``, ``qc_inspect``, ``load_experiment_data``,
    ``_ports.load_frame``) without needing a full DB-shaped ``SupabaseReader``
    fixture (see ``seed_multi_source_experiment`` above for that heavier path).

    Was duplicated near-verbatim across ``test_qc_clean_tool.py``,
    ``test_qc_inspect_tool.py``, and ``test_ports.py`` before being
    consolidated here (#626 PR review).
    """

    def __init__(self, source_ids, *, resolve_when_unpinned: bool = True):
        super().__init__()
        self._sources = [
            SourceInfo(
                source_id=sid, source_name=f"run-{sid}", pipeline_run_id=f"p{sid}"
            )
            for sid in source_ids
        ]
        # test_ports.py's raw-tier-forcing scenario needs an UNPINNED call to leave
        # the cleaned-version resolution alone (no source touched) so it can prove
        # version="latest" resolves the cleaned version today; every other caller
        # needs an unpinned call to resolve "latest" so source_note /
        # available_source_count populate the same way SupabaseReader's do.
        self._resolve_when_unpinned = resolve_when_unpinned

    def list_sources(self, name):
        return list(self._sources)

    def resolve_source(self, name, *, source_id=None, run_id=None):
        if source_id is not None and run_id is not None:
            raise AmbiguousSourceSelectionError("both source_id and run_id given")
        if source_id is not None:
            for s in self._sources:
                if s.source_id == source_id:
                    return s
            raise SourcePinNotFoundError(f"no source_id={source_id}")
        if run_id is not None:
            for s in self._sources:
                if s.pipeline_run_id == run_id:
                    return s
            raise SourcePinNotFoundError(f"no run_id={run_id}")
        # max by source_id, not constructor order -- matches SupabaseReader's own
        # unpinned resolution exactly (the experiment-wide max source_id), so a
        # caller constructing sources out of ascending order still gets the same
        # semantics the real adapter would (PR #644 review: this was latent,
        # masked only by every existing caller happening to pass ascending ids).
        return max(self._sources, key=lambda s: s.source_id) if self._sources else None

    def load_experiment(
        self,
        name,
        *,
        version="latest",
        require_clean=False,
        source_id=None,
        run_id=None,
    ):
        if source_id is not None or run_id is not None or self._resolve_when_unpinned:
            resolved = self.resolve_source(name, source_id=source_id, run_id=run_id)
        else:
            resolved = None
        frame = super().load_experiment(
            name, version=version, require_clean=require_clean
        )
        if resolved is not None:
            frame = dataclasses.replace(
                frame,
                resolved_source=resolved,
                available_source_count=len(self._sources),
            )
        return frame


@pytest.fixture
def make_multi_source_fake_reader():
    """Factory fixture: ``make_multi_source_fake_reader([9, 10, 11])`` ->
    a ``_MultiSourceFakeReader`` instance. See that class's docstring.

    A fixture (rather than a plain importable class) for the same reason as
    ``seed_multi_source_experiment`` above — no top-level ``tests/__init__.py``.
    """
    return _MultiSourceFakeReader


# --- In-memory RPC boundary (bloommcp_usage / call_rpc) -----------------------
#
# `call_rpc` is bloommcp's one seam for calling a Postgres RPC (e.g.
# `record_bloommcp_usage`, see bloom_mcp.usage). This fixture fakes that
# boundary in memory, mirroring `fake_supabase_storage`'s shape: monkeypatch
# the module-level name directly (not the client `.rpc()` call itself) so
# tests never touch a network or real Postgres.


class _FakeRpc:
    """Records every `call_rpc` call and returns a caller-configured result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._results: dict[str, list[dict]] = {}

    def set_result(self, function_name: str, rows: list[dict]) -> None:
        self._results[function_name] = rows

    def __call__(self, function_name: str, params: dict) -> list[dict]:
        self.calls.append((function_name, dict(params)))
        return self._results.get(function_name, [])


@pytest.fixture
def fake_bloommcp_rpc(monkeypatch):
    """Patch `bloom_mcp.supabase_client.call_rpc` with an in-memory recorder.

    Returns the fake so tests can inspect `.calls` or seed `.set_result(...)`.
    """
    import bloom_mcp.supabase_client as _sc

    fake = _FakeRpc()
    monkeypatch.setattr(_sc, "call_rpc", fake)
    return fake


# --- Local storage-backend fixture for manifest-level tests (bloom#585) -------
#
# `trim_staleness`/`_resolve_versioned_cleaned` read manifests through
# `bloom_mcp.manifest.AnalysisDir`, a seam below `fake_supabase_storage`'s ports
# (it talks to `bloom_mcp.storage_backend.active_backend()` directly). Tests that
# need real, on-disk manifests use the `local` backend instead of faking it.
# Promoted here (out of `test_storage_backend.py`, its original home) so any test
# file can use it — originally fixture-local, it only reset the backend on
# setup; promoted with a teardown reset added, so a test using it here can never
# leak a stale backend into a test in a *different* file that has no equivalent
# autouse net (test_storage_backend.py's own autouse `_reset_backend` fixture
# happened to mask this for tests within that one file only).


@pytest.fixture
def local_manifest_backend(monkeypatch, tmp_path):
    """Point the storage-backend seam at a throwaway local root for one test.

    Returns `tmp_path` (the fixture's own scratch dir, not the storage root)
    for parity with the fixture's original signature. Manifest-building
    helpers (`write_cleaned_manifest` etc.) live in `manifest_fixtures.py`, a
    plain (non-`conftest`) module name — `bloommcp/tests/smoke/` has its own,
    unrelated `conftest.py`, and pytest's default (no-`__init__.py`) import
    mode gives every same-named module one shared `sys.modules` slot, so a
    bare `from conftest import ...` anywhere in the tree is ambiguous.
    """
    import bloom_mcp.storage_backend as sb

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()
    yield tmp_path
    sb.reset_backend_for_tests()

"""FakeResultStore ↔ SupabaseResultStore behave equivalently for observers."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import (
    CommitFailedError,
    FakeResultStore,
    RunNotFoundError,
    RunStateError,
    SupabaseResultStore,
)
from bloom_mcp.storage import AnalysisDir

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _prov(seed: int = 5) -> Provenance:
    return Provenance.stamp(tool="t", params={"a": 1}, seed=seed)


@pytest.fixture
def stores(fake_supabase_storage):
    # fake_supabase_storage makes the Supabase adapter run in-memory.
    return {"fake": FakeResultStore(), "supabase": SupabaseResultStore()}


def _inject_commit_failure(
    kind, store, monkeypatch, *, experiment, tool_class, after_outputs, num_outputs
):
    """Force the next commit() for (experiment, tool_class) to fail after
    `after_outputs` of `num_outputs` are recorded — one shared scenario body,
    two structurally different injection techniques per backend."""
    if kind == "fake":
        store.fail_next_commit(experiment, tool_class, after_outputs=after_outputs)
        return

    import bloom_mcp.result_store.supabase_store as _store_mod
    import bloom_mcp.supabase_client as sc

    if after_outputs >= num_outputs:
        # All uploads succeed; fail at the manifest-write step. One-shot, like
        # the upload-side `_flaky` below, so a retry on the same handle can
        # actually succeed instead of failing identically forever.
        real_write = _store_mod.write_manifest
        calls = {"n": 0}

        def _boom_write(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated failure (manifest write)")
            return real_write(*a, **k)

        monkeypatch.setattr(_store_mod, "write_manifest", _boom_write)
    else:
        real_upload = sc.upload_file
        calls = {"n": 0}

        def _flaky(key, path):
            calls["n"] += 1
            if calls["n"] == after_outputs + 1:
                raise RuntimeError("simulated failure (upload)")
            return real_upload(key, path)

        monkeypatch.setattr(sc, "upload_file", _flaky)


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_create_commit_get_parity(kind, stores):
    store = stores[kind]
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(), user_label="lbl"
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.seed == 5
    assert (
        set(stored.outputs)
        == set(stored.output_keys)
        == set(stored.output_sha256)
        == {"cleaned"}
    )
    assert stored.output_sha256["cleaned"] == hashlib.sha256(b"data").hexdigest()
    # Logical keys use forward slashes on every OS.
    assert "\\" not in stored.output_keys["cleaned"]
    assert stored.output_keys["cleaned"].startswith("bloommcp_output/qc_exp/")
    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_not_found_and_lifecycle_parity(kind, stores):
    store = stores[kind]

    # Unknown run → not found (both backends).
    with pytest.raises(RunNotFoundError):
        store.get_run("x.csv", "qc", "latest")

    # Multi-commit increments and resolves latest identically.
    for _ in range(2):
        run = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
        (run.staging_dir / "o.csv").write_bytes(b"d")
        store.commit(run, {"o": "o.csv"})
    assert [r.run_ref for r in store.list_runs("x.csv", "qc")] == ["v1", "v2"]
    assert store.get_run("x.csv", "qc", "latest").run_ref == "v2"

    # Double-commit is rejected on both.
    run = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"d")
    store.commit(run, {"o": "o.csv"})
    with pytest.raises(RunStateError):
        store.commit(run, {"o": "o.csv"})

    # Empty outputs rejected on both.
    run2 = store.create_run(experiment="x.csv", tool_class="qc", provenance=_prov())
    with pytest.raises(ValueError):
        store.commit(run2, {})


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_commit_failure_retry_parity(kind, stores, monkeypatch):
    """#325: both backends leave nothing recorded after an injected commit
    failure, keep the handle retryable, and succeed on retry — exercising
    version_dir namespacing (non-shared logic), not just hash_outputs."""
    store = stores[kind]
    run = store.create_run(experiment="fail.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    (run.staging_dir / "b.csv").write_bytes(b"b")

    _inject_commit_failure(
        kind,
        store,
        monkeypatch,
        experiment="fail.csv",
        tool_class="qc",
        after_outputs=1,
        num_outputs=2,
    )
    with pytest.raises(CommitFailedError):
        store.commit(run, {"a": "a.csv", "b": "b.csv"})

    assert store.list_runs("fail.csv", "qc") == []

    stored = store.commit(run, {"a": "a.csv", "b": "b.csv"})
    assert stored.run_ref == "v1"
    assert stored.version_dir.startswith("v1")
    assert store.get_run("fail.csv", "qc", "latest").run_ref == "v1"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_commit_failure_after_all_uploads_before_manifest_write_parity(
    kind, stores, monkeypatch, fake_supabase_storage
):
    """The failure boundary after every output is uploaded/recorded but
    before the manifest/run is actually appended — untested until now, and
    the case most relevant to gap A's cleanup: every output has *already*
    been uploaded when this fires, so cleanup must delete all of them, not
    leave any as an orphan."""
    store = stores[kind]
    run = store.create_run(
        experiment="boundary.csv", tool_class="qc", provenance=_prov()
    )
    (run.staging_dir / "a.csv").write_bytes(b"a")
    (run.staging_dir / "b.csv").write_bytes(b"b")

    _inject_commit_failure(
        kind,
        store,
        monkeypatch,
        experiment="boundary.csv",
        tool_class="qc",
        after_outputs=2,
        num_outputs=2,
    )
    with pytest.raises(CommitFailedError):
        store.commit(run, {"a": "a.csv", "b": "b.csv"})

    assert store.list_runs("boundary.csv", "qc") == []
    if kind == "supabase":
        assert not any(
            k.startswith("bloommcp_output/qc_boundary/")
            for k in fake_supabase_storage.objects
        )

    stored = store.commit(run, {"a": "a.csv", "b": "b.csv"})
    assert stored.run_ref == "v1"
    assert store.get_run("boundary.csv", "qc", "latest").run_ref == "v1"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_duplicate_id_reallocates_to_distinct_ids_parity(kind, stores):
    """#325: two create_run calls against empty state allocate the same
    provisional id on both backends; committing both never clobbers either's
    bytes/hash, and each lands on a distinct, non-shared `version_dir`."""
    store = stores[kind]
    run1 = store.create_run(
        experiment="collide.csv", tool_class="qc", provenance=_prov(seed=1)
    )
    run2 = store.create_run(
        experiment="collide.csv", tool_class="qc", provenance=_prov(seed=2)
    )
    assert run1.version_id == run2.version_id  # both saw the same empty state

    (run1.staging_dir / "o.csv").write_bytes(b"first")
    (run2.staging_dir / "o.csv").write_bytes(b"second")

    stored1 = store.commit(run1, {"o": "o.csv"})  # lands first, fully completes
    stored2 = store.commit(run2, {"o": "o.csv"})  # collides, reallocates

    assert stored1.run_ref != stored2.run_ref
    assert stored1.version_dir != stored2.version_dir
    assert [r.run_ref for r in store.list_runs("collide.csv", "qc")] == [
        stored1.run_ref,
        stored2.run_ref,
    ]
    for stored, expected in ((stored1, b"first"), (stored2, b"second")):
        key = stored.output_keys["o"]
        assert stored.version_dir in key  # id/version_dir/key stay in lockstep
        assert stored.output_sha256["o"] == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_concurrent_commits_never_corrupt_each_others_data(
    kind, stores, fake_supabase_storage
):
    """Real thread-pool concurrency — not just sequential interleaving — must
    never let one writer's failure-cleanup delete, or its upload overwrite,
    another writer's already-committed bytes. FastMCP dispatches sync tool
    handlers via a thread pool, so two `commit()` calls for the same
    (experiment, tool_class) can genuinely race within one process; both
    threads start via a barrier so the race is real, not just scheduled
    back-to-back, and correctness must hold regardless of which one wins."""
    store = stores[kind]
    run1 = store.create_run(
        experiment="race.csv", tool_class="qc", provenance=_prov(seed=1)
    )
    run2 = store.create_run(
        experiment="race.csv", tool_class="qc", provenance=_prov(seed=2)
    )
    assert run1.version_id == run2.version_id  # both saw the same empty state

    (run1.staging_dir / "o.csv").write_bytes(b"first")
    (run2.staging_dir / "o.csv").write_bytes(b"second")

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: list[tuple[str, Exception]] = []

    def _run(label, run):
        barrier.wait()
        try:
            results[label] = store.commit(run, {"o": "o.csv"})
        except Exception as exc:  # pragma: no cover - captured for assertion
            errors.append((label, exc))

    t1 = threading.Thread(target=_run, args=("first", run1))
    t2 = threading.Thread(target=_run, args=("second", run2))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not errors, errors
    assert set(results) == {"first", "second"}
    stored_first, stored_second = results["first"], results["second"]

    assert stored_first.run_ref != stored_second.run_ref
    assert stored_first.version_dir != stored_second.version_dir
    for stored, expected in ((stored_first, b"first"), (stored_second, b"second")):
        assert stored.output_sha256["o"] == hashlib.sha256(expected).hexdigest()
    assert {r.run_ref for r in store.list_runs("race.csv", "qc")} == {
        stored_first.run_ref,
        stored_second.run_ref,
    }

    if kind == "supabase":
        # The strongest assertion: neither run's actually-stored bytes were
        # deleted (by the loser's cleanup) or overwritten (by the loser's
        # upload landing under the same deterministic version_dir).
        for stored, expected in ((stored_first, b"first"), (stored_second, b"second")):
            key = stored.output_keys["o"]
            assert fake_supabase_storage.objects[key] == expected


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_v2_backcompat_parity(kind, stores, fake_supabase_storage):
    """#325: a v2-shaped historical entry (no seed/output_sha256/output_keys)
    coexists with a new v3 commit on both backends, and `latest` resolves to
    the new one — issue #325's Scope bullet, previously Supabase-only."""
    store = stores[kind]

    if kind == "fake":
        store.seed_v2_run(
            "v2.csv", "qc", tool="dimred_workflow", outputs={"cleaned": "_cleaned.csv"}
        )
    else:
        v2 = json.loads((_FIXTURES / "manifest_v2.json").read_text())
        adir = AnalysisDir("bloommcp_output", "v2.csv", "qc")
        fake_supabase_storage.objects[f"{adir.path}manifest.json"] = json.dumps(
            v2
        ).encode()

    runs_before = store.list_runs("v2.csv", "qc")
    assert [r.run_ref for r in runs_before] == ["v1"]
    assert runs_before[0].seed is None  # v2 predates the seed field

    run = store.create_run(experiment="v2.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")
    stored = store.commit(run, {"o": "o.csv"})

    assert stored.run_ref == "v2"
    assert stored.seed == 5
    assert [r.run_ref for r in store.list_runs("v2.csv", "qc")] == ["v1", "v2"]
    assert store.get_run("v2.csv", "qc", "latest").run_ref == "v2"

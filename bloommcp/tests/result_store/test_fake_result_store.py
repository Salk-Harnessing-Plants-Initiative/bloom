"""FakeResultStore oracle + edge cases — the write port with no Supabase."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.result_store import (
    CommitFailedError,
    FakeResultStore,
    ManifestReadError,
    RunNotFoundError,
    RunStateError,
)


def _prov(tool: str = "run_qc_workflow", seed: int = 42) -> Provenance:
    return Provenance.stamp(tool=tool, params={"k": 1}, seed=seed)


def _write(staging: Path, name: str, data: bytes) -> None:
    (Path(staging) / name).write_bytes(data)


def test_create_commit_records_versioned_run_with_provenance():
    store = FakeResultStore()
    run = store.create_run(
        experiment="exp.csv", tool_class="qc", provenance=_prov(), user_label="first"
    )
    assert run.version_id == "v1"

    payload = b"a,b\n1,2\n"
    _write(run.staging_dir, "_cleaned.csv", payload)
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})

    assert stored.run_ref == "v1"
    assert stored.tool == "run_qc_workflow"
    assert stored.seed == 42
    assert stored.agent == "bloom_agent"
    # outputs / keys / hashes share one key-set.
    assert (
        set(stored.outputs)
        == set(stored.output_keys)
        == set(stored.output_sha256)
        == {"cleaned"}
    )
    assert stored.output_sha256["cleaned"] == hashlib.sha256(payload).hexdigest()
    assert stored.output_keys["cleaned"].startswith("bloommcp_output/qc_exp/")
    assert stored.output_keys["cleaned"].endswith("/_cleaned.csv")

    assert store.get_run("exp.csv", "qc", "latest").run_ref == "v1"
    assert [r.run_ref for r in store.list_runs("exp.csv", "qc")] == ["v1"]


def test_get_run_resolves_latest_across_commits():
    store = FakeResultStore()
    for i in range(2):
        run = store.create_run(
            experiment="e.csv", tool_class="stats", provenance=_prov(tool="t")
        )
        _write(run.staging_dir, "out.csv", f"v{i}".encode())
        store.commit(run, {"o": "out.csv"})

    assert [r.run_ref for r in store.list_runs("e.csv", "stats")] == ["v1", "v2"]
    assert store.get_run("e.csv", "stats", "latest").run_ref == "v2"
    assert store.get_run("e.csv", "stats", "v1").run_ref == "v1"


def test_unknown_run_raises_not_found():
    store = FakeResultStore()
    with pytest.raises(RunNotFoundError):
        store.get_run("e.csv", "qc", "latest")
    store2 = FakeResultStore()
    run = store2.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")
    store2.commit(run, {"o": "o.csv"})
    with pytest.raises(RunNotFoundError):
        store2.get_run("e.csv", "qc", "v9")


def test_double_commit_rejected():
    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")
    store.commit(run, {"o": "o.csv"})
    with pytest.raises(RunStateError):
        store.commit(run, {"o": "o.csv"})


def test_key_outside_run_prefix_fails_commit_and_cleans_up(monkeypatch):
    """#598: FakeResultStore.commit() gets the same structural key-scoping
    guarantee as SupabaseResultStore (fake/real parity) — a key outside this
    run's own freshly-computed prefix fails the whole commit via the existing
    CommitFailedError path, same as any other commit failure.

    Injection mirrors test_supabase_result_store's approach: monkeypatch the
    module-level build_output_links import (not the prefix-building logic
    itself, which would corrupt both sides of the comparison identically and
    never reproduce a mismatch)."""
    import bloom_mcp.result_store.fake_store as fstore
    from bloom_mcp.result_store._artifacts import (
        build_output_links as real_build_output_links,
    )

    def _wrong_prefix(output_keys, output_sha256, output_size_bytes, url_for, **_):
        return real_build_output_links(
            output_keys,
            output_sha256,
            output_size_bytes,
            url_for,
            expected_prefix="bloommcp_output/qc_someone_else/v1/",
        )

    monkeypatch.setattr(fstore, "build_output_links", _wrong_prefix)

    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")

    with pytest.raises(CommitFailedError) as excinfo:
        store.commit(run, {"o": "o.csv"})

    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "qc_someone_else" in str(excinfo.value.__cause__)
    assert store.list_runs("e.csv", "qc") == []


def test_commit_failure_is_retryable_and_does_not_leak():
    """#325: fake mirror of test_supabase_result_store's retry contract."""
    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")

    store.fail_next_commit("e.csv", "qc", after_outputs=0)
    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    # Failure is recoverable: nothing recorded, staging retained, handle live.
    assert store.list_runs("e.csv", "qc") == []
    assert run.staging_dir.exists()

    # Retry on the same handle succeeds (one-shot injection already cleared).
    stored = store.commit(run, {"o": "o.csv"})
    assert stored.run_ref == "v1"
    assert store.get_run("e.csv", "qc", "latest").run_ref == "v1"
    assert not run.staging_dir.exists()


def test_commit_failure_after_partial_recording_leaves_no_partial_state():
    """#325: fake mirror of test_commit_failure_cleans_up_orphaned_objects_from_
    partial_upload, reinterpreted for an in-memory store as "no partial state
    survives" (there is nothing external to leak an orphan into)."""
    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "a.csv", b"a")
    _write(run.staging_dir, "b.csv", b"b")

    store.fail_next_commit("e.csv", "qc", after_outputs=1)
    with pytest.raises(CommitFailedError):
        store.commit(run, {"a": "a.csv", "b": "b.csv"})

    assert store.list_runs("e.csv", "qc") == []

    stored = store.commit(run, {"a": "a.csv", "b": "b.csv"})
    assert stored.run_ref == "v1"
    assert set(stored.outputs) == {"a", "b"}


def test_interleaved_commits_get_distinct_ids_with_consistent_provenance():
    """#325: fake mirror — a seeded collision reallocates to a free id."""
    store = FakeResultStore()
    run1 = store.create_run(
        experiment="e.csv", tool_class="qc", provenance=_prov(seed=1)
    )
    _write(run1.staging_dir, "o.csv", b"first")

    # Simulate a second writer's commit having already landed at v1.
    store.seed_collision("e.csv", "qc", "v1")

    stored1 = store.commit(run1, {"o": "o.csv"})

    assert stored1.run_ref == "v2"  # reallocated around the seeded v1
    assert store.get_run("e.csv", "qc", "v1").tool == "interloper"
    assert store.get_run("e.csv", "qc", "v2").run_ref == "v2"
    assert [r.run_ref for r in store.list_runs("e.csv", "qc")] == ["v1", "v2"]


def test_retry_exhaustion_before_recording_raises_with_nothing_recorded(monkeypatch):
    """#325: fake mirror of test_retry_exhaustion_before_upload_raises_with_
    no_uploads — every reallocation attempt still collides."""
    import bloom_mcp.result_store.fake_store as _fake_mod

    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")

    store.seed_collision("e.csv", "qc", run.version_id)
    monkeypatch.setattr(_fake_mod, "next_version_id", lambda manifest: run.version_id)

    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    # Only the seeded interloper is recorded — this attempt recorded nothing.
    runs = store.list_runs("e.csv", "qc")
    assert [r.run_ref for r in runs] == [run.version_id]
    assert runs[0].tool == "interloper"


def test_prewrite_collision_cleans_up_and_retry_succeeds():
    """#325: fake mirror of test_prewrite_collision_cleans_up_and_retry_
    succeeds — a collision visible only to the pre-append recheck."""
    store = FakeResultStore()
    run = store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    _write(run.staging_dir, "o.csv", b"x")

    store.seed_collision("e.csv", "qc", run.version_id, visible_at="pre_append")

    with pytest.raises(CommitFailedError):
        store.commit(run, {"o": "o.csv"})

    # The pending collision was never actually recorded (it only existed for
    # the pre-append recheck), so nothing landed from this failed attempt.
    assert store.list_runs("e.csv", "qc") == []

    # Retry finds a genuinely free id and succeeds.
    stored = store.commit(run, {"o": "o.csv"})
    assert stored.run_ref == run.version_id
    assert store.get_run("e.csv", "qc", "latest").run_ref == run.version_id


# ── #596: fail_next_read simulates a manifest-read failure ──────────────────


def test_fail_next_read_raises_once_then_clears():
    """The flat in-memory store has no read to fail organically — this hook
    is its only way to exercise SupabaseResultStore's manifest-read guard
    contract (ManifestReadError) without a live Supabase adapter."""
    store = FakeResultStore()
    store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())

    store.fail_next_read("e.csv", "qc")
    with pytest.raises(ManifestReadError):
        store.list_runs("e.csv", "qc")

    # One-shot: cleared after firing once.
    assert store.list_runs("e.csv", "qc") == []


def test_fail_next_read_is_scoped_to_experiment_and_tool_class():
    store = FakeResultStore()
    store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())
    store.create_run(experiment="e.csv", tool_class="outliers", provenance=_prov())

    store.fail_next_read("e.csv", "qc")

    # A different tool_class for the same experiment is unaffected.
    assert store.list_runs("e.csv", "outliers") == []
    # The armed key still raises.
    with pytest.raises(ManifestReadError):
        store.list_runs("e.csv", "qc")


def test_fail_next_read_fires_on_whichever_of_the_three_methods_is_called_first():
    """Unlike `fail_next_commit` (one call site), `fail_next_read` is shared
    across three methods — the flag must fire on whichever is called first
    for the armed key, not just a fixed one."""
    store = FakeResultStore()
    store.create_run(experiment="e.csv", tool_class="qc", provenance=_prov())

    store.fail_next_read("e.csv", "qc")
    with pytest.raises(ManifestReadError):
        store.list_runs("e.csv", "qc")
    # Cleared by the call above — a subsequent get_run for the same key no
    # longer raises ManifestReadError a second time; it runs the real lookup
    # and (there being no committed run yet) raises RunNotFoundError instead.
    with pytest.raises(RunNotFoundError):
        store.get_run("e.csv", "qc", "latest")

    store.fail_next_read("e.csv", "qc")
    with pytest.raises(ManifestReadError):
        store.get_run("e.csv", "qc", "latest")

    store.fail_next_read("new.csv", "qc")
    with pytest.raises(ManifestReadError):
        store.create_run(experiment="new.csv", tool_class="qc", provenance=_prov())


def test_fail_next_read_is_consumed_exactly_once_under_real_concurrency():
    """`_maybe_fail_read`'s check-then-discard is guarded by its own lock so
    two threads racing the same armed key can't both observe it set before
    either discards it -- real thread concurrency (via a barrier), not just
    scheduled back-to-back, mirroring how
    `test_concurrent_commits_never_corrupt_each_others_data` exercises this
    module's other one-shot hook under genuine concurrency."""
    store = FakeResultStore()
    store.fail_next_read("race.csv", "qc")

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def _call(label):
        barrier.wait()
        try:
            results[label] = store.list_runs("race.csv", "qc")
        except ManifestReadError as exc:
            results[label] = exc

    t1 = threading.Thread(target=_call, args=("first",))
    t2 = threading.Thread(target=_call, args=("second",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert set(results) == {"first", "second"}
    outcomes = list(results.values())
    raised = [o for o in outcomes if isinstance(o, ManifestReadError)]
    succeeded = [o for o in outcomes if not isinstance(o, ManifestReadError)]
    # Exactly one thread consumed the one-shot flag and raised; the other saw
    # it already cleared and got the real (empty) result.
    assert len(raised) == 1
    assert succeeded == [[]]

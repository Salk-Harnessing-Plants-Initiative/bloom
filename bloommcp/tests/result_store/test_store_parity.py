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
    CorruptRunLinksError,
    FakeResultStore,
    ManifestReadError,
    RunNotFoundError,
    RunStateError,
    SupabaseResultStore,
)
from bloom_mcp.manifest import AnalysisDir, ExperimentBlock, Manifest, write_manifest
from bloom_mcp.result_store._artifacts import KeyScopeGuardError
from bloom_mcp.result_store._artifacts import (
    build_output_links as _real_build_output_links,
)

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


def _inject_wrong_expected_prefix(kind, monkeypatch):
    """Force the next commit()'s build_output_links call to check every real
    output key against a deliberately wrong expected_prefix.

    No real call path can produce a mismatched key (expected_prefix and every
    output key both derive from the same key_for(...) closure inside
    commit()) — monkeypatching AnalysisDir.key/the closure itself would
    corrupt both sides identically and never reproduce the mismatch this
    needs (see design.md's Risks section). The seam that works on both
    backends: monkeypatch the module-level build_output_links name each
    adapter module imports, delegating to the real function but substituting
    a wrong expected_prefix — leaving output_keys/upload fully
    self-consistent (real bytes land at the real, correctly-scoped key).
    """
    import bloom_mcp.result_store.fake_store as fstore
    import bloom_mcp.result_store.supabase_store as sstore

    module = sstore if kind == "supabase" else fstore

    def _wrong_prefix(output_keys, output_sha256, output_size_bytes, url_for, **_):
        return _real_build_output_links(
            output_keys,
            output_sha256,
            output_size_bytes,
            url_for,
            expected_prefix="bloommcp_output/qc_someone_else/v1/",
        )

    monkeypatch.setattr(module, "build_output_links", _wrong_prefix)


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_key_outside_run_prefix_fails_commit_and_cleans_up_parity(
    kind, stores, monkeypatch, fake_supabase_storage
):
    """#598: commit()'s key-scoping guard rejects a key outside this run's
    own freshly-computed prefix identically on both backends, via the same
    CommitFailedError fail-closed/cleanup path any other commit failure
    already takes."""
    store = stores[kind]
    _inject_wrong_expected_prefix(kind, monkeypatch)

    run = store.create_run(experiment="scope.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")

    with pytest.raises(CommitFailedError) as excinfo:
        store.commit(run, {"a": "a.csv"})

    # Assert on the chained cause, not just "some exception happened" — before
    # the guard existed, expected_prefix was an unexpected kwarg and the cause
    # was a bare TypeError; only the guard itself raises KeyScopeGuardError
    # naming the mismatched key. Without this, the test would pass identically
    # whether or not the guard is implemented.
    assert isinstance(excinfo.value.__cause__, KeyScopeGuardError)
    assert "qc_someone_else" in str(excinfo.value.__cause__)
    assert store.list_runs("scope.csv", "qc") == []  # latest un-advanced
    if kind == "supabase":
        # The already-uploaded object is cleaned up, same as a signing
        # failure — the strongest assertion only the real backend can make.
        assert not any(k.endswith("a.csv") for k in fake_supabase_storage.objects)


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
def test_output_links_parity(kind, stores):
    """bloom#581: commit() returns one OutputLink per output, keyed identically
    to `outputs`, with the right sha256/size_bytes and a non-empty URL — same
    shape on both backends (a real/served URL vs. the fake's synthesized one is
    the only expected divergence)."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"aaa")
    (run.staging_dir / "b.csv").write_bytes(b"bb")
    stored = store.commit(run, {"a": "a.csv", "b": "b.csv"})

    assert set(stored.output_links) == {"a", "b"}
    urls_seen = set()
    for name, expected_bytes in (("a", b"aaa"), ("b", b"bb")):
        link = stored.output_links[name]
        assert link.key == stored.output_keys[name]
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes == len(expected_bytes)
        assert link.url
        # Bound to the correct key, not just truthy — both backends here
        # synthesize a fake://signed/<key>?... URL (fake_supabase_storage
        # backs the "supabase" kind too), so a bug that wired every output to
        # the same URL (e.g. a closure-over-loop-variable mistake) would slip
        # past a truthy-only check.
        assert link.key in link.url
        urls_seen.add(link.url)
    assert len(urls_seen) == 2  # the two outputs never share a URL


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_output_links_empty_for_get_run_and_list_runs_parity(kind, stores):
    """bloom#581 Decision 1: only commit()'s own return value carries signed
    links — resolving/listing the same run afterward never does."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    store.commit(run, {"a": "a.csv"})

    assert store.get_run("exp.csv", "qc", "latest").output_links == {}
    assert all(r.output_links == {} for r in store.list_runs("exp.csv", "qc"))


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_params_populated_only_by_get_run_not_commit_or_list_runs_parity(kind, stores):
    """bloom#600, reworked per bloom#622 review (see design.md Decision 5):
    params/based_on_version are populated only by get_run (and therefore by
    get_download_links, which calls it internally) -- commit's own return
    value and list_runs both leave them at their StoredRun defaults
    (`{}`/`""`), mirroring #581 Decision 1's output_links-empty pattern.
    This is deliberate: list_runs backs list_existing_analyses, which dumps
    every returned StoredRun verbatim via dataclasses.asdict -- populating
    params there would leak every historical run's raw params (the same
    class of cross-run exposure this rework exists to close) to an
    always-included, no-opt-in discovery tool."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    committed = store.commit(run, {"a": "a.csv"})

    assert committed.params == {}
    assert committed.based_on_version == ""
    assert all(r.params == {} for r in store.list_runs("exp.csv", "qc"))
    assert all(r.based_on_version == "" for r in store.list_runs("exp.csv", "qc"))

    resolved = store.get_run("exp.csv", "qc", "latest")
    assert resolved.params == {"a": 1}
    assert resolved.based_on_version == "raw"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_reruns_signing_for_a_prior_run_parity(kind, stores):
    """bloom#599: unlike get_run/list_runs, get_download_links always
    (re-)populates output_links -- resolving "latest" and an explicit
    run_ref both work, with a live-resolved size_bytes matching the real
    byte count on both backends."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"aaa")
    committed = store.commit(run, {"a": "a.csv"})

    for ref in ("latest", committed.run_ref):
        resolved = store.get_download_links("exp.csv", "qc", ref)
        assert resolved.run_ref == committed.run_ref
        link = resolved.output_links["a"]
        assert link.key == committed.output_keys["a"]
        assert link.sha256 == committed.output_sha256["a"]
        assert link.size_bytes == 3
        assert link.url


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_no_persisted_size_field_parity(kind, stores):
    """bloom#599 Decision 1: size_bytes is resolved live on every call --
    there is no persisted-size fast path to bypass, on either backend."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"aaaaa")
    store.commit(run, {"a": "a.csv"})

    resolved = store.get_download_links("exp.csv", "qc", "latest")
    assert resolved.output_links["a"].size_bytes == 5


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_returns_only_the_resolved_runs_own_params_parity(
    kind, stores
):
    """bloom#622 review fix: the resolved run's params/based_on_version must
    never leak another run's data. This is the exact property the prior
    manifest_url design (a signed link to the shared, all-versions
    manifest.json) violated -- any known run_ref unlocked every run's
    params for that (experiment, tool_class) pair. Two runs with distinct
    params on the same pair: each must resolve only its own, for both
    "latest" and an explicit run_ref."""
    store = stores[kind]
    run1 = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="t", params={"which": "first"}, seed=1),
    )
    (run1.staging_dir / "a.csv").write_bytes(b"a")
    first = store.commit(run1, {"a": "a.csv"})

    run2 = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=Provenance.stamp(tool="t", params={"which": "second"}, seed=2),
    )
    (run2.staging_dir / "a.csv").write_bytes(b"aa")
    second = store.commit(run2, {"a": "a.csv"})
    assert first.run_ref != second.run_ref

    resolved_first = store.get_download_links("exp.csv", "qc", first.run_ref)
    resolved_second = store.get_download_links("exp.csv", "qc", second.run_ref)
    resolved_latest = store.get_download_links("exp.csv", "qc", "latest")

    assert resolved_first.params == {"which": "first"}
    assert resolved_second.params == {"which": "second"}
    assert resolved_latest.params == resolved_second.params
    assert resolved_latest.run_ref == second.run_ref


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_retired_tool_class_still_resolves_parity(kind, stores):
    """A retired-but-historical tool_class (still queryable per
    list_existing_analyses.TOOL_CLASSES) resolves and re-signs normally --
    ResultStore itself has no allowlist of tool_class values."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="stats", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"a")
    store.commit(run, {"a": "a.csv"})

    resolved = store.get_download_links("exp.csv", "stats", "latest")
    assert resolved.output_links["a"].url
    assert resolved.params == {"a": 1}


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_legacy_run_with_no_keys_yields_no_links_parity(
    kind, stores
):
    """A legacy v2-shaped run (no output_keys ever recorded) returns
    output_links == {} rather than raising -- nothing to sign or size."""
    store = stores[kind]
    if kind == "fake":
        store.seed_v2_run(
            "exp.csv", "qc", tool="qc_clean", outputs={"cleaned": "_cleaned.csv"}
        )
    else:
        # A real v2 manifest entry, mirroring the fixture-based v2-backcompat
        # test elsewhere in this file — no output_keys/output_sha256 at all.
        prov = _prov().model_copy(
            update={
                "outputs": {"cleaned": "_cleaned.csv"},
                "output_keys": {},
                "output_sha256": {},
                "version_dir": "v1",
            }
        )
        entry = prov.to_version_entry(version_id="v1")
        adir = AnalysisDir("bloommcp_output", "exp.csv", "qc")
        write_manifest(
            adir.path,
            Manifest(
                experiment=ExperimentBlock(
                    filename="exp.csv", source_path="", input_sha256=""
                ),
                versions=[entry],
                latest="v1",
            ),
        )

    resolved = store.get_download_links("exp.csv", "qc", "latest")
    assert resolved.output_links == {}
    # bloom#600, reworked per bloom#622 review: unlike output_links,
    # params/based_on_version are never gated on output_keys being
    # non-empty -- they were part of the manifest schema since v2, present
    # regardless of whether per-artifact keys were ever recorded for this
    # run. Content isn't asserted for exact equality here (the fake's
    # seed_v2_run stub and the supabase branch's real Provenance-derived
    # entry aren't constructed to match each other's specific values) --
    # only that both backends return the correctly-typed fields, not a
    # missing/None value, for a legacy run.
    assert isinstance(resolved.params, dict)
    assert isinstance(resolved.based_on_version, str)


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_empty_experiment_string_parity(kind, stores):
    """An empty experiment string does not crash the lookup -- it resolves
    through the same not-found path an unknown experiment would."""
    store = stores[kind]
    with pytest.raises(RunNotFoundError):
        store.get_download_links("", "qc", "latest")


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_unknown_run_raises_not_found_parity(kind, stores):
    store = stores[kind]
    with pytest.raises(RunNotFoundError):
        store.get_download_links("never-committed.csv", "qc", "latest")


def _seed_mismatched_key(kind, store, *, experiment, tool_class):
    """Seed a historical run whose persisted output_keys fall outside its own
    expected prefix -- the only way to exercise the CorruptRunLinksError
    guard, since no real commit() call path can ever produce this (every
    real key is derived from this same run's own key_for closure)."""
    bad_key = f"bloommcp_output/{tool_class}_someone_elses_experiment/v1/_cleaned.csv"
    if kind == "fake":
        store.seed_run_with_keys(
            experiment, tool_class, output_keys={"cleaned": bad_key}
        )
        return
    prov = _prov().model_copy(
        update={
            "outputs": {"cleaned": "_cleaned.csv"},
            "output_keys": {"cleaned": bad_key},
            "output_sha256": {"cleaned": "0" * 64},
            "version_dir": "v1_2026-01-01",
        }
    )
    entry = prov.to_version_entry(version_id="v1")
    adir = AnalysisDir("bloommcp_output", experiment, tool_class)
    write_manifest(
        adir.path,
        Manifest(
            experiment=ExperimentBlock(
                filename=experiment, source_path="", input_sha256=""
            ),
            versions=[entry],
            latest="v1",
        ),
    )


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_key_outside_scope_raises_parity(kind, stores):
    store = stores[kind]
    _seed_mismatched_key(kind, store, experiment="exp.csv", tool_class="qc")

    with pytest.raises(CorruptRunLinksError):
        store.get_download_links("exp.csv", "qc", "latest")


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_get_download_links_multi_output_partial_failure_aborts_whole_call_parity(
    kind, stores, monkeypatch
):
    """A second output's lookup failure aborts the whole call -- the first,
    already-succeeded output's link must not leak out as a partial result."""
    store = stores[kind]
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"aaa")
    (run.staging_dir / "b.csv").write_bytes(b"bb")
    committed = store.commit(run, {"a": "a.csv", "b": "b.csv"})

    if kind == "fake":
        # Force specifically the *second* output's ("b") size lookup to
        # raise, not an arbitrary one -- next(iter(...)) previously picked
        # whichever key happened to be first in dict order (always "a" here,
        # since dicts preserve insertion order), which never actually
        # exercised "the first output already succeeded, then the second
        # failed" -- it just made the *first* lookup fail immediately
        # (review finding, PR #611).
        key_to_break = committed.output_keys["b"]
        for key_tuple, sizes in store._output_sizes.items():
            if key_tuple[:2] == ("exp.csv", "qc") and key_to_break in sizes:
                del sizes[key_to_break]
    else:
        import bloom_mcp.supabase_client as sc

        real_get_object_size = sc.get_object_size
        calls = {"n": 0}

        def _flaky(key):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated failure (get_object_size)")
            return real_get_object_size(key)

        monkeypatch.setattr(sc, "get_object_size", _flaky)

    with pytest.raises(Exception):
        store.get_download_links("exp.csv", "qc", "latest")


def test_fake_get_download_links_never_calls_storage_backend(monkeypatch):
    """design.md Decision 6 (outputs): FakeResultStore.get_download_links
    never calls anything on StorageBackend for any run it recorded itself --
    it has its own private size bookkeeping for outputs, and its
    params/based_on_version come from an in-memory side table (Decision 5,
    bloom#622), not a live call. Specific to FakeResultStore (the real
    adapter's equivalent guarantee is instead "makes exactly one live call
    per output," covered by the parity tests above)."""
    store = FakeResultStore()
    run = store.create_run(experiment="exp.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "a.csv").write_bytes(b"aaa")
    store.commit(run, {"a": "a.csv"})

    import bloom_mcp.storage_backend as sb_module

    def _boom():
        raise AssertionError(
            "FakeResultStore.get_download_links must never call active_backend()"
        )

    monkeypatch.setattr(sb_module, "active_backend", _boom)

    resolved = store.get_download_links("exp.csv", "qc", "latest")
    assert resolved.output_links["a"].size_bytes == 3
    assert resolved.params == {"a": 1}


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
    assert runs_before[0].output_links == {}  # bloom#581: legacy entry, never signed

    run = store.create_run(experiment="v2.csv", tool_class="qc", provenance=_prov())
    (run.staging_dir / "o.csv").write_bytes(b"x")
    stored = store.commit(run, {"o": "o.csv"})

    assert stored.run_ref == "v2"
    assert stored.seed == 5
    assert [r.run_ref for r in store.list_runs("v2.csv", "qc")] == ["v1", "v2"]
    assert store.get_run("v2.csv", "qc", "latest").run_ref == "v2"


@pytest.mark.parametrize("kind", ["fake", "supabase"])
def test_create_run_with_source_records_identity_parity(kind, stores):
    """bloom#551: create_run(..., source=SourceInfo(...)) merges source_id/
    source_name into the committed VersionEntry on both backends; omitting it
    leaves both None rather than a fabricated value."""
    from bloom_mcp.data_access import SourceInfo

    store = stores[kind]
    run = store.create_run(
        experiment="exp.csv",
        tool_class="qc",
        provenance=_prov(),
        source=SourceInfo(
            source_id=7, source_name="reprocess-2026-07", pipeline_run_id=None
        ),
    )
    (run.staging_dir / "_cleaned.csv").write_bytes(b"data")
    stored = store.commit(run, {"cleaned": "_cleaned.csv"})
    assert stored.source_id == 7
    assert stored.source_name == "reprocess-2026-07"

    run_no_source = store.create_run(
        experiment="exp2.csv", tool_class="qc", provenance=_prov()
    )
    (run_no_source.staging_dir / "_cleaned.csv").write_bytes(b"data")
    stored_no_source = store.commit(run_no_source, {"cleaned": "_cleaned.csv"})
    assert stored_no_source.source_id is None
    assert stored_no_source.source_name is None


def _inject_read_failure(kind, store, monkeypatch, *, experiment, tool_class):
    """Force the next manifest read for (experiment, tool_class) to fail —
    one shared scenario body, two structurally different injection
    techniques per backend (mirrors `_inject_commit_failure` above).

    Not fully equivalent between backends, unlike `_inject_commit_failure`:
    the fake's `fail_next_read` is one-shot and scoped to this one key, while
    the supabase side's monkeypatch is persistent and global for the rest of
    the test. Harmless for `test_manifest_read_failure_parity` below (one call
    per parametrized case), but this helper does not itself prove one-shot
    semantics on the supabase side — a future test asserting that would need
    its own, backend-specific injection.
    """
    if kind == "fake":
        store.fail_next_read(experiment, tool_class)
        return

    import bloom_mcp.manifest.analysis_dir as _adir_mod

    def _boom(prefix):
        raise RuntimeError("simulated failure (manifest read)")

    monkeypatch.setattr(_adir_mod, "read_manifest", _boom)


_READ_CALL_SITES = {
    "create_run": lambda store, experiment, tool_class: store.create_run(
        experiment=experiment, tool_class=tool_class, provenance=_prov()
    ),
    "list_runs": lambda store, experiment, tool_class: store.list_runs(
        experiment, tool_class
    ),
    "get_run": lambda store, experiment, tool_class: store.get_run(
        experiment, tool_class, "latest"
    ),
    "get_download_links": lambda store, experiment, tool_class: (
        store.get_download_links(experiment, tool_class, "latest")
    ),
}


@pytest.mark.parametrize("kind", ["fake", "supabase"])
@pytest.mark.parametrize("call_site", sorted(_READ_CALL_SITES))
def test_manifest_read_failure_parity(kind, call_site, stores, monkeypatch):
    """#596: a manifest-read failure at create_run/list_runs/get_run raises
    ManifestReadError on both backends. FakeResultStore has no real read to
    fail organically — `fail_next_read` is its only way to exercise the same
    contract SupabaseResultStore's guard provides for a real storage/network
    failure. `get_download_links` (bloom#599) resolves through the same
    `get_run` lookup, so it inherits this guarantee rather than needing a
    separate one."""
    store = stores[kind]
    _inject_read_failure(
        kind, store, monkeypatch, experiment="read-fail.csv", tool_class="qc"
    )
    with pytest.raises(ManifestReadError):
        _READ_CALL_SITES[call_site](store, "read-fail.csv", "qc")

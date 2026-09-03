"""bloom#585 — `trim_is_stale` field on `list_existing_analyses`.

`trim_staleness` reads real manifests through `AnalysisDir`/`storage_backend`,
a seam below `FakeReader`/`FakeResultStore` (see `injected_ports` in
`test_qc_tools_discovery.py`) — so these tests combine `injected_ports` (with
an empty `FakeReader`, which never rejects an experiment name — the "known
experiment" guard only fires when its `known` set is non-empty) with the
`local_manifest_backend` fixture for real, on-disk manifests.
"""

from __future__ import annotations

import json

import pytest

from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.sections.core import list_existing_analyses as list_existing_analyses_mod
from bloom_mcp.tools import _ports
from manifest_fixtures import write_cleaned_manifest

_EXPERIMENT = "exp.csv"


@pytest.fixture
def injected_ports():
    reader = FakeReader()
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    list_existing_analyses_mod._RESPONSE_CACHE.clear()
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
        list_existing_analyses_mod._RESPONSE_CACHE.clear()


def test_untrimmed_experiment_has_no_new_field(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert "errors" not in response


def test_current_trim_reports_not_stale(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is False
    # Discriminator fields: a caller can tell "current" from "no qc baseline at
    # all" without the server-side log line only #420/#585's own log call sees.
    assert response["trim_based_on_qc_version"] == "v1_cleaned"
    assert response["trim_current_qc_version"] == "v1_cleaned"


def test_stale_trim_reports_stale(injected_ports, local_manifest_backend):
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v2", "2026-07-06T00:01:00Z", b"a,b\n3,4\n"
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is True
    assert response["trim_based_on_qc_version"] == "v1_cleaned"
    assert response["trim_current_qc_version"] == "v2_cleaned"


def test_no_qc_baseline_reports_stale_with_null_current_version(
    injected_ports, local_manifest_backend
):
    """The no-`qc`-baseline-at-all corner (design.md Decision 1): `trim_is_stale`
    is `True`, but `trim_current_qc_version` is `None` — a caller can tell this
    apart from ordinary "a qc_clean ran since" staleness without needing the
    server-side log line."""
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:00Z",
        b"trim\n1\n",
    )

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is True
    assert response["trim_current_qc_version"] is None


def test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together(
    injected_ports, local_manifest_backend, monkeypatch
):
    """A `trim_staleness` result and an unrelated tool-class `list_runs` failure
    happening in the same call must both land in the response — neither should
    silently drop the other (bloom#585 review: the two failure/success paths
    populate `errors`/`trim_is_stale` independently, but only a co-occurrence
    test proves neither write clobbers the other)."""
    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:00:01Z",
        b"trim\n1\n",
    )
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["trim_is_stale"] is False
    assert any(e.startswith("qc_clean: ") for e in response["errors"])
    assert len(response["errors"]) == len(list_existing_analyses_mod.TOOL_CLASSES)


def test_tool_class_error_entry_is_redacted(injected_ports, monkeypatch):
    """bloom#664 item 1: the per-tool_class loop must scrub `list_runs` failures
    with `safe_error_text`, mirroring the `trim_staleness` sibling branch below
    it in the same function and `get_download_links.py`'s equivalent handling —
    not rely on `_guarded_manifest_read`'s current callers pre-redacting for it
    implicitly (#660 design.md Decision 3)."""
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("apikey=sk-secret123 leaked from store")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert response["errors"]
    for entry in response["errors"]:
        assert "sk-secret123" not in entry
    assert any("apikey=<redacted>" in e for e in response["errors"])


def test_tool_class_error_entry_uses_public_tool_name(injected_ports, monkeypatch):
    """bloom#664 item 3: the aggregated error entry must be labeled with the
    public tool name an agent actually invoked (e.g. `descriptive_stats`), not
    the internal `tool_class` string (`stats`) — with an unmapped legacy
    `tool_class` (`dimred`) falling back to itself rather than being dropped."""
    _reader, store = injected_ports

    def _boom(_experiment, _tool_class):
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert any(e.startswith("descriptive_stats: ") for e in response["errors"])
    assert not any(e.startswith("stats: ") for e in response["errors"])
    # Every legacy/retired tool_class falls back to itself, not just "dimred"
    # (PR #671 review suggestion).
    assert any(e.startswith("dimred: ") for e in response["errors"])
    assert any(e.startswith("outlier: ") for e in response["errors"])
    assert any(e.startswith("viz: ") for e in response["errors"])


def test_redaction_and_naming_compose_on_a_single_error_entry(
    injected_ports, monkeypatch
):
    """PR #671 review suggestion: a single failing tool_class's error entry
    must be BOTH redacted AND publicly named at once — not just each in
    isolation (the two prior tests each monkeypatch a different-shaped
    failure; this proves the two behaviors compose on the same entry)."""
    _reader, store = injected_ports

    def _boom(_experiment, tool_class):
        if tool_class == "stats":
            raise RuntimeError("apikey=sk-secret123 leaked from store")
        raise RuntimeError("boom")

    monkeypatch.setattr(store, "list_runs", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    stats_entry = next(
        e for e in response["errors"] if e.startswith("descriptive_stats: ")
    )
    assert "sk-secret123" not in stats_entry
    assert "apikey=<redacted>" in stats_entry


def test_every_non_legacy_tool_class_has_a_public_name_mapping():
    """PR #671 review: guards against a future TOOL_CLASSES addition (e.g.
    #669/#673's `pca`/`umap`/`qc_inspect`) silently reintroducing the
    tool_class leak by landing without an accompanying
    `_TOOL_CLASS_TO_PUBLIC_NAME` entry — this fails loudly instead of
    regressing silently to the raw tool_class string for the new entry."""
    legacy_unmapped = {"dimred", "outlier", "viz"}
    for tool_class in list_existing_analyses_mod.TOOL_CLASSES:
        if tool_class in legacy_unmapped:
            continue
        assert tool_class in list_existing_analyses_mod._TOOL_CLASS_TO_PUBLIC_NAME, (
            f"{tool_class!r} is in TOOL_CLASSES but has no "
            "_TOOL_CLASS_TO_PUBLIC_NAME mapping — a list_runs failure for it "
            "will leak the raw tool_class string instead of the public tool name"
        )


def test_trim_staleness_failure_is_reported_not_raised(injected_ports, monkeypatch):
    """A `trim_staleness` failure must not raise, must omit `trim_is_stale`, and
    must still return the tool's other output (`analyses`)."""

    def _boom(_stem):
        raise RuntimeError("manifest schema error for 'exp': boom")

    monkeypatch.setattr(list_existing_analyses_mod, "trim_staleness", _boom)

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert any(e.startswith("trim_staleness: ") for e in response["errors"])
    assert "analyses" in response


def test_pca_umap_qc_inspect_registered_in_discovery_and_canonical_registries():
    """bloom#669: `TOOL_CLASSES`/`CANONICAL_TOOL_CLASSES` never included `"pca"`, `"umap"`,
    or `"qc_inspect"` — the tool_class values `pca_analysis`/`umap_analysis`/`qc_inspect`
    actually persist their runs under — so the aggregation loop structurally never called
    `store.list_runs(experiment, ...)` for any of the 3. Mirrors
    test_remove_outliers_tool.py::test_outliers_class_registered_in_discovery_and_canonical_registries's
    existing pattern for `"outliers"`."""
    from bloom_mcp.manifest import CANONICAL_TOOL_CLASSES

    for tool_class in ("pca", "umap", "qc_inspect"):
        assert tool_class in list_existing_analyses_mod.TOOL_CLASSES
        assert tool_class in CANONICAL_TOOL_CLASSES


def test_tool_classes_is_a_subset_of_canonical_tool_classes():
    """`manifest.CANONICAL_TOOL_CLASSES`'s own comment states it SHALL remain a superset of
    `list_existing_analyses.TOOL_CLASSES`. Enforced generically here (not just spot-checked
    for `pca`/`umap`/`qc_inspect`, bloom#673 review) so a future entry added to one tuple but
    not the other fails this test regardless of which literal it is."""
    from bloom_mcp.manifest import CANONICAL_TOOL_CLASSES

    assert set(list_existing_analyses_mod.TOOL_CLASSES) <= set(CANONICAL_TOOL_CLASSES)


def test_pca_umap_qc_inspect_list_runs_failure_is_individually_reported(
    injected_ports,
):
    """bloom#673 review: `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together`
    monkeypatches `list_runs` to raise for *every* tool_class, so its
    `len(errors) == len(TOOL_CLASSES)` assertion holds regardless of whether `pca`/`umap`/
    `qc_inspect` are even in that tuple — it's a tautology w.r.t. this bug, not evidence the
    3 new classes are actually iterated. This test isolates the failure to *only* those 3,
    proving the aggregation loop calls `store.list_runs` for each of them specifically (a
    tool_class the loop never visited would produce no corresponding error entry at all).
    """
    _reader, store = injected_ports
    targeted = {"pca", "umap", "qc_inspect"}

    def _boom_for_targeted_classes_only(_experiment, tool_class):
        if tool_class in targeted:
            raise RuntimeError(f"store unavailable for {tool_class}")
        return []

    store.list_runs = _boom_for_targeted_classes_only

    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    # This `injected_ports`-only fixture (no `local_manifest_backend`) has no storage
    # backend configured, so `trim_staleness` fails independently of this test's own
    # monkeypatch — filter its unrelated entry out before asserting on the tool_class errors
    # this test actually targets.
    tool_class_errors = [
        e for e in response["errors"] if not e.startswith("trim_staleness: ")
    ]
    # Public tool names (test_every_non_legacy_tool_class_has_a_public_name_mapping
    # requires all 3 to be mapped), not the raw tool_class strings, per
    # test_tool_class_error_entry_uses_public_tool_name's established contract.
    for tool_class in targeted:
        public_name = list_existing_analyses_mod._TOOL_CLASS_TO_PUBLIC_NAME[tool_class]
        assert any(e.startswith(f"{public_name}: ") for e in tool_class_errors)
    assert len(tool_class_errors) == len(targeted)


def test_no_storage_backend_configured_is_reported_not_raised(injected_ports):
    """This repo's actual test-suite default (`conftest.py` scrubs
    `SUPABASE_URL`/`BLOOM_AGENT_KEY`, and no test here sets
    `BLOOM_STORAGE_BACKEND`) must not raise out of `list_existing_analyses` —
    converts what would otherwise be an untested, accidental-pass corner into a
    verified contract."""
    response = json.loads(
        list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT)
    )

    assert "trim_is_stale" not in response
    assert any(e.startswith("trim_staleness: ") for e in response["errors"])


def test_foreign_tool_class_catalog_does_not_hide_healthy_classes(
    injected_ports, local_manifest_backend, monkeypatch
):
    """#573 characterization pin: the per-tool-class error isolation
    (`except Exception → errors.append → continue`) already contains a foreign
    catalog — one foreignized class contributes an error entry naming both
    backends while the experiment's healthy classes still list. Pinned so this
    error type can never abort the whole listing. (`trim_staleness` reads the
    same foreign manifest and contributes its own advisory error entry — also
    isolated, also expected.)"""
    reader, _fake_store = injected_ports
    monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", raising=False)
    # The fake store has no manifest concept — list over the real (local
    # backend) manifests instead, keeping the fixture's cache/restore behavior.
    _ports.configure(reader=reader, store=SupabaseResultStore())
    list_existing_analyses_mod._RESPONSE_CACHE.clear()

    write_cleaned_manifest(
        local_manifest_backend, "exp", "qc", "v1", "2026-07-06T00:00:00Z", b"a,b\n1,2\n"
    )
    write_cleaned_manifest(
        local_manifest_backend,
        "exp",
        "outliers",
        "v1",
        "2026-07-06T00:01:00Z",
        b"a,b\n1,2\n",
    )
    manifest_path = (
        local_manifest_backend / "root" / "bloommcp_output" / "outliers_exp"
        / "manifest.json"
    )
    raw = json.loads(manifest_path.read_text())
    assert raw["storage_backend"] == "local"
    raw["storage_backend"] = "supabase"
    manifest_path.write_text(json.dumps(raw))

    response = json.loads(list_existing_analyses_mod.list_existing_analyses(_EXPERIMENT))

    assert "qc" in response["analyses"], "healthy class must still list"
    assert "outliers" not in response["analyses"]
    errors = response.get("errors", [])
    assert any("supabase" in e and "local" in e for e in errors), errors

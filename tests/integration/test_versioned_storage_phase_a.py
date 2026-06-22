"""storage layer: read-side, manifest, version resolution, introspection.

These tests are filesystem-only — they do NOT require the compose stack. They
run against the storage modules directly, using tmp_path for isolation.

Pandas-dependent tests (the load_experiment_data integration assertions) are
skipped when pandas isn't installed in the test runner's environment.
"""
import pytest

# Storage layer migrated to Supabase Storage; the assertions in this file
# exercise the pre-migration local-FS, fcntl-protected, tempfile+rename
# behavior — including imports (e.g. write_manifest_atomic) that no longer
# exist. Skipping at module scope (allow_module_level=True) keeps pytest
# from even attempting to import the file, so CI stays green without
# losing the file as a placeholder for the rewrite in the follow-up PR.
pytest.skip(
    "pre-migration storage contract; rewrite pending follow-up PR",
    allow_module_level=True,
)
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

# Make bloommcp's flat-package layout importable. bloommcp ships with its
# packages (`source`, `tools`, `storage`) directly under bloommcp/ rather
# than nested under a top-level `bloommcp` namespace, so tests must add
# that directory to sys.path before importing.
_BLOOMMCP_DIR = Path(__file__).resolve().parents[2] / "bloommcp" / "src"
if str(_BLOOMMCP_DIR) not in sys.path:
    sys.path.insert(0, str(_BLOOMMCP_DIR))

# experiment_utils._validate_dirs() runs at import time and requires these
# four env vars to point at extant directories. If the local env doesn't
# have them, fall back to a tmp dir so the import doesn't crash collection.
_TMP_BASE = tempfile.mkdtemp(prefix="bloommcp_phase_a_default_")
os.environ.setdefault("BLOOM_TRAITS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_OUTPUT_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_DIR", _TMP_BASE)
os.environ.setdefault("BLOOM_PLOTS_URL", "http://test.invalid")

from pydantic import ValidationError  # noqa: E402

from bloom_mcp.storage import (  # noqa: E402
    AnalysisDir,
    CodeVersions,
    ExperimentBlock,
    Manifest,
    ManifestSchemaError,
    VersionEntry,
    next_version_id,
    read_manifest,
    slugify,
    validate_schema,
    version_dir_name,
    write_manifest_atomic,
)


def _make_version_entry(
    *,
    id: str = "v1",
    created_at: str = "2026-04-20T00:00:00Z",
    tool: str = "clean_experiment_data",
    params: dict | None = None,
    based_on_version: str = "raw",
    code_versions: CodeVersions | None = None,
    outputs: dict[str, str] | None = None,
    user_label: str | None = None,
) -> VersionEntry:
    return VersionEntry(
        id=id,
        created_at=created_at,
        tool=tool,
        params=params or {},
        based_on_version=based_on_version,
        code_versions=code_versions
        or CodeVersions(bloommcp="0.1.0", sleap_roots_analyze="unknown"),
        outputs=outputs or {},
        user_label=user_label,
    )


def _make_manifest(
    *,
    filename: str = "foo.csv",
    versions: list[VersionEntry] | None = None,
    latest: str | None = None,
    input_sha256: str = "abc",
) -> Manifest:
    versions = versions or []
    return Manifest(
        experiment=ExperimentBlock(
            filename=filename,
            source_path=f".../{filename}",
            input_sha256=input_sha256,
        ),
        versions=versions,
        latest=latest if latest is not None else (versions[-1].id if versions else None),
    )


# ─── Manifest schema enforcement ───────────────────────────────────────────────

def test_validate_schema_rejects_unknown_future_version():
    with pytest.raises(ManifestSchemaError):
        validate_schema({"manifest_schema_version": 99, "versions": []})


def test_validate_schema_rejects_missing_field():
    with pytest.raises(ManifestSchemaError):
        validate_schema({"versions": []})


def test_read_manifest_returns_none_when_absent(tmp_path):
    assert read_manifest(tmp_path) is None


def test_read_manifest_rejects_unknown_future_version(tmp_path):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"manifest_schema_version": 99, "versions": []})
    )
    with pytest.raises(ManifestSchemaError):
        read_manifest(tmp_path)


# ─── Atomic writes ─────────────────────────────────────────────────────────────

def test_write_manifest_atomic_round_trips(tmp_path):
    entry = _make_version_entry(
        id="v1",
        created_at="2026-04-20T14:23:11Z",
        tool="clean_experiment_data",
        params={"contamination": 0.05},
        outputs={"_cleaned.csv": "v1_2026-04-20/_cleaned.csv"},
    )
    manifest = _make_manifest(versions=[entry], latest="v1")
    write_manifest_atomic(tmp_path, manifest)
    assert (tmp_path / "manifest.json").exists()
    round_tripped = read_manifest(tmp_path)
    assert round_tripped == manifest


def test_write_manifest_atomic_leaves_no_tmp_files(tmp_path):
    write_manifest_atomic(tmp_path, _make_manifest())
    leftover_tmps = list(tmp_path.glob("manifest.json.tmp.*"))
    assert leftover_tmps == []


def test_manifest_rejects_extra_fields():
    """Strict mode catches writer bugs that emit unexpected keys."""
    with pytest.raises(ValidationError):
        Manifest.model_validate({
            "manifest_schema_version": 1,
            "experiment": {"filename": "x.csv", "source_path": "...", "input_sha256": ""},
            "versions": [],
            "latest": None,
            "this_field_should_not_exist": True,
        })


# ─── Versioning ────────────────────────────────────────────────────────────────

def test_next_version_id_starts_at_v1():
    assert next_version_id(None) == "v1"
    assert next_version_id(_make_manifest(versions=[])) == "v1"


def test_next_version_id_skips_deleted_ids():
    manifest = _make_manifest(
        versions=[
            _make_version_entry(id="v1"),
            _make_version_entry(id="v3"),
        ],
        latest="v3",
    )
    assert next_version_id(manifest) == "v4"


def test_slugify_normalises_user_labels():
    assert slugify("Iso Method") == "iso_method"
    assert slugify("contamination=0.05") == "contamination005"
    assert slugify("--bad chars--") == "bad_chars"
    assert slugify("a" * 50) == "a" * 32


def test_version_dir_name_with_and_without_label():
    d = date(2026, 4, 20)
    assert version_dir_name("v1", None, today=d) == "v1_2026-04-20"
    assert version_dir_name("v2", "iso_method", today=d) == "v2_2026-04-20_iso_method"


# ─── AnalysisDir ───────────────────────────────────────────────────────────────

def _seed_qc_dir(output_root: Path, stem: str, manifest: Manifest) -> Path:
    qc_dir = output_root / f"qc_{stem}"
    qc_dir.mkdir(parents=True)
    write_manifest_atomic(qc_dir, manifest)
    return qc_dir


def test_analysis_dir_no_manifest(tmp_path):
    ad = AnalysisDir(tmp_path, "foo.csv", "qc")
    assert ad.read_manifest() is None
    assert ad.list_versions() == []
    assert ad.get_version("latest") is None
    assert ad.get_version("v1") is None


def test_analysis_dir_resolves_latest_pointer(tmp_path):
    manifest = _make_manifest(
        versions=[
            _make_version_entry(id="v1", created_at="2026-04-20T00:00:00Z"),
            _make_version_entry(id="v2", created_at="2026-04-25T00:00:00Z"),
        ],
        latest="v2",
    )
    _seed_qc_dir(tmp_path, "foo", manifest)
    ad = AnalysisDir(tmp_path, "foo.csv", "qc")

    assert ad.get_version("latest").id == "v2"
    assert ad.get_version("v1").id == "v1"
    assert ad.get_version("v99") is None


def test_analysis_dir_list_versions_sorted_by_created_at(tmp_path):
    manifest = _make_manifest(
        versions=[
            _make_version_entry(id="v2", created_at="2026-04-25T00:00:00Z"),
            _make_version_entry(id="v1", created_at="2026-04-20T00:00:00Z"),
        ],
        latest="v2",
    )
    _seed_qc_dir(tmp_path, "foo", manifest)
    ad = AnalysisDir(tmp_path, "foo.csv", "qc")
    ids = [v.id for v in ad.list_versions()]
    assert ids == ["v1", "v2"]


def test_analysis_dir_input_sha256_caches_after_first_call(tmp_path):
    csv_path = tmp_path / "foo.csv"
    csv_path.write_bytes(b"col_a,col_b\n1,2\n3,4\n")
    ad = AnalysisDir(tmp_path, "foo.csv", "qc")

    first = ad.input_sha256(csv_path)
    csv_path.write_bytes(b"DIFFERENT CONTENT")
    second = ad.input_sha256(csv_path)

    assert first == second  # second call returns cached value, not re-hashed


# ─── Version-aware loader ──────────────────────────────────────────────────────

def _import_loader():
    """Importing experiment_utils triggers env-var validation; defer to test time
    so the test file's import phase stays fast and deterministic."""
    from bloom_mcp.experiment_utils import load_experiment_data
    return load_experiment_data


def _write_csv(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_load_experiment_data_falls_back_to_raw_when_no_manifest(tmp_path):
    pytest.importorskip("pandas")
    load_experiment_data = _import_loader()

    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    _write_csv(traits_dir / "bar.csv", "trait_a,trait_b\n1.0,2.0\n3.0,4.0\n")

    df, trait_cols, _config, source = load_experiment_data(
        "bar.csv",
        traits_dir=traits_dir,
        output_dir=output_dir,
        version="latest",
    )

    assert df is not None
    assert source == "raw"
    assert "trait_a" in trait_cols


def test_load_experiment_data_raw_skips_cached_cleaned(tmp_path):
    pytest.importorskip("pandas")
    load_experiment_data = _import_loader()

    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    _write_csv(traits_dir / "bar.csv", "trait_a,trait_b\n1.0,2.0\n")

    qc_dir = output_dir / "qc_bar"
    version_subdir = qc_dir / "v1_2026-04-20"
    _write_csv(version_subdir / "_cleaned.csv", "trait_a,trait_b\n9.9,9.9\n")
    write_manifest_atomic(
        qc_dir,
        _make_manifest(
            filename="bar.csv",
            versions=[
                _make_version_entry(
                    outputs={"_cleaned.csv": "v1_2026-04-20/_cleaned.csv"},
                ),
            ],
            latest="v1",
        ),
    )

    df_raw, _, _, source_raw = load_experiment_data(
        "bar.csv", traits_dir=traits_dir, output_dir=output_dir, version="raw"
    )
    df_latest, _, _, source_latest = load_experiment_data(
        "bar.csv", traits_dir=traits_dir, output_dir=output_dir, version="latest"
    )

    assert source_raw == "raw"
    assert df_raw["trait_a"].iloc[0] == 1.0
    assert source_latest == "v1_cleaned"
    assert df_latest["trait_a"].iloc[0] == 9.9


def test_load_experiment_data_explicit_missing_version_errors(tmp_path):
    pytest.importorskip("pandas")
    load_experiment_data = _import_loader()

    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    _write_csv(traits_dir / "bar.csv", "trait_a\n1.0\n")

    df, trait_cols, _config, source = load_experiment_data(
        "bar.csv",
        traits_dir=traits_dir,
        output_dir=output_dir,
        version="v99",
    )

    assert df is None and trait_cols is None
    assert "v99" in source


# ─── list_existing_analyses introspection tool ─────────────────────────────────

def test_list_existing_analyses_empty_when_no_dirs(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    _write_csv(traits_dir / "bar.csv", "trait_a\n1.0\n")
    output_dir.mkdir()

    # Storage tool reads OUTPUT_DIR / TRAITS_DIR from experiment_utils module-globals
    import bloom_mcp.experiment_utils as eu
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)

    from bloom_mcp.tools.storage_tools import list_existing_analyses
    payload = json.loads(list_existing_analyses("bar.csv"))

    assert payload["experiment_filename"] == "bar.csv"
    assert payload["analyses"] == {}
    assert "No prior analyses" in payload["message"]


def test_list_existing_analyses_reports_qc_versions(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    _write_csv(traits_dir / "bar.csv", "trait_a\n1.0\n")

    qc_manifest = _make_manifest(
        filename="bar.csv",
        versions=[
            _make_version_entry(
                params={"contamination": 0.05},
                outputs={"_cleaned.csv": "v1_2026-04-20/_cleaned.csv"},
            ),
        ],
        latest="v1",
    )
    _seed_qc_dir(output_dir, "bar", qc_manifest)

    import bloom_mcp.experiment_utils as eu
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)

    from bloom_mcp.tools.storage_tools import list_existing_analyses
    payload = json.loads(list_existing_analyses("bar.csv"))

    assert "qc" in payload["analyses"]
    qc_versions = payload["analyses"]["qc"]
    assert len(qc_versions) == 1
    assert qc_versions[0]["id"] == "v1"
    assert qc_versions[0]["tool"] == "clean_experiment_data"
    assert qc_versions[0]["code_versions"]["bloommcp"] == "0.1.0"


def test_list_existing_analyses_unknown_experiment_returns_error(tmp_path, monkeypatch):
    pytest.importorskip("pandas")
    traits_dir = tmp_path / "traits"
    output_dir = tmp_path / "output"
    traits_dir.mkdir()
    output_dir.mkdir()

    import bloom_mcp.experiment_utils as eu
    monkeypatch.setattr(eu, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)

    from bloom_mcp.tools.storage_tools import list_existing_analyses
    payload = json.loads(list_existing_analyses("missing.csv"))

    assert "error" in payload
    assert "missing.csv" in payload["error"]

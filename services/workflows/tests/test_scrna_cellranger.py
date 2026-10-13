"""Unit tests for the Cell Ranger trigger: request validation, the database call,
run reads and the routes."""

import re

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import scrna_cellranger

USER = "00000000-0000-0000-0000-000000000001"
# A ready genome version the requests start on: tiny_ref v1, id 3.
VERSION_ID = 3
# A run's reads: one SRA run imported under the sample's name.
RUNS = ["SRR28503597"]
GENOME_VERSION = {
    "id": VERSION_ID,
    "version": 1,
    "genome_references": {"name": "tiny_ref"},
}


class _Result:
    def __init__(self, data):
        self.data = data


class FakeSupabase:
    """Records RPC calls and serves table reads from `tables`."""

    def __init__(self, tables=None, rpc_result=7):
        self.tables = tables or {}
        self.rpc_calls = []
        self.rpc_result = rpc_result

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        client = self

        class _Call:
            def execute(self):
                return _Result(client.rpc_result)

        return _Call()

    def table(self, name):
        rows = self.tables.get(name, [])

        class _Query:
            def __init__(self):
                self.filters = []

            def select(self, *a):
                return self

            def eq(self, key, val):
                self.filters.append((key, val))
                return self

            def limit(self, *a):
                return self

            def execute(self):
                out = [r for r in rows if all(r.get(k) == v for k, v in self.filters)]
                return _Result(out)

        return _Query()


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase(tables={"genome_reference_versions": [GENOME_VERSION]})
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# Request validation (before any database work)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "x",
        {},
        {"genome_version_id": 3},
        {"sra_runs": RUNS, "sample": "root_a"},
        {"sra_runs": RUNS, "sample": "", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "../x", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "a/b", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": ".hidden", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": 123, "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": ["root_a"], "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "x" * 101, "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "a__b", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "tinygex\n", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "S1.rep1", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "x" * 65, "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "-rep1", "genome_version_id": VERSION_ID},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 0},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": -1},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": "3"},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 3.0},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": True},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": None},
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 2**63},
        {
            "sra_runs": RUNS,
            "sample": "root_a",
            "genome_version_id": 3,
            "reference": "tiny_ref",
        },
    ],
)
def test_invalid_requests_are_rejected_before_the_database(body, monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(body, USER)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("sample", ["x" * 64, "Col0_root_rep1", "rep1-", "rep1_"])
def test_names_cellranger_can_use_are_accepted(db, sample):
    scrna_cellranger.trigger_run(
        {"sra_runs": RUNS, "sample": sample, "genome_version_id": VERSION_ID}, USER
    )
    assert db.rpc_calls[0][1]["p_sample"] == sample


def test_a_reference_name_is_refused_with_a_pointer_to_genome_versions(monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {
                "sra_runs": RUNS,
                "sample": "root_a",
                "genome_version_id": 3,
                "reference": "tiny_ref",
            },
            USER,
        )
    assert exc.value.status_code == 422
    assert "reference names aren't taken" in exc.value.detail


def test_the_largest_id_the_database_holds_is_accepted(db):
    db.tables["genome_reference_versions"].append(
        {"id": 2**63 - 1, "version": 1, "genome_references": {"name": "big"}}
    )
    started = scrna_cellranger.trigger_run(
        {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 2**63 - 1}, USER
    )
    assert started["reference"] == "big.v1"


def test_a_version_whose_genome_cant_be_read_is_422_and_nothing_is_started(db):
    db.tables["genome_reference_versions"].append(
        {"id": 5, "version": 1, "genome_references": None}
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 5}, USER
        )
    assert (exc.value.status_code, exc.value.detail) == (422, "no genome version 5")
    assert db.rpc_calls == []


def test_an_unknown_genome_version_is_422_and_nothing_is_started(db):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": 99}, USER
        )
    assert (exc.value.status_code, exc.value.detail) == (422, "no genome version 99")
    assert db.rpc_calls == []


def test_a_version_that_isnt_ready_is_refused_by_the_database_as_422(db):
    from postgrest import APIError

    def _refuse(name, params):
        class _Call:
            def execute(self):
                raise APIError(
                    {
                        "code": "22023",
                        "message": "genome tiny_ref.v1 is withdrawn, not ready",
                    }
                )

        return _Call()

    db.rpc = _refuse
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": VERSION_ID},
            USER,
        )
    assert (exc.value.status_code, exc.value.detail) == (
        422,
        "genome tiny_ref.v1 is withdrawn, not ready",
    )


def test_a_sample_name_alone_is_refused_before_the_database(monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"sample": "root_a", "genome_version_id": VERSION_ID}, USER
        )
    assert (exc.value.status_code, exc.value.detail) == (
        422,
        "give an S3 folder or SRA run IDs to read the sample from",
    )


def test_a_dotted_sample_is_refused_with_the_sample_rule(monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"sra_runs": RUNS, "sample": "S1.rep1", "reference": "r"}, USER
        )
    assert (
        exc.value.detail == f"sample must be a name of {scrna_cellranger.SAMPLE_HELP}"
    )
    assert "at most 64" in exc.value.detail


# --------------------------------------------------------------------------- #
# The database call
# --------------------------------------------------------------------------- #


def test_a_valid_request_calls_the_request_function_once(db):
    out = scrna_cellranger.trigger_run(
        {"sra_runs": RUNS, "sample": "root_b", "genome_version_id": VERSION_ID}, USER
    )
    assert db.rpc_calls == [
        (
            "request_scrna_cellranger_run",
            {
                "p_sample": "root_b",
                "p_reference": None,
                "p_genome_version_id": VERSION_ID,
                "p_requested_by": USER,
                "p_sra_runs": RUNS,
            },
        )
    ]
    assert out == {
        "run_id": 7,
        "sample": "root_b",
        "genome_version_id": VERSION_ID,
        "reference": "tiny_ref.v1",
        "run_key": f"root_b__tiny_ref.v1__{USER}",
        "sra_runs": RUNS,
    }


def test_metadata_is_passed_to_the_request_function(db):
    metadata = {"species_id": 2, "dataset_name": "Root atlas", "tissue": "root"}
    scrna_cellranger.trigger_run(
        {
            "sra_runs": RUNS,
            "sample": "root_b",
            "genome_version_id": VERSION_ID,
            "metadata": metadata,
        },
        USER,
    )
    assert db.rpc_calls[0][1] == {
        "p_sample": "root_b",
        "p_reference": None,
        "p_genome_version_id": VERSION_ID,
        "p_requested_by": USER,
        "p_metadata": metadata,
        "p_sra_runs": RUNS,
    }


def test_null_metadata_is_left_out_of_the_call(db):
    scrna_cellranger.trigger_run(
        {
            "sra_runs": RUNS,
            "sample": "root_b",
            "genome_version_id": VERSION_ID,
            "metadata": None,
        },
        USER,
    )
    assert "p_metadata" not in db.rpc_calls[0][1]


@pytest.mark.parametrize("metadata", [[], "root", 5, True, [{"tissue": "root"}]])
def test_metadata_that_is_not_an_object_is_422(metadata, monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {
                "sra_runs": RUNS,
                "sample": "root_b",
                "genome_version_id": VERSION_ID,
                "metadata": metadata,
            },
            USER,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "metadata must be a JSON object"


def test_metadata_over_the_size_limit_is_422(monkeypatch):
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    too_big = {"notes": "x" * scrna_cellranger.METADATA_MAX_BYTES}
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {
                "sra_runs": RUNS,
                "sample": "root_b",
                "genome_version_id": VERSION_ID,
                "metadata": too_big,
            },
            USER,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == "metadata must be at most 64 KB"


def test_metadata_at_the_size_limit_is_accepted(db):
    # {"notes": "..."} is 13 bytes around the value.
    at_limit = {"notes": "x" * (scrna_cellranger.METADATA_MAX_BYTES - 13)}
    scrna_cellranger.trigger_run(
        {
            "sra_runs": RUNS,
            "sample": "root_b",
            "genome_version_id": VERSION_ID,
            "metadata": at_limit,
        },
        USER,
    )
    assert db.rpc_calls[0][1]["p_metadata"] == at_limit


# --------------------------------------------------------------------------- #
# Run reads
# --------------------------------------------------------------------------- #


_CELLRANGER_ROW = {
    "id": 3,
    "workflow_type": "scrna-cellranger",
    "params": {"sra_runs": RUNS, "sample": "a", "reference": "r"},
    "status": "queued",
}


def test_get_run_returns_the_stored_row(monkeypatch):
    fake = FakeSupabase(
        tables={
            "rnaseq_runs": [
                _CELLRANGER_ROW,
                {**_CELLRANGER_ROW, "id": 4, "status": "running"},
            ]
        }
    )
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: fake)
    assert scrna_cellranger.get_run(3) == _CELLRANGER_ROW


def test_get_run_of_another_workflow_type_is_404(monkeypatch):
    fake = FakeSupabase(
        tables={"rnaseq_runs": [{**_CELLRANGER_ROW, "workflow_type": "fastqc"}]}
    )
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: fake)
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.get_run(3)
    assert exc.value.status_code == 404


def test_get_run_of_an_unknown_run_is_404(monkeypatch):
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: FakeSupabase())
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.get_run(99)
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Route wiring
# --------------------------------------------------------------------------- #


@pytest.fixture
def app_as_user():
    import main
    from auth import require_supabase_user

    main.app.dependency_overrides[require_supabase_user] = lambda: USER
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def test_routes_require_auth(monkeypatch):
    import main
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    monkeypatch.setattr(
        scrna_cellranger, "trigger_run", lambda b, u: pytest.fail("ran")
    )
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        client = TestClient(main.app)
        assert client.post("/scrna/cellranger/runs", json={}).status_code == 401
        assert client.get("/scrna/cellranger/runs/1").status_code == 401
    finally:
        main.app.dependency_overrides.clear()


def test_start_run_returns_201(app_as_user, db):
    resp = app_as_user.post(
        "/scrna/cellranger/runs",
        json={"sra_runs": RUNS, "sample": "root_a", "genome_version_id": VERSION_ID},
    )
    assert resp.status_code == 201
    assert resp.json()["run_key"] == f"root_a__tiny_ref.v1__{USER}"


def test_start_run_validation_error_is_422(app_as_user, db):
    resp = app_as_user.post(
        "/scrna/cellranger/runs",
        json={"sra_runs": RUNS, "sample": "root_a", "reference": "../x"},
    )
    assert resp.status_code == 422
    assert db.rpc_calls == []


def test_start_run_is_rate_limited_before_any_work(app_as_user, db, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "RATE_LIMIT", 1)
    body = {"sra_runs": RUNS, "sample": "root_a", "genome_version_id": VERSION_ID}
    assert app_as_user.post("/scrna/cellranger/runs", json=body).status_code == 201
    resp = app_as_user.post("/scrna/cellranger/runs", json=body)
    assert resp.status_code == 429
    assert len(db.rpc_calls) == 1


# --------------------------------------------------------------------------- #
# Matches the database's rules
# --------------------------------------------------------------------------- #


def _latest_db_rule(name: str) -> str:
    """The last `v_<name>_rule` defined across the migrations, in timestamp order."""
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
    found = []
    for path in sorted(migrations.glob("*.sql")):
        found += re.findall(
            rf"v_{name}_rule CONSTANT TEXT := '([^']+)'", path.read_text()
        )
    assert found, f"no v_{name}_rule in the migrations"
    return found[-1]


@pytest.mark.parametrize(
    "name, rule",
    [
        ("sample", scrna_cellranger.SAMPLE_RULE),
    ],
)
def test_the_api_rule_is_the_databases_rule(name, rule):
    # The database checks '__' separately, so the API's look-ahead is not part of it.
    assert rule.pattern.replace("(?!.*__)", "") == _latest_db_rule(name)


# --------------------------------------------------------------------------- #
# An S3 folder
# --------------------------------------------------------------------------- #

FOLDER = {
    "fastq_url": "s3://lab-data/run42/",
    "sample": "col0",
    "lanes": [1],
    "files": [
        {"name": "col0_S1_L001_R1_001.fastq.gz", "size": 10, "etag": '"a"'},
        {"name": "col0_S1_L001_R2_001.fastq.gz", "size": 32, "etag": '"b"'},
    ],
    "file_count": 2,
    "total_bytes": 42,
}


@pytest.fixture
def folder(monkeypatch):
    checked = []

    def check(url):
        checked.append(url)
        return FOLDER

    monkeypatch.setattr(scrna_cellranger.s3_folder, "check_folder", check)
    return checked


SHOWN = FOLDER["files"]


def test_a_folder_run_is_checked_and_named_from_its_files(db, folder):
    started = scrna_cellranger.trigger_run(
        {
            "fastq_url": "s3://lab-data/run42",
            "genome_version_id": VERSION_ID,
            "fastq_files": SHOWN,
        },
        USER,
    )
    assert folder == ["s3://lab-data/run42"]
    assert db.rpc_calls == [
        (
            "request_scrna_cellranger_run",
            {
                "p_sample": "col0",
                "p_reference": None,
                "p_genome_version_id": VERSION_ID,
                "p_requested_by": USER,
                "p_fastq_url": "s3://lab-data/run42/",
                "p_fastq_files": FOLDER["files"],
            },
        )
    ]
    assert started["sample"] == "col0"
    assert started["run_key"] == f"col0__tiny_ref.v1__{USER}"
    assert {
        k: started[k]
        for k in ("fastq_url", "lanes", "files", "file_count", "total_bytes")
    } == {
        "fastq_url": "s3://lab-data/run42/",
        "lanes": [1],
        "files": FOLDER["files"],
        "file_count": 2,
        "total_bytes": 42,
    }


def test_a_folder_run_keeps_its_metadata(db, folder):
    scrna_cellranger.trigger_run(
        {
            "fastq_url": "s3://lab-data/run42/",
            "genome_version_id": VERSION_ID,
            "metadata": {"k": "v"},
            "fastq_files": SHOWN,
        },
        USER,
    )
    assert db.rpc_calls[0][1]["p_metadata"] == {"k": "v"}


def test_the_files_shown_may_come_in_any_order(db, folder):
    scrna_cellranger.trigger_run(
        {
            "fastq_url": "s3://lab-data/run42/",
            "genome_version_id": VERSION_ID,
            "fastq_files": list(reversed(SHOWN)),
        },
        USER,
    )
    assert len(db.rpc_calls) == 1


@pytest.mark.parametrize(
    "shown",
    [
        SHOWN[:1],
        SHOWN + [{"name": "col0_S1_L002_R1_001.fastq.gz", "size": 5, "etag": '"c"'}],
        [{**SHOWN[0], "size": 11}, SHOWN[1]],
        [{**SHOWN[0], "etag": '"new"'}, SHOWN[1]],
    ],
    ids=["a-file-gone", "a-file-added", "a-size-changed", "a-file-replaced"],
)
def test_a_folder_that_changed_since_its_check_is_refused(db, folder, shown):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {
                "fastq_url": "s3://lab-data/run42/",
                "genome_version_id": VERSION_ID,
                "fastq_files": shown,
            },
            USER,
        )
    assert exc.value.status_code == 409 and "check it again" in exc.value.detail
    assert db.rpc_calls == []


@pytest.mark.parametrize(
    "shown", [None, "x", [1, 2]], ids=["missing", "string", "not-objects"]
)
def test_a_folder_run_needs_the_files_it_was_shown(db, folder, shown):
    body = {"fastq_url": "s3://lab-data/run42/", "genome_version_id": VERSION_ID}
    if shown is not None:
        body["fastq_files"] = shown
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(body, USER)
    assert exc.value.status_code == 422 and "fastq_files" in exc.value.detail
    assert folder == [] and db.rpc_calls == []


@pytest.mark.parametrize(
    "extra, words",
    [
        ({"sra_runs": ["SRR28503597"]}, "not both"),
        ({"sample": "col0"}, "not both"),
    ],
)
def test_a_folder_with_another_source_is_refused(db, folder, extra, words):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {
                "fastq_url": "s3://lab-data/run42/",
                "genome_version_id": VERSION_ID,
                **extra,
            },
            USER,
        )
    assert exc.value.status_code == 422 and words in exc.value.detail
    assert folder == [] and db.rpc_calls == []


def test_a_null_sample_or_sra_runs_beside_a_folder_counts_as_absent(db, folder):
    scrna_cellranger.trigger_run(
        {
            "fastq_url": "s3://lab-data/run42/",
            "genome_version_id": VERSION_ID,
            "fastq_files": SHOWN,
            "sample": None,
            "sra_runs": None,
        },
        USER,
    )
    assert db.rpc_calls[0][1]["p_sample"] == "col0"


def test_a_null_folder_beside_a_sample_counts_as_absent(db, folder):
    scrna_cellranger.trigger_run(
        {
            "sample": "col0",
            "genome_version_id": VERSION_ID,
            "fastq_url": None,
            "sra_runs": RUNS,
        },
        USER,
    )
    assert folder == []
    assert "p_fastq_url" not in db.rpc_calls[0][1]


@pytest.mark.parametrize(
    "extra",
    [{"reference": "../x"}, {"genome_version_id": VERSION_ID, "metadata": "x"}],
    ids=["bad-reference", "bad-metadata"],
)
def test_bad_fields_are_refused_before_the_folder_is_listed(db, folder, extra):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(
            {"fastq_url": "s3://lab-data/run42/", "fastq_files": SHOWN, **extra}, USER
        )
    assert exc.value.status_code == 422
    assert folder == [] and db.rpc_calls == []


def test_a_refused_folder_queues_nothing(db, monkeypatch):
    def refuse(url):
        raise HTTPException(
            status_code=422, detail="No FASTQs directly in s3://lab-data/run42/"
        )

    monkeypatch.setattr(scrna_cellranger.s3_folder, "check_folder", refuse)
    with pytest.raises(HTTPException):
        scrna_cellranger.trigger_run(
            {
                "fastq_url": "s3://lab-data/run42/",
                "genome_version_id": VERSION_ID,
                "fastq_files": SHOWN,
            },
            USER,
        )
    assert db.rpc_calls == []


def test_the_folder_check_route_returns_the_folder(app_as_user, db, folder):
    resp = app_as_user.post(
        "/scrna/cellranger/folder-check", json={"fastq_url": "s3://lab-data/run42/"}
    )
    assert resp.status_code == 200
    assert resp.json() == FOLDER
    assert db.rpc_calls == []


def test_the_folder_check_route_requires_a_json_object(app_as_user):
    assert (
        app_as_user.post("/scrna/cellranger/folder-check", json=["x"]).status_code
        == 422
    )


def test_the_folder_check_route_requires_auth():
    import main
    from auth import require_supabase_user

    def _raise_401():
        raise HTTPException(status_code=401, detail="missing token")

    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        resp = TestClient(main.app).post(
            "/scrna/cellranger/folder-check", json={"fastq_url": "x"}
        )
        assert resp.status_code == 401
    finally:
        main.app.dependency_overrides.clear()


def test_checking_folders_doesnt_use_up_the_start_limit(
    app_as_user, db, folder, monkeypatch
):
    import auth

    monkeypatch.setattr(auth, "RATE_LIMIT", 1)
    monkeypatch.setattr(auth, "FOLDER_CHECK_RATE_LIMIT", 3)
    auth._hits.clear()
    body = {"fastq_url": "s3://lab-data/run42/"}
    for _ in range(3):
        assert (
            app_as_user.post("/scrna/cellranger/folder-check", json=body).status_code
            == 200
        )
    refused = app_as_user.post("/scrna/cellranger/folder-check", json=body)
    assert refused.status_code == 429 and "(3/" in refused.json()["detail"]
    start = {
        "fastq_url": "s3://lab-data/run42/",
        "genome_version_id": VERSION_ID,
        "fastq_files": SHOWN,
    }
    assert app_as_user.post("/scrna/cellranger/runs", json=start).status_code == 201

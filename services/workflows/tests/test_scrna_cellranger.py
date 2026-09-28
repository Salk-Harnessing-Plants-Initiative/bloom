"""Unit tests for the Cell Ranger trigger: input listing, request validation,
bucket checks, the database call, run reads and the routes."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from test_scrna_s3 import BUCKET, FakeS3

import scrna_cellranger
import scrna_s3

USER = "00000000-0000-0000-0000-000000000001"


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
def s3(monkeypatch):
    fake = FakeS3(dict(BUCKET))
    monkeypatch.setattr(scrna_s3, "client", lambda: fake)
    return fake


@pytest.fixture
def db(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: fake)
    return fake


# --------------------------------------------------------------------------- #
# Inputs listing
# --------------------------------------------------------------------------- #


def test_inputs_lists_samples_with_fastq_counts(s3):
    out = scrna_cellranger.list_inputs()
    assert out["samples"] == [
        {"name": "empty", "fastq_count": 0, "total_bytes": 0},
        {"name": "root_a", "fastq_count": 1, "total_bytes": 10},
        {"name": "root_b", "fastq_count": 2, "total_bytes": 350},
    ]


def test_inputs_offers_only_references_with_reference_json(s3):
    assert scrna_cellranger.list_inputs()["references"] == ["tiny_ref"]


def test_inputs_reports_folders_whose_names_cannot_be_run(s3):
    s3.objects["raw_reads/bad name/x_S1_L001_R1_001.fastq.gz"] = 1
    s3.objects["raw_reads/a__b/a__b_S1_L001_R1_001.fastq.gz"] = 1
    out = scrna_cellranger.list_inputs()
    assert sorted(out["unusable_sample_folders"]) == ["a__b", "bad name"]
    assert not {"a__b", "bad name"} & {s["name"] for s in out["samples"]}


def test_inputs_reports_truncation(s3, monkeypatch):
    monkeypatch.setattr(scrna_cellranger, "MAX_LISTED_FOLDERS", 2)
    out = scrna_cellranger.list_inputs()
    assert out["samples_truncated"] is True
    assert len(out["samples"]) == 2


# --------------------------------------------------------------------------- #
# Request validation (before any S3 or database work)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        "x",
        {},
        {"reference": "tiny_ref"},
        {"sample": "root_a"},
        {"sample": "", "reference": "tiny_ref"},
        {"sample": "../x", "reference": "tiny_ref"},
        {"sample": "a/b", "reference": "tiny_ref"},
        {"sample": ".hidden", "reference": "tiny_ref"},
        {"sample": 123, "reference": "tiny_ref"},
        {"sample": ["root_a"], "reference": "tiny_ref"},
        {"sample": "x" * 101, "reference": "tiny_ref"},
        {"sample": "a__b", "reference": "tiny_ref"},
        {"sample": "root_a", "reference": "../etc"},
        {"sample": "root_a", "reference": "a/b"},
        {"sample": "root_a", "reference": 5},
        {"sample": "root_a", "reference": "b__c"},
    ],
)
def test_invalid_requests_are_rejected_before_any_io(body, monkeypatch):
    monkeypatch.setattr(scrna_s3, "client", lambda: pytest.fail("S3 was called"))
    monkeypatch.setattr(
        scrna_cellranger, "app_client", lambda: pytest.fail("DB was called")
    )
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run(body, USER)
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# Bucket checks and the database call
# --------------------------------------------------------------------------- #


def test_unknown_reference_is_404_and_writes_nothing(s3, db):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run({"sample": "root_a", "reference": "partial"}, USER)
    assert exc.value.status_code == 404
    assert "reference_genome/partial/" in exc.value.detail
    assert db.rpc_calls == []


@pytest.mark.parametrize("sample", ["empty", "nope"])
def test_a_sample_without_fastqs_is_404_and_writes_nothing(s3, db, sample):
    with pytest.raises(HTTPException) as exc:
        scrna_cellranger.trigger_run({"sample": sample, "reference": "tiny_ref"}, USER)
    assert exc.value.status_code == 404
    assert exc.value.detail == f"no FASTQs at raw_reads/{sample}/"
    assert db.rpc_calls == []


def test_a_valid_request_calls_the_request_function_once(s3, db):
    out = scrna_cellranger.trigger_run(
        {"sample": "root_b", "reference": "tiny_ref"}, USER
    )
    assert db.rpc_calls == [
        (
            "request_scrna_cellranger_run",
            {"p_sample": "root_b", "p_reference": "tiny_ref", "p_requested_by": USER},
        )
    ]
    assert out == {
        "run_id": 7,
        "sample": "root_b",
        "reference": "tiny_ref",
        "run_key": f"root_b__tiny_ref__{USER}",
    }


# --------------------------------------------------------------------------- #
# Run reads
# --------------------------------------------------------------------------- #


def test_get_run_returns_the_stored_row(monkeypatch):
    fake = FakeSupabase(
        tables={
            "scrna_cellranger_runs": [
                {"id": 3, "sample": "a", "status": "queued"},
                {"id": 4, "sample": "b", "status": "running"},
            ]
        }
    )
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: fake)
    assert scrna_cellranger.get_run(3) == {"id": 3, "sample": "a", "status": "queued"}


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
    monkeypatch.setattr(scrna_cellranger, "list_inputs", lambda: pytest.fail("ran"))
    main.app.dependency_overrides[require_supabase_user] = _raise_401
    try:
        client = TestClient(main.app)
        assert client.get("/scrna/cellranger/inputs").status_code == 401
        assert client.post("/scrna/cellranger/runs", json={}).status_code == 401
        assert client.get("/scrna/cellranger/runs/1").status_code == 401
    finally:
        main.app.dependency_overrides.clear()


def test_start_run_returns_201(app_as_user, s3, db):
    resp = app_as_user.post(
        "/scrna/cellranger/runs", json={"sample": "root_a", "reference": "tiny_ref"}
    )
    assert resp.status_code == 201
    assert resp.json()["run_key"] == f"root_a__tiny_ref__{USER}"


def test_start_run_validation_error_is_422(app_as_user, s3, db):
    resp = app_as_user.post(
        "/scrna/cellranger/runs", json={"sample": "root_a", "reference": "../x"}
    )
    assert resp.status_code == 422
    assert db.rpc_calls == []


def test_inputs_route_returns_the_listing(app_as_user, s3):
    resp = app_as_user.get("/scrna/cellranger/inputs")
    assert resp.status_code == 200
    assert resp.json()["references"] == ["tiny_ref"]


def test_start_run_is_rate_limited_before_any_work(app_as_user, s3, db, monkeypatch):
    import auth

    monkeypatch.setattr(auth, "RATE_LIMIT", 1)
    body = {"sample": "root_a", "reference": "tiny_ref"}
    assert app_as_user.post("/scrna/cellranger/runs", json=body).status_code == 201
    resp = app_as_user.post("/scrna/cellranger/runs", json=body)
    assert resp.status_code == 429
    assert len(db.rpc_calls) == 1

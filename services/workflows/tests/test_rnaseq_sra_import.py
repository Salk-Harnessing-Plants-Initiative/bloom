"""Unit tests for importing a Cell Ranger run's sample from SRA: the start API's sra_runs and
the database's refusals, fetch-sra in the Workflow body, the new steps and exit messages in
the status reader, and the poller registering the sample. No database or cluster."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from postgrest import APIError

import rnaseq_status as st
import rnaseq_status_poller as poller
import rnaseq_workflows
import scrna_cellranger

USER = "00000000-0000-0000-0000-000000000001"
TEMPLATE_FILE = (
    Path(__file__).resolve().parents[3]
    / "argo/scrna/cellranger/cellranger-count-template.yaml"
)
RUNS = ["SRR28503597", "SRR28503598"]


class _Result:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Records RPCs; `errors` maps an RPC name to the APIError it raises."""

    def __init__(self, result=7, errors=None):
        self.result = result
        self.errors = errors or {}
        self.rpcs = []

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        client = self

        class _Call:
            def execute(self):
                if name in client.errors:
                    raise client.errors[name]
                return _Result(client.result)

        return _Call()


# --------------------------------------------------------------------------- #
# The start API
# --------------------------------------------------------------------------- #


@pytest.fixture
def db(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(scrna_cellranger, "app_client", lambda: client)
    return client


def _start(**body):
    return scrna_cellranger.trigger_run(
        {"sample": "root_tip", "reference": "tiny_ref", **body}, USER
    )


def test_run_ids_are_passed_to_the_database_in_order(db):
    started = _start(sra_runs=["SRR28503598", "SRR28503597"])
    ((name, args),) = db.rpcs
    assert name == "request_scrna_cellranger_run"
    assert args["p_sra_runs"] == ["SRR28503598", "SRR28503597"]
    assert started["sra_runs"] == ["SRR28503598", "SRR28503597"]


def test_a_run_without_run_ids_sends_none(db):
    started = _start()
    assert "p_sra_runs" not in db.rpcs[0][1]
    assert "sra_runs" not in started


@pytest.mark.parametrize(
    "sra_runs",
    [
        [],
        [f"SRR100000{i}" for i in range(10)],
        ["SRR1000001", "SRR1000001"],
        ["GSM8180251"],
        ["srr1000001"],
        ["SRR12"],
        "SRR1000001",
        [1234567],
        {"a": "SRR1000001"},
    ],
    ids=[
        "empty",
        "ten",
        "twice",
        "gsm",
        "lowercase",
        "short",
        "string",
        "number",
        "object",
    ],
)
def test_bad_run_ids_are_refused_before_the_database(db, sra_runs):
    with pytest.raises(HTTPException) as err:
        _start(sra_runs=sra_runs)
    assert err.value.status_code == 422
    assert "SRA run IDs like SRR12046049" in err.value.detail
    assert db.rpcs == []


@pytest.mark.parametrize(
    "code, status",
    [("23505", 409), ("55000", 409), ("22023", 422)],
    ids=["name-taken", "still-importing", "bad-value"],
)
def test_the_databases_refusals_become_clear_http_errors(db, code, status):
    message = "sample root_tip is still being imported from SRA; start the run once it is registered"
    db.errors["request_scrna_cellranger_run"] = APIError(
        {"code": code, "message": message}
    )
    with pytest.raises(HTTPException) as err:
        _start(sra_runs=RUNS)
    assert (err.value.status_code, err.value.detail) == (status, message)


def test_any_other_database_error_is_not_hidden(db):
    db.errors["request_scrna_cellranger_run"] = APIError(
        {"code": "42501", "message": "denied"}
    )
    with pytest.raises(APIError):
        _start()


# --------------------------------------------------------------------------- #
# The Workflow body
# --------------------------------------------------------------------------- #

RUN = {
    "run_id": 42,
    "run_key": f"root_tip__tiny_ref__{USER}",
    "params": {"sample": "root_tip", "reference": "tiny_ref", "sra_runs": RUNS},
}


@pytest.fixture
def k8s(monkeypatch):
    monkeypatch.setattr(rnaseq_workflows.k8s_client, "ENV_LABEL", "dev")
    monkeypatch.setattr(rnaseq_workflows.k8s_client, "NAMESPACE", "runai-busch-lab")


def _tasks(body):
    return {t["name"]: t for t in body["spec"]["templates"][0]["dag"]["tasks"]}


def test_an_import_downloads_alongside_the_reference_and_the_sample_waits_for_both(k8s):
    body = rnaseq_workflows.build_cellranger_body(RUN)
    tasks = _tasks(body)
    assert list(tasks) == ["stage-reference", "fetch-sra", "sample"]
    assert "depends" not in tasks["fetch-sra"]
    assert tasks["sample"]["depends"] == "stage-reference && fetch-sra"
    assert tasks["fetch-sra"]["templateRef"] == {
        "name": rnaseq_workflows.CELLRANGER_TEMPLATE,
        "template": "fetch-sra",
    }


def test_fetch_sra_gets_the_run_ids_comma_separated_in_lane_order(k8s):
    body = rnaseq_workflows.build_cellranger_body(RUN)
    params = {p["name"]: p["value"] for p in body["spec"]["arguments"]["parameters"]}
    assert params["sra-runs"] == "SRR28503597,SRR28503598"
    args = {
        p["name"]: p["value"]
        for p in _tasks(body)["fetch-sra"]["arguments"]["parameters"]
    }
    assert args == {
        "sample": "{{workflow.parameters.sample}}",
        "sra-runs": "{{workflow.parameters.sra-runs}}",
        "run-id": "{{workflow.parameters.run-id}}",
    }


def test_fetch_sras_arguments_are_exactly_the_templates_inputs(k8s):
    import yaml

    template = yaml.safe_load(TEMPLATE_FILE.read_text())
    fetch = next(t for t in template["spec"]["templates"] if t["name"] == "fetch-sra")
    inputs = {p["name"] for p in fetch["inputs"]["parameters"]}
    body = rnaseq_workflows.build_cellranger_body(RUN)
    given = {p["name"] for p in _tasks(body)["fetch-sra"]["arguments"]["parameters"]}
    assert given == inputs


def test_a_run_without_run_ids_has_no_download(k8s):
    run = {**RUN, "params": {"sample": "root_tip", "reference": "tiny_ref"}}
    body = rnaseq_workflows.build_cellranger_body(run)
    assert list(_tasks(body)) == ["stage-reference", "sample"]
    assert _tasks(body)["sample"]["depends"] == "stage-reference"
    assert "sra-runs" not in {
        p["name"] for p in body["spec"]["arguments"]["parameters"]
    }


# --------------------------------------------------------------------------- #
# The status reader
# --------------------------------------------------------------------------- #

WF = "scrna-cellranger-dev-42-abcd1234"


def _node(template, phase, started, n, exit_code=None, outputs=None):
    out = {}
    if exit_code is not None:
        out["exitCode"] = str(exit_code)
    if outputs:
        out["parameters"] = [{"name": k, "value": v} for k, v in outputs.items()]
    return {
        "id": f"{WF}-{n}",
        "type": "Pod",
        "templateRef": {"name": "cellranger-count-template", "template": template},
        "phase": phase,
        "startedAt": started,
        "finishedAt": started if phase not in ("Running", "Pending") else None,
        "outputs": out,
    }


def _workflow(phase, *nodes):
    return {
        "metadata": {"name": WF},
        "status": {"phase": phase, "nodes": {n["id"]: n for n in nodes}},
    }


DOWNLOADED = {"fastq-count": "6", "total-bytes": "11902105823"}


def test_an_import_reports_fetch_sra_while_it_runs():
    wf = _workflow(
        "Running",
        _node("stage-reference", "Succeeded", "2026-09-30T01:00:00Z", 1),
        _node("fetch-sra", "Running", "2026-09-30T01:00:01Z", 2),
    )
    status = st.read_cellranger_status(wf, RUN)
    assert status.current_step == "fetch-sra"
    assert status.step_pods["fetch-sra"] == f"{WF}-fetch-sra-2"


@pytest.mark.parametrize("step", ["preprocess", "cluster", "build-h5ad"])
def test_the_analysis_steps_are_reported(step):
    wf = _workflow("Running", _node(step, "Running", "2026-09-30T02:00:00Z", 9))
    assert st.read_cellranger_status(wf, RUN).current_step == step


@pytest.mark.parametrize(
    "code, text",
    [
        (6, "or SRA run IDs SRR28503597, SRR28503598 can't be used"),
        (7, "couldn't be named the Illumina way"),
        (10, "Couldn't download SRR28503597, SRR28503598 from SRA"),
        (11, "lacks the 10x barcode or cDNA read"),
        (12, "raw_reads/root_tip/ already holds other FASTQs"),
    ],
)
def test_a_failed_download_says_why(code, text):
    wf = _workflow(
        "Failed",
        _node("fetch-sra", "Failed", "2026-09-30T01:00:01Z", 2, exit_code=code),
    )
    status = st.read_cellranger_status(wf, RUN)
    assert (status.status, status.current_step, status.exit_code) == (
        "failed",
        "fetch-sra",
        code,
    )
    assert text in status.message


@pytest.mark.parametrize(
    "step, code, text",
    [
        ("preprocess", 13, "Fewer than 50 cells"),
        ("preprocess", 14, "count matrix wasn't found"),
        ("build-h5ad", 15, "the build-h5ad step's log has the details"),
    ],
)
def test_a_failed_analysis_step_says_why(step, code, text):
    wf = _workflow(
        "Failed", _node(step, "Failed", "2026-09-30T02:00:00Z", 9, exit_code=code)
    )
    assert text in st.read_cellranger_status(wf, RUN).message


def test_count_exits_keep_their_meaning_outside_the_download():
    wf = _workflow(
        "Failed", _node("count", "Failed", "2026-09-30T02:00:00Z", 9, exit_code=6)
    )
    assert (
        "can't be used as a Cell Ranger run id"
        in st.read_cellranger_status(wf, RUN).message
    )


def test_the_download_reports_its_counts_once_it_succeeds():
    running = _workflow(
        "Running", _node("fetch-sra", "Running", "2026-09-30T01:00:00Z", 2)
    )
    done = _workflow(
        "Running",
        _node("fetch-sra", "Succeeded", "2026-09-30T01:00:00Z", 2, outputs=DOWNLOADED),
    )
    assert st.sra_download(running) is None
    assert st.sra_download(done) == (6, 11_902_105_823)


def test_a_download_without_its_counts_reports_none():
    wf = _workflow(
        "Running", _node("fetch-sra", "Succeeded", "2026-09-30T01:00:00Z", 2)
    )
    assert st.sra_download(wf) is None


# --------------------------------------------------------------------------- #
# The poller registering the sample
# --------------------------------------------------------------------------- #

ROW = {
    "id": 42,
    "workflow_type": "scrna-cellranger",
    "params": RUN["params"],
    "run_key": RUN["run_key"],
    "status": "running",
    "argo_workflow_name": WF,
}
AFTER_DOWNLOAD = _workflow(
    "Running",
    _node("fetch-sra", "Succeeded", "2026-09-30T01:00:00Z", 2, outputs=DOWNLOADED),
    _node("count", "Running", "2026-09-30T01:40:00Z", 5),
)


@pytest.fixture(autouse=True)
def _fresh_poller():
    poller._registered.clear()
    yield
    poller._registered.clear()


def _poll(monkeypatch, workflow, row=ROW, client=None):
    client = client or FakeClient(result=True)
    monkeypatch.setattr(poller, "get_workflow", lambda name: workflow)
    poller.poll_run(client, row)
    return client


def _registrations(client):
    return [params for name, params in client.rpcs if name == "register_rnaseq_sample"]


def test_the_sample_is_registered_once_the_download_succeeds(monkeypatch):
    client = _poll(monkeypatch, AFTER_DOWNLOAD)
    assert [name for name, _ in client.rpcs] == [
        "update_rnaseq_run_status",
        "register_rnaseq_sample",
    ]
    assert _registrations(client) == [
        {"p_run_id": 42, "p_fastq_count": 6, "p_total_bytes": 11_902_105_823}
    ]


def test_it_is_registered_only_once_by_this_poller(monkeypatch):
    client = _poll(monkeypatch, AFTER_DOWNLOAD)
    _poll(monkeypatch, AFTER_DOWNLOAD, client=client)
    assert len(_registrations(client)) == 1


def test_nothing_is_registered_while_the_download_runs(monkeypatch):
    wf = _workflow("Running", _node("fetch-sra", "Running", "2026-09-30T01:00:00Z", 2))
    assert _registrations(_poll(monkeypatch, wf)) == []


def test_a_failed_run_registers_nothing(monkeypatch):
    wf = _workflow(
        "Failed",
        _node("fetch-sra", "Succeeded", "2026-09-30T01:00:00Z", 2, outputs=DOWNLOADED),
        _node("count", "Failed", "2026-09-30T01:40:00Z", 5, exit_code=5),
    )
    assert _registrations(_poll(monkeypatch, wf)) == []


def test_a_run_without_run_ids_registers_nothing(monkeypatch):
    row = {**ROW, "params": {"sample": "tinygex", "reference": "tiny_ref"}}
    assert _registrations(_poll(monkeypatch, AFTER_DOWNLOAD, row=row)) == []


def test_a_taken_name_is_logged_once_and_not_retried(monkeypatch, caplog):
    client = FakeClient(
        result=True,
        errors={
            "register_rnaseq_sample": APIError(
                {"code": "23505", "message": "already registered"}
            )
        },
    )
    _poll(monkeypatch, AFTER_DOWNLOAD, client=client)
    _poll(monkeypatch, AFTER_DOWNLOAD, client=client)
    assert len(_registrations(client)) == 1
    assert "was not registered: already registered" in caplog.text


def test_another_registration_error_is_retried_on_the_next_poll(monkeypatch):
    client = FakeClient(
        result=True,
        errors={
            "register_rnaseq_sample": APIError(
                {"code": "08006", "message": "connection lost"}
            )
        },
    )
    with pytest.raises(APIError):
        _poll(monkeypatch, AFTER_DOWNLOAD, client=client)
    del client.errors["register_rnaseq_sample"]
    _poll(monkeypatch, AFTER_DOWNLOAD, client=client)
    assert len(_registrations(client)) == 2

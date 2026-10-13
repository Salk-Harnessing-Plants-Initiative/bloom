"""Unit tests for importing a Cell Ranger run's sample from SRA: the start API's sra_runs and
the database's refusals, fetch-sra in the Workflow body, the new steps and exit messages in
the status reader, and the poller registering nothing. No database or cluster."""

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
    """Records RPCs; `errors` maps an RPC name to the APIError it raises. Any table read
    returns genome version 3, tiny_ref v1."""

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

    def table(self, _name):
        class _Query:
            def select(self, _cols):
                return self

            def eq(self, _col, _value):
                return self

            def execute(self):
                return _Result(
                    [{"version": 1, "genome_references": {"name": "tiny_ref"}}]
                )

        return _Query()


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
        {"sample": "root_tip", "genome_version_id": 3, **body}, USER
    )


def test_run_ids_are_passed_to_the_database_in_order(db):
    started = _start(sra_runs=["SRR28503598", "SRR28503597"])
    ((name, args),) = db.rpcs
    assert name == "request_scrna_cellranger_run"
    assert args["p_sra_runs"] == ["SRR28503598", "SRR28503597"]
    assert started["sra_runs"] == ["SRR28503598", "SRR28503597"]


def test_a_sample_name_alone_is_refused_before_the_database(db):
    with pytest.raises(HTTPException) as refused:
        _start()
    assert refused.value.status_code == 422
    assert (
        refused.value.detail
        == "give an S3 folder or SRA run IDs to read the sample from"
    )
    assert db.rpcs == []


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
        _start(sra_runs=RUNS)


# --------------------------------------------------------------------------- #
# The Workflow body
# --------------------------------------------------------------------------- #

RUN = {
    "run_id": 42,
    "run_key": f"root_tip__athaliana_col0.v2__{USER}",
    "params": {
        "sample": "root_tip",
        "reference": "athaliana_col0.v2",
        "sra_runs": RUNS,
    },
    "genome": "athaliana_col0.v2",
}


@pytest.fixture
def k8s(monkeypatch):
    monkeypatch.setattr(rnaseq_workflows.k8s_client, "ENV_LABEL", "dev")
    monkeypatch.setattr(rnaseq_workflows.k8s_client, "NAMESPACE", "runai-busch-lab")


def _tasks(body):
    return {t["name"]: t for t in body["spec"]["templates"][0]["dag"]["tasks"]}


def test_an_import_is_one_sample_pipeline_given_its_run_ids(k8s):
    body = rnaseq_workflows.build_cellranger_body(RUN)
    tasks = _tasks(body)
    assert list(tasks) == ["sample"] and "depends" not in tasks["sample"]
    args = {p["name"]: p["value"] for p in tasks["sample"]["arguments"]["parameters"]}
    assert args["sra-runs"] == "{{workflow.parameters.sra-runs}}"


def test_the_run_ids_are_passed_comma_separated_in_lane_order(k8s):
    body = rnaseq_workflows.build_cellranger_body(RUN)
    params = {p["name"]: p["value"] for p in body["spec"]["arguments"]["parameters"]}
    assert params["sra-runs"] == "SRR28503597,SRR28503598"


def _pipeline_tasks():
    import yaml

    template = yaml.safe_load(TEMPLATE_FILE.read_text())
    pipeline = next(
        t for t in template["spec"]["templates"] if t["name"] == "sample-pipeline"
    )
    return {t["name"]: t for t in pipeline["dag"]["tasks"]}, template


def test_fetch_sra_runs_inside_the_pipeline_only_for_an_import():
    tasks, _ = _pipeline_tasks()
    assert tasks["fetch-sra"]["when"] == "'{{inputs.parameters.sra-runs}}' != ''"
    assert "depends" not in tasks["fetch-sra"]


def test_the_reads_come_from_fetch_sra_or_stage_alongside_the_genome_steps():
    tasks, _ = _pipeline_tasks()
    assert tasks["fetch-sra"]["when"] == "'{{inputs.parameters.sra-runs}}' != ''"
    assert tasks["stage"]["when"] == "'{{inputs.parameters.fastq-url}}' != ''"
    assert "depends" not in tasks["stage"] and "depends" not in tasks["fetch-sra"]
    # A skipped task counts as done, so qc follows whichever of the two ran.
    assert tasks["qc"]["depends"] == "fetch-sra && stage"
    assert "depends" not in tasks["download-genome"]
    assert tasks["count"]["depends"] == "qc && build-reference"


def test_fetch_sras_arguments_are_exactly_the_templates_inputs():
    tasks, template = _pipeline_tasks()
    fetch = next(t for t in template["spec"]["templates"] if t["name"] == "fetch-sra")
    inputs = {p["name"] for p in fetch["inputs"]["parameters"]}
    args = {
        p["name"]: p["value"] for p in tasks["fetch-sra"]["arguments"]["parameters"]
    }
    assert set(args) == inputs
    assert args == {
        "sample": "{{inputs.parameters.sample}}",
        "sra-runs": "{{inputs.parameters.sra-runs}}",
        "run-id": "{{inputs.parameters.run-id}}",
    }


def test_the_pipeline_declares_the_run_ids_empty_by_default():
    _, template = _pipeline_tasks()
    pipeline = next(
        t for t in template["spec"]["templates"] if t["name"] == "sample-pipeline"
    )
    declared = {p["name"]: p.get("value") for p in pipeline["inputs"]["parameters"]}
    assert declared["sra-runs"] == ""


def test_a_folder_run_has_no_download(k8s):
    files = [{"name": "root_tip_S1_L001_R1_001.fastq.gz", "size": 1, "etag": '"e"'}]
    run = {
        **RUN,
        "params": {
            "sample": "root_tip",
            "reference": "athaliana_col0.v2",
            "fastq_url": "s3://lab-data/run42/",
            "fastq_files": files,
        },
    }
    body = rnaseq_workflows.build_cellranger_body(run)
    assert list(_tasks(body)) == ["sample"]
    assert "depends" not in _tasks(body)["sample"]
    assert "sra-runs" not in {
        p["name"] for p in body["spec"]["arguments"]["parameters"]
    }
    assert "sra-runs" not in {
        p["name"] for p in _tasks(body)["sample"]["arguments"]["parameters"]
    }, "the pipeline's empty default skips fetch-sra"


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


def test_an_import_reports_fetch_sra_while_it_runs():
    wf = _workflow(
        "Running",
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
        (10, "Couldn't download SRR28503597, SRR28503598 from ENA or SRA"),
        (11, "don't look like 10x gene-expression reads"),
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


def test_the_retired_folder_taken_exit_has_no_message_of_its_own():
    wf = _workflow(
        "Failed", _node("fetch-sra", "Failed", "2026-09-30T01:00:01Z", 2, exit_code=12)
    )
    assert (
        st.read_cellranger_status(wf, RUN).message == "Step fetch-sra failed (exit 12)"
    )


# --------------------------------------------------------------------------- #
# The poller registers nothing
# --------------------------------------------------------------------------- #

ROW = {
    "id": 42,
    "workflow_type": "scrna-cellranger",
    "params": RUN["params"],
    "run_key": RUN["run_key"],
    "status": "running",
    "argo_workflow_name": WF,
}


def test_a_finished_download_registers_no_sample(monkeypatch):
    wf = _workflow(
        "Running",
        _node("fetch-sra", "Succeeded", "2026-09-30T01:00:00Z", 2),
        _node("count", "Running", "2026-09-30T01:40:00Z", 5),
    )
    client = FakeClient(result=True)
    monkeypatch.setattr(poller, "get_workflow", lambda name: wf)
    poller.poll_run(client, ROW)
    assert [name for name, _ in client.rpcs] == ["update_rnaseq_run_status"]

"""Unit tests for the load-dataset step as the status poller sees it: the step is followed like
the others, its failures say what happened, and once it succeeds the run is linked to its
dataset with link_rnaseq_run_dataset, before the run's status is recorded. Fake client and
fake Workflows; no database or cluster."""

import pytest
from postgrest import APIError

import k8s_client
import rnaseq_status
import rnaseq_status_poller as poller
import rnaseq_workflows as wfs
from rnaseq_status import read_cellranger_status

RUN = {
    "id": 7,
    "workflow_type": "scrna-cellranger",
    "params": {"sample": "col0", "reference": "tair10"},
    "run_key": "col0__tair10__u",
    "status": "running",
    "argo_workflow_name": "wf-7",
}


def _workflow(
    phase="Running", load=None, load_exit=None, after=None, dataset_name="Root atlas"
):
    """A Workflow naming `dataset_name` (None: no dataset-name argument, as the worker
    builds it) whose build-h5ad succeeded, with load-dataset in `load`'s phase."""
    nodes = {
        "wf-7-1": {
            "id": "wf-7-1",
            "type": "Pod",
            "templateName": "build-h5ad",
            "phase": "Succeeded",
            "startedAt": "2026-10-06T10:00:00Z",
            "finishedAt": "2026-10-06T10:10:00Z",
        }
    }
    if load:
        node = {
            "id": "wf-7-2",
            "type": "Pod",
            "templateName": "load-dataset",
            "phase": load,
            "startedAt": "2026-10-06T10:11:00Z",
            "finishedAt": "2026-10-06T10:40:00Z",
        }
        if load_exit is not None:
            node["outputs"] = {"exitCode": str(load_exit)}
        nodes["wf-7-2"] = node
    if after:
        nodes["wf-7-3"] = {
            "id": "wf-7-3",
            "type": "Pod",
            "templateName": "cleanup",
            "phase": after,
            "startedAt": "2026-10-06T10:41:00Z",
            "finishedAt": "2026-10-06T10:42:00Z",
        }
    parameters = [{"name": "sample", "value": "col0"}]
    if dataset_name is not None:
        parameters.append({"name": "dataset-name", "value": dataset_name})
    return {
        "metadata": {"name": "wf-7"},
        "spec": {"arguments": {"parameters": parameters}},
        "status": {"phase": phase, "nodes": nodes},
    }


class FakeClient:
    """Records RPCs; the link answers with `link` (a dataset id) or raises it. A list
    gives one answer per call, the last repeated."""

    def __init__(self, link=42):
        self.answers = link if isinstance(link, list) else [link]
        self.rpcs = []

    def rpc(self, name, params):
        self.rpcs.append(name)
        answer = True
        if name == poller.LINK_FN:
            answer = self.answers[0] if len(self.answers) == 1 else self.answers.pop(0)

        class _Call:
            def execute(self):
                if isinstance(answer, Exception):
                    raise answer
                return type("R", (), {"data": answer})()

        return _Call()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    poller._linked.clear()
    yield
    poller._linked.clear()


def _poll(monkeypatch, workflow, client):
    monkeypatch.setattr(poller, "get_workflow", lambda name: workflow)
    poller.poll_run(client, RUN)
    return client.rpcs


# --------------------------------------------------------------------------- #
# The step as the run page shows it
# --------------------------------------------------------------------------- #


def test_the_load_step_runs_between_build_h5ad_and_cleanup():
    steps = list(rnaseq_status.CELLRANGER_STEPS.values())
    assert (
        steps.index("build-h5ad") < steps.index("load-dataset") < steps.index("cleanup")
    )


def test_a_running_load_is_the_runs_current_step():
    status = read_cellranger_status(_workflow(load="Running"), RUN)
    assert (status.status, status.current_step) == ("running", "load-dataset")


@pytest.mark.parametrize(
    "exit_code, words",
    [
        (16, "has no Bloom credentials"),
        (
            1,
            "Loading the dataset into Bloom failed; the load-dataset step's log says why",
        ),
        (None, "Loading the dataset into Bloom failed"),
    ],
)
def test_a_failed_load_says_what_happened(exit_code, words):
    status = read_cellranger_status(
        _workflow("Failed", load="Failed", load_exit=exit_code), RUN
    )
    assert (status.status, status.current_step) == ("failed", "load-dataset")
    assert words in status.message


# --------------------------------------------------------------------------- #
# Linking the run to its dataset
# --------------------------------------------------------------------------- #


def test_a_loaded_dataset_is_linked_before_the_status_is_recorded(monkeypatch):
    rpcs = _poll(monkeypatch, _workflow(load="Succeeded"), FakeClient())
    assert rpcs[:2] == [poller.LINK_FN, poller.UPDATE_FN]


@pytest.mark.parametrize("load", [None, "Running", "Failed"])
def test_no_link_until_the_load_has_succeeded(monkeypatch, load):
    rpcs = _poll(monkeypatch, _workflow(load=load), FakeClient())
    assert poller.LINK_FN not in rpcs


def test_a_run_is_linked_once(monkeypatch):
    client = FakeClient()
    _poll(monkeypatch, _workflow(load="Succeeded"), client)
    _poll(monkeypatch, _workflow(load="Succeeded", after="Running"), client)
    assert client.rpcs.count(poller.LINK_FN) == 1


def test_a_run_that_failed_after_its_load_is_still_linked(monkeypatch):
    rpcs = _poll(
        monkeypatch, _workflow("Failed", load="Succeeded", after="Failed"), FakeClient()
    )
    assert rpcs[:2] == [poller.LINK_FN, poller.UPDATE_FN]
    assert rpcs.count(poller.LINK_FN) == 1, "a linked run isn't linked again"


@pytest.mark.parametrize("name", [None, "", "   "])
def test_a_run_naming_no_dataset_is_not_linked(monkeypatch, caplog, name):
    client = FakeClient()
    with caplog.at_level("ERROR", logger="rnaseq_status_poller"):
        _poll(
            monkeypatch,
            _workflow(
                "Succeeded", load="Succeeded", after="Succeeded", dataset_name=name
            ),
            client,
        )
    assert poller.LINK_FN not in client.rpcs
    assert caplog.text == ""


def test_a_run_first_seen_finished_is_linked_after_its_status_is_recorded(monkeypatch):
    not_yet = APIError({"code": "55000", "message": "run 7 is submitted"})
    client = FakeClient(link=[not_yet, 42])
    rpcs = _poll(
        monkeypatch,
        _workflow("Succeeded", load="Succeeded", after="Succeeded"),
        client,
    )
    assert rpcs[:3] == [poller.LINK_FN, poller.UPDATE_FN, poller.LINK_FN]
    assert 7 in poller._linked


def test_a_running_run_is_linked_once_per_pass(monkeypatch):
    # "Not yet" keeps the run unlinked, so only the running-or-final check limits the calls.
    not_yet = APIError({"code": "55000", "message": "run 7 is submitted"})
    rpcs = _poll(monkeypatch, _workflow(load="Succeeded"), FakeClient(link=not_yet))
    assert rpcs.count(poller.LINK_FN) == 1


@pytest.mark.parametrize(
    "dataset, loaded",
    [({"name": "Root atlas", "species": "Arabidopsis"}, True), (None, False)],
)
def test_a_load_counts_only_for_a_workflow_the_worker_built_with_a_dataset(
    monkeypatch, dataset, loaded
):
    monkeypatch.setattr(k8s_client, "NAMESPACE", "runai-busch-lab")
    monkeypatch.setattr(k8s_client, "ENV_LABEL", "staging")
    run = {
        "run_id": 7,
        "workflow_type": "scrna-cellranger",
        "params": {"sample": "col0", "reference": "tair10"},
        "run_key": "col0__tair10__u",
        "dataset": dataset,
    }
    workflow = wfs.build_cellranger_body(run)
    workflow["status"] = _workflow(load="Succeeded")["status"]
    assert rnaseq_status.dataset_loaded(workflow) is loaded


def test_not_yet_is_tried_again_at_the_next_poll(monkeypatch):
    not_yet = APIError({"code": "55000", "message": "run 7 is submitted"})
    client = FakeClient(link=not_yet)
    _poll(monkeypatch, _workflow(load="Succeeded"), client)
    _poll(monkeypatch, _workflow(load="Succeeded"), client)
    assert client.rpcs.count(poller.LINK_FN) == 2
    assert poller.UPDATE_FN in client.rpcs, "the status is still recorded"


@pytest.mark.parametrize("code", ["P0002", "23505", "42501", "21000"])
def test_a_link_refused_for_good_is_logged_once_and_the_run_carries_on(
    monkeypatch, caplog, code
):
    client = FakeClient(link=APIError({"code": code, "message": "no dataset named x"}))
    with caplog.at_level("ERROR", logger="rnaseq_status_poller"):
        _poll(monkeypatch, _workflow(load="Succeeded"), client)
        _poll(monkeypatch, _workflow(load="Succeeded"), client)
    assert client.rpcs.count(poller.LINK_FN) == 1
    assert "was not linked to its dataset" in caplog.text
    assert poller.UPDATE_FN in client.rpcs


def test_an_unexpected_link_error_leaves_the_run_unrecorded(monkeypatch):
    client = FakeClient(link=RuntimeError("database down"))
    monkeypatch.setattr(
        poller, "get_workflow", lambda name: _workflow("Succeeded", load="Succeeded")
    )
    with pytest.raises(RuntimeError):
        poller.poll_run(client, RUN)
    assert client.rpcs == [poller.LINK_FN], (
        "nothing recorded, so the next poll links and records"
    )

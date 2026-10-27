"""Unit tests for scripts/check_template_contract.py (bloom #879).

No cluster and no network: `_probe_cluster` and `_fetch_live` are monkeypatched, matching
this repo's other script-shape tests (`test_check_vendored_workflow_drift.py` patches
`fetch_with_retry`/`fetch_canonical_file` the same way). Named seams are patched rather
than only `subprocess.run`, so the tests say what they mean — and one test asserts the
real `kubectl` argv, because every other test mocks it away and a typo there would
otherwise ship unnoticed (the reason `test_check_uv_locks.py` does the same).

The `#### Scenario:` blocks in
`openspec/changes/fix-registered-template-contract-check/specs/cyl-pipeline-dispatch/spec.md`
map 1:1 onto the tests below; the mapping is noted per test.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_template_contract.py"
_FIXTURES = Path(__file__).parent / "fixtures"
VENDORED = REPO_ROOT / "services" / "workflows" / "vendored" / "sleap-roots-pipeline.yaml"


def _load():
    spec = importlib.util.spec_from_file_location("check_template_contract", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


# --------------------------------------------------------------------------------------
# Builders. Deliberately mirror the real shapes captured in tests/unit/fixtures/:
# stored templates carry `inputs: {}` (present but EMPTY, not absent) and `name: ""`.
# --------------------------------------------------------------------------------------


def _task(name, *, template=None, params=None):
    task = {
        "name": name,
        "templateRef": {"name": f"sleap-roots-{name}-template", "template": template or name},
    }
    if params is not None:
        task["arguments"] = {"parameters": [{"name": p, "value": "x"} for p in params]}
    return task


def _wf(tasks, *, globals_=None, volumes=None):
    """A vendored Workflow with an entrypoint-resolved DAG."""
    wf = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "spec": {
            "entrypoint": "pipeline",
            "templates": [{"name": "pipeline", "dag": {"tasks": tasks}}],
        },
    }
    if globals_ is not None:
        wf["spec"]["arguments"] = {"parameters": [{"name": g} for g in globals_]}
    if volumes is not None:
        wf["spec"]["volumes"] = [{"name": v} for v in volumes]
    return wf


def _tmpl(
    inner,
    *,
    obj_name=None,
    inputs="empty",
    image="ghcr.io/example/img:sha-aaaaaaa",
    env=None,
    mounts=None,
    args=None,
):
    """A registered WorkflowTemplate as the API server stores one."""
    container = {"name": "", "image": image, "imagePullPolicy": "Always"}
    if env:
        container["env"] = [{"name": k, "value": v} for k, v in env.items()]
    if mounts:
        container["volumeMounts"] = [
            {"name": m, "mountPath": f"/workspace/{m}"} for m in mounts
        ]
    if args:
        container["args"] = list(args)

    template = {"name": inner, "container": container, "metadata": {}, "outputs": {}}
    if inputs == "empty":
        template["inputs"] = {}
    elif inputs == "absent":
        pass
    elif inputs == "empty-list":
        template["inputs"] = {"parameters": []}
    else:
        template["inputs"] = {"parameters": list(inputs)}

    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "WorkflowTemplate",
        "metadata": {
            "name": obj_name or f"sleap-roots-{inner}-template",
            "namespace": "ns",
        },
        "spec": {"arguments": {}, "templates": [template]},
    }


def _write(tmp_path, wf):
    path = tmp_path / "sleap-roots-pipeline.yaml"
    path.write_text(yaml.safe_dump(wf), encoding="utf-8")
    return path


@pytest.fixture
def cluster(monkeypatch):
    """Install a fake cluster. `objects` maps WorkflowTemplate name -> dict | NotFound."""
    state = {"objects": {}, "reachable": True, "calls": []}

    def _probe(namespace):
        state["calls"].append(("probe", namespace))
        if not state["reachable"]:
            raise mod.CheckUnavailable(f"cannot reach {namespace}")

    def _fetch(obj, namespace):
        state["calls"].append(("get", obj))
        value = state["objects"].get(obj, "NOTFOUND")
        if value == "NOTFOUND":
            return None
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(mod, "_probe_cluster", _probe)
    monkeypatch.setattr(mod, "_fetch_live", _fetch)
    return state


def _register(cluster, *templates):
    for t in templates:
        cluster["objects"][t["metadata"]["name"]] = t


def _refs(*names):
    return frozenset((f"sleap-roots-{n}-template", n) for n in names)


def _run(tmp_path, wf, cluster_state, *, expected=None, namespace="ns"):
    path = _write(tmp_path, wf)
    names = [t["name"] for t in wf["spec"]["templates"][0]["dag"]["tasks"]]
    return mod.check_contract(
        path, namespace, expected_refs=expected if expected is not None else _refs(*names)
    )


# --------------------------------------------------------------------------------------
# The committed expected set must describe the real vendored Workflow. No cluster needed;
# this is the guard that a vendored-Workflow edit cannot silently outrun the constant.
# --------------------------------------------------------------------------------------


def test_expected_refs_constant_matches_the_real_vendored_workflow():
    wf = yaml.safe_load(VENDORED.read_text(encoding="utf-8"))
    discovered = mod.discover_template_refs(wf)
    assert discovered == mod.EXPECTED_TEMPLATE_REFS, (
        "the vendored Workflow's templateRefs and EXPECTED_TEMPLATE_REFS disagree; "
        "if the DAG was restructured on purpose, update the constant deliberately"
    )


def test_expected_refs_constant_has_the_five_known_stages():
    assert mod.EXPECTED_TEMPLATE_REFS == _refs(
        "images-downloader", "predictor", "trait-extractor", "write-back", "exit-gate"
    )


# --------------------------------------------------------------------------------------
# Scenario: Newer image pins on a structurally conforming cluster report success.
# The bloom#879 regression guard.
# --------------------------------------------------------------------------------------


def test_differing_image_tags_and_digests_do_not_fail_the_check(tmp_path, cluster):
    """bloom#879: the five real diffs of 2026-09-21 must not be a violation.

    Image values here are deliberately the real ones observed live -- the bloomctl bump
    and both @sha256: digest pins -- against a vendored Workflow that knows nothing of
    them. Under the old comparator this was DRIFT x5, exit 1.
    """
    wf = _wf([_task("images-downloader"), _task("predictor")], volumes=["images-input-dir"])
    _register(
        cluster,
        _tmpl(
            "images-downloader",
            image="ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-28034f6",
            mounts=["images-input-dir"],
        ),
        _tmpl(
            "predictor",
            image="ghcr.io/talmolab/sleap-roots-predict:sha-e025e309@sha256:4d4064c6",
            env={"SRP_PREDICT_CONTAINER_DIGEST": "sha256:4d4064c6"},
        ),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


def test_image_references_are_printed_on_the_success_path(tmp_path, cluster, capsys):
    wf = _wf([_task("predictor")])
    _register(cluster, _tmpl("predictor", image="ghcr.io/example/img:sha-deadbee"))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK
    assert "sha-deadbee" in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# Scenario: A workflow-level parameter that no template declares is not a violation.
# The false-positive trap: folding spec.arguments.parameters into the per-task "passed"
# set would fail on today's correct cluster, since images-downloader declares no inputs.
# --------------------------------------------------------------------------------------


def test_workflow_level_global_is_not_treated_as_a_passed_task_parameter(tmp_path, cluster):
    wf = _wf([_task("images-downloader")], globals_=["scan-ids"])
    _register(cluster, _tmpl("images-downloader", inputs="empty"))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


@pytest.mark.parametrize("inputs", ["absent", "empty", "empty-list"])
@pytest.mark.parametrize("params", [None, []])
def test_no_arguments_and_no_inputs_is_the_common_case_and_passes(
    tmp_path, cluster, inputs, params
):
    """Four of the five real tasks have no `arguments` and templates with `inputs: {}`."""
    wf = _wf([_task("write-back", params=params)])
    _register(cluster, _tmpl("write-back", inputs=inputs))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


# --------------------------------------------------------------------------------------
# Scenarios: the six contract violations.
# --------------------------------------------------------------------------------------


def test_missing_workflow_template_is_a_violation(tmp_path, cluster, capsys):
    wf = _wf([_task("predictor")])
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "sleap-roots-predictor-template" in capsys.readouterr().err


def test_wrong_inner_template_name_is_a_violation(tmp_path, cluster, capsys):
    """The defect that cannot fail at submit: the API server accepts it regardless.

    Note which side is perturbed. The vendored Workflow is left correct — it references
    `(sleap-roots-predictor-template, predictor)`, matching the expected set — and the
    *cluster's* object is the one that declares a differently-named inner template. A
    vendored-Workflow-side mismatch is a different fault with a different outcome
    (`CHECK FAILED`, exit 2), covered by
    `test_discovered_refs_not_equal_to_the_expected_set_is_unavailable`.
    """
    wf = _wf([_task("predictor")])
    _register(cluster, _tmpl("predict", obj_name="sleap-roots-predictor-template"))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    err = capsys.readouterr().err
    # Asserting the whole phrase, not `"predictor" in err`: that is a substring of
    # "sleap-roots-predictor-template" and so could never fail independently.
    assert "declares no template named 'predictor'" in err
    assert "sleap-roots-predictor-template" in err


def test_passed_parameter_not_declared_by_the_template_is_a_violation(tmp_path, cluster, capsys):
    wf = _wf([_task("exit-gate", params=["nonesuch"])])
    _register(cluster, _tmpl("exit-gate", inputs=[{"name": "images-downloader-code"}]))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "nonesuch" in capsys.readouterr().err


def test_required_template_input_not_supplied_is_a_violation(tmp_path, cluster, capsys):
    """The direction that actually fires. exit-gate's three inputs have no defaults."""
    wf = _wf([_task("exit-gate", params=["images-downloader-code"])])
    _register(
        cluster,
        _tmpl(
            "exit-gate",
            inputs=[{"name": "images-downloader-code"}, {"name": "predictor-code"}],
        ),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "predictor-code" in capsys.readouterr().err


def test_a_workflow_level_global_does_not_satisfy_a_required_template_input(tmp_path, cluster):
    """Argo binds `spec.arguments.parameters` to the ENTRYPOINT template's inputs.

    A template invoked through a DAG task's `templateRef` must have its non-defaulted inputs
    supplied by that task, or the controller fails with `inputs.parameters.X was not
    supplied`. An earlier draft subtracted the workflow globals here, which would have
    exempted any required input whose name merely collided with a global — silently
    disabling the assertion the design argues matters most.
    """
    wf = _wf([_task("write-back")], globals_=["scan-ids"])
    _register(cluster, _tmpl("write-back", inputs=[{"name": "scan-ids"}]))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "verbosity", "default": "info"},
        {"name": "verbosity", "value": "info"},
        {"name": "verbosity", "valueFrom": {"configMapKeyRef": {"name": "c", "key": "k"}}},
    ],
    ids=["default", "value", "valueFrom"],
)
def test_a_declared_input_that_supplies_itself_need_not_be_passed(tmp_path, cluster, entry):
    """Argo treats an input as unsatisfied only when Value and ValueFrom are both nil."""
    wf = _wf([_task("write-back")])
    _register(cluster, _tmpl("write-back", inputs=[entry]))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


def test_volume_mount_naming_an_undeclared_volume_is_a_violation(tmp_path, cluster, capsys):
    wf = _wf([_task("images-downloader")], volumes=["images-input-dir"])
    _register(
        cluster,
        _tmpl("images-downloader", mounts=["images-input-dir", "traits-output-dir"]),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "traits-output-dir" in capsys.readouterr().err


def test_undeclared_workflow_parameter_reference_is_a_violation(tmp_path, cluster, capsys):
    wf = _wf([_task("images-downloader")], globals_=["scan-ids"])
    _register(
        cluster,
        _tmpl("images-downloader", args=["--ids", "{{workflow.parameters.scanIds}}"]),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "scanIds" in capsys.readouterr().err


def test_declared_workflow_parameter_reference_passes(tmp_path, cluster):
    wf = _wf([_task("images-downloader")], globals_=["scan-ids"])
    _register(
        cluster,
        _tmpl("images-downloader", args=["--ids", "{{workflow.parameters.scan-ids}}"]),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


def test_digest_env_var_disagreeing_with_its_own_image_is_a_violation(tmp_path, cluster, capsys):
    """Self-referential, so a legitimate upstream bump cannot trip it.

    Worth failing on because these values become the provenance of trait rows.
    """
    wf = _wf([_task("predictor")])
    _register(
        cluster,
        _tmpl(
            "predictor",
            image="ghcr.io/talmolab/sleap-roots-predict:sha-e025e309@sha256:4d4064c6",
            env={"SRP_PREDICT_CONTAINER_DIGEST": "sha256:ffffffff"},
        ),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    err = capsys.readouterr().err
    assert "sha256:ffffffff" in err and "sha256:4d4064c6" in err


def test_digest_env_var_agreeing_with_an_unrecorded_image_passes(tmp_path, cluster):
    wf = _wf([_task("trait-extractor")])
    _register(
        cluster,
        _tmpl(
            "trait-extractor",
            image="ghcr.io/talmolab/sleap-roots-trait-extractor:sha-999@sha256:ab5a1f43",
            env={"SRT_TRAITS_CONTAINER_DIGEST": "sha256:ab5a1f43"},
        ),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_OK


def test_all_violations_are_reported_not_just_the_first(tmp_path, cluster, capsys):
    """Two violations of different kinds in one run: one missing object, one bad inner."""
    wf = _wf([_task("predictor"), _task("write-back")])
    # predictor's object declares the wrong inner name; write-back is not registered at all.
    _register(cluster, _tmpl("predict", obj_name="sleap-roots-predictor-template"))
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    err = capsys.readouterr().err
    assert "WRONG INNER TEMPLATE" in err
    assert "sleap-roots-write-back-template" in err


def test_image_references_are_printed_on_the_violation_path_too(tmp_path, cluster, capsys):
    wf = _wf([_task("predictor")])
    _register(
        cluster,
        _tmpl(
            "predict",
            obj_name="sleap-roots-predictor-template",
            image="ghcr.io/example/img:sha-cafe123",
        ),
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_VIOLATION
    assert "sha-cafe123" in capsys.readouterr().out


# --------------------------------------------------------------------------------------
# Scenarios: could-not-check. Never reported as a violation, never as success.
# --------------------------------------------------------------------------------------


def test_unreachable_cluster_is_unavailable_not_drift(tmp_path, cluster, capsys):
    cluster["reachable"] = False
    wf = _wf([_task("predictor")])
    assert _run(tmp_path, wf, cluster) == mod.EXIT_UNAVAILABLE
    out = capsys.readouterr()
    assert "NOT REGISTERED" not in (out.out + out.err)


def test_a_kubectl_failure_other_than_notfound_is_unavailable(tmp_path, cluster):
    """Regression: the old `_fetch_live` mapped ANY non-zero kubectl exit to
    NOT REGISTERED + exit 1, fabricating a contract violation from a transient error."""
    wf = _wf([_task("predictor")])
    cluster["objects"]["sleap-roots-predictor-template"] = mod.CheckUnavailable(
        "Error from server (InternalError)"
    )
    assert _run(tmp_path, wf, cluster) == mod.EXIT_UNAVAILABLE


def test_a_kubectl_failure_other_than_notfound_does_not_report_missing(tmp_path, cluster, capsys):
    wf = _wf([_task("predictor")])
    cluster["objects"]["sleap-roots-predictor-template"] = mod.CheckUnavailable("boom")
    _run(tmp_path, wf, cluster)
    out = capsys.readouterr()
    assert "NOT REGISTERED" not in (out.out + out.err)


def test_unavailable_outranks_a_violation_in_the_same_run(tmp_path, cluster, capsys):
    """A partial check must never be reportable as a complete verdict."""
    # Both faults are cluster-side, so the expected-set guard passes and the run reaches the
    # per-template checks. Perturbing the vendored Workflow instead would trip that guard
    # first and this would silently stop testing precedence at all.
    wf = _wf([_task("predictor"), _task("write-back")])
    _register(cluster, _tmpl("predict", obj_name="sleap-roots-predictor-template"))
    cluster["objects"]["sleap-roots-write-back-template"] = mod.CheckUnavailable("boom")
    assert _run(tmp_path, wf, cluster) == mod.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    # `"predict" in err` would be satisfied by the object name alone; assert the label,
    # which appears nowhere on the could-not-check path.
    assert "WRONG INNER TEMPLATE" in err, "the violation must still be reported, not swallowed"
    assert "CHECK FAILED" in err


def test_kubectl_returning_success_with_non_workflowtemplate_output_is_unavailable(
    tmp_path, cluster
):
    wf = _wf([_task("predictor")])
    cluster["objects"]["sleap-roots-predictor-template"] = {"kind": "Status", "code": 504}
    assert _run(tmp_path, wf, cluster) == mod.EXIT_UNAVAILABLE


@pytest.mark.parametrize(
    "content",
    ["", "{{{ not yaml", "just a string", "spec: {}", "spec: {entrypoint: nope, templates: []}"],
    ids=["empty", "unparseable", "non-mapping", "no-entrypoint", "entrypoint-missing"],
)
def test_unusable_vendored_workflow_is_unavailable_not_a_violation(
    tmp_path, cluster, content
):
    """Uncaught, a yaml.YAMLError exits 1 and collides with the violation code."""
    path = tmp_path / "wf.yaml"
    path.write_text(content, encoding="utf-8")
    assert mod.check_contract(path, "ns") == mod.EXIT_UNAVAILABLE


def test_absent_vendored_workflow_is_unavailable(tmp_path, cluster):
    assert mod.check_contract(tmp_path / "nope.yaml", "ns") == mod.EXIT_UNAVAILABLE


def test_discovered_refs_not_equal_to_the_expected_set_is_unavailable(tmp_path, cluster, capsys):
    """Set equality, not a count compared against itself -- the latter can never fail."""
    wf = _wf([_task("predictor")])
    _register(cluster, _tmpl("predictor"))
    result = _run(tmp_path, wf, cluster, expected=_refs("predictor", "write-back"))
    assert result == mod.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "write-back" in err


def test_an_empty_dag_is_unavailable_not_success(tmp_path, cluster):
    wf = _wf([])
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_UNAVAILABLE


# --------------------------------------------------------------------------------------
# Structural discipline and the argv contract.
# --------------------------------------------------------------------------------------


def test_dag_is_resolved_through_the_entrypoint_not_templates_zero(tmp_path, cluster):
    """Indexing spec.templates[0] passes when a gateless template is prepended and the
    entrypoint repointed -- the bug `_dag_tasks` in test_k8s_client.py documents."""
    wf = _wf([_task("predictor")])
    wf["spec"]["templates"].insert(0, {"name": "decoy", "dag": {"tasks": []}})
    _register(cluster, _tmpl("predictor"))
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_OK


def test_duplicate_template_refs_are_checked_per_task(tmp_path, cluster, capsys):
    """Keyed by template name, the second task's requirements would overwrite the first's."""
    tasks = [
        _task("fan-a", params=["only-a"]),
        _task("fan-b", params=["only-b"]),
    ]
    for t in tasks:
        t["templateRef"] = {"name": "sleap-roots-shared-template", "template": "shared"}
    wf = _wf(tasks)
    shared = _tmpl("shared", inputs=[{"name": "only-a"}])
    shared["metadata"]["name"] = "sleap-roots-shared-template"
    _register(cluster, shared)
    result = _run(
        tmp_path, wf, cluster, expected=frozenset({("sleap-roots-shared-template", "shared")})
    )
    assert result == mod.EXIT_VIOLATION
    assert "only-b" in capsys.readouterr().err


def test_exit_codes_are_zero_one_two_and_distinct():
    assert (mod.EXIT_OK, mod.EXIT_VIOLATION, mod.EXIT_UNAVAILABLE) == (0, 1, 2)
    assert len({mod.EXIT_OK, mod.EXIT_VIOLATION, mod.EXIT_UNAVAILABLE}) == 3


def test_fetch_live_invokes_kubectl_with_the_expected_argv(monkeypatch):
    """Every other test mocks `_fetch_live`, so a typo in the real argv would ship."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, 0, stdout="kind: WorkflowTemplate\n", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    mod._fetch_live("sleap-roots-predictor-template", "runai-busch-lab")
    # Flags BEFORE `--`, positionals after. Verified against real kubectl 2026-09-22:
    # `get workflowtemplate -- <obj> -n ns -o yaml` silently ignores `-o yaml` and returns
    # the table form, because `--` terminates parsing for the later flags too.
    assert seen["argv"] == [
        "kubectl",
        "get",
        "-n",
        "runai-busch-lab",
        "-o",
        "yaml",
        "--",
        "workflowtemplate",
        "sleap-roots-predictor-template",
    ]
    assert seen["timeout"], "a wedged VPN must not hang the gate indefinitely"


def test_fetch_live_treats_a_missing_kubectl_binary_as_unavailable(monkeypatch):
    """kubectl lives in WSL, not Windows -- the likeliest workstation failure."""

    def fake_run(argv, **kwargs):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(mod.CheckUnavailable):
        mod._fetch_live("sleap-roots-predictor-template", "ns")


def test_fetch_live_treats_a_timeout_as_unavailable(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(mod.CheckUnavailable):
        mod._fetch_live("sleap-roots-predictor-template", "ns")


def test_fetch_live_returns_none_only_for_a_genuine_notfound(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr='Error from server (NotFound): workflowtemplates.argoproj.io "x" not found'
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    assert mod._fetch_live("x", "ns") is None


def test_fetch_live_raises_for_a_non_notfound_kubectl_error(monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1, stdout="", stderr="error: You must be logged in to the server (Unauthorized)"
        )

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(mod.CheckUnavailable):
        mod._fetch_live("x", "ns")


def _probe_stub(monkeypatch, *, returncode, stdout="", stderr=""):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return seen


def test_probe_cluster_invokes_kubectl_with_the_expected_argv(monkeypatch):
    """Every check_contract test patches _probe_cluster away, so a typo here would ship."""
    seen = _probe_stub(monkeypatch, returncode=0, stdout="workflowtemplate.argoproj.io/x\n")
    mod._probe_cluster("runai-busch-lab")
    assert seen["argv"] == [
        "kubectl",
        "get",
        "workflowtemplates",
        "-n",
        "runai-busch-lab",
        "-o",
        "name",
    ]
    assert seen["timeout"]


def test_probe_cluster_treats_forbidden_as_reachable(monkeypatch):
    """A kubeconfig may hold `get` without `list`; that is a checkable cluster."""
    _probe_stub(monkeypatch, returncode=1, stderr='workflowtemplates is Forbidden for user "x"')
    mod._probe_cluster("ns")  # must not raise


def test_probe_cluster_raises_when_the_cluster_is_unreachable(monkeypatch):
    _probe_stub(monkeypatch, returncode=1, stderr="Unable to connect to the server")
    with pytest.raises(mod.CheckUnavailable):
        mod._probe_cluster("ns")


def test_probe_cluster_rejects_a_namespace_holding_no_templates(monkeypatch):
    """A typo'd --namespace exits 0 with no output, and every get then NotFounds.

    Without this the operator's likeliest mistake produces five fabricated NOT REGISTERED
    violations and exit 1 on a perfectly healthy cluster.
    """
    _probe_stub(monkeypatch, returncode=0, stdout="   \n")
    with pytest.raises(mod.CheckUnavailable, match="wrong namespace"):
        mod._probe_cluster("runai-typo-lab")


def test_default_namespace_is_the_configured_dispatch_namespace():
    assert mod.DEFAULT_NAMESPACE == "runai-busch-lab"


def test_fetch_live_treats_success_with_an_empty_body_as_unavailable(monkeypatch):
    """`yaml.safe_load("")` is None — the same sentinel as NotFound, before this fix."""

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    with pytest.raises(mod.CheckUnavailable):
        mod._fetch_live("sleap-roots-predictor-template", "ns")


def test_an_inline_dag_task_is_unavailable_not_a_violation(tmp_path, cluster, capsys):
    """A task with no templateRef is invisible to the expected-set guard.

    Before the fix it reached `_fetch_live(None, ...)`, and `subprocess.run` rejects a None
    argv element with an uncaught TypeError — process exit 1, the violation code.
    """
    wf = _wf([_task("predictor"), {"name": "notify", "template": "local-notify"}])
    _register(cluster, _tmpl("predictor"))
    result = _run(tmp_path, wf, cluster, expected=_refs("predictor"))
    assert result == mod.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "notify" in err
    assert "NOT REGISTERED" not in err


@pytest.mark.parametrize(
    "bad_name",
    ["--server=http://127.0.0.1:9/", "--kubeconfig=/tmp/evil.yaml", "-n"],
)
def test_a_templateref_name_that_looks_like_a_flag_is_refused(tmp_path, cluster, bad_name):
    """Verified live 2026-09-22: kubectl honours `--server=...` as a flag and dials it.

    The vendored Workflow is copied from another GitHub org, so its object names are not
    automatically trustworthy. A name without a `template` is invisible to the expected-set
    guard, which is why this is checked at the point of use rather than only at discovery.
    """
    task = {"name": "evil", "templateRef": {"name": bad_name}}
    wf = _wf([_task("predictor"), task])
    _register(cluster, _tmpl("predictor"))
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_UNAVAILABLE


@pytest.mark.parametrize(
    "shape",
    [
        {"script": {"image": "img:1", "volumeMounts": [{"name": "nope"}]}},
        {"initContainers": [{"image": "img:1", "volumeMounts": [{"name": "nope"}]}]},
        {"sidecars": [{"image": "img:1", "volumeMounts": [{"name": "nope"}]}]},
    ],
    ids=["script", "initContainers", "sidecars"],
)
def test_non_container_template_shapes_are_still_inspected(tmp_path, cluster, shape):
    """Reaching only into `container` made three assertions vacuous on these shapes."""
    wf = _wf([_task("predictor")], volumes=["declared-only"])
    doc = _tmpl("predictor")
    doc["spec"]["templates"][0].pop("container")
    doc["spec"]["templates"][0].update(shape)
    _register(cluster, doc)
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_VIOLATION


def test_a_template_with_no_inspectable_container_is_unavailable(tmp_path, cluster, capsys):
    """An unrecognised shape must fail loud, not pass vacuously."""
    wf = _wf([_task("predictor")])
    doc = _tmpl("predictor")
    doc["spec"]["templates"][0].pop("container")
    _register(cluster, doc)
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_UNAVAILABLE
    assert "cannot verify" in capsys.readouterr().err


@pytest.mark.parametrize(
    "env,image",
    [
        (
            {"SRP_PREDICT_CONTAINER_DIGEST": None},
            "ghcr.io/x/predict:sha-1@sha256:aaaa",
        ),
        (
            {"SRP_PREDICT_CONTAINER_DIGEST": "sha256:bbbb"},
            "ghcr.io/x/bloomctl:sha-1",
        ),
    ],
    ids=["valueFrom-not-value", "image-not-digest-pinned"],
)
def test_digest_assertion_skips_what_it_cannot_justify(tmp_path, cluster, env, image):
    """Each of these was a false violation on a correct cluster before the narrowing."""
    wf = _wf([_task("predictor")])
    doc = _tmpl("predictor", image=image)
    entries = []
    for name, value in env.items():
        entry = {"name": name}
        if value is None:
            entry["valueFrom"] = {"configMapKeyRef": {"name": "c", "key": "k"}}
        else:
            entry["value"] = value
        entries.append(entry)
    doc["spec"]["templates"][0]["container"]["env"] = entries
    _register(cluster, doc)
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_OK


def test_two_digest_env_vars_are_not_a_violation(tmp_path, cluster):
    """Recording another image's digest for provenance chaining is legitimate."""
    wf = _wf([_task("write-back")])
    doc = _tmpl("write-back", image="ghcr.io/x/bloomctl:sha-1@sha256:cccc")
    doc["spec"]["templates"][0]["container"]["env"] = [
        {"name": "SRP_PREDICT_CONTAINER_DIGEST", "value": "sha256:aaaa"},
        {"name": "SRT_TRAITS_CONTAINER_DIGEST", "value": "sha256:bbbb"},
    ]
    _register(cluster, doc)
    assert _run(tmp_path, wf, cluster, expected=_refs("write-back")) == mod.EXIT_OK


@pytest.mark.parametrize(
    "mutate,ids",
    [
        (lambda wf: wf["spec"].update(volumes={"not": "a list"}), "volumes-mapping"),
        (
            lambda wf: wf["spec"].update(arguments={"parameters": [{"no": "name"}]}),
            "param-without-name",
        ),
    ],
)
def test_a_structurally_invalid_vendored_workflow_is_unavailable(tmp_path, cluster, mutate, ids):
    """A configuration error in this repo is not nine cluster contract violations."""
    wf = _wf([_task("predictor")], volumes=["v"])
    mutate(wf)
    _register(cluster, _tmpl("predictor"))
    assert _run(tmp_path, wf, cluster, expected=_refs("predictor")) == mod.EXIT_UNAVAILABLE


def test_the_module_makes_no_network_request_at_all():
    """bloom#879: every expectation comes from the vendored Workflow in this repo.

    The old comparator fetched five upstream files per run at the Workflow's pinned SHA;
    that coupling is the defect, so the import must be gone, not merely unused.
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("urllib", "requests", "httpx", "raw.githubusercontent.com"):
        assert forbidden not in source, f"{forbidden!r} must not appear in the comparator"


# --------------------------------------------------------------------------------------
# The captured real objects: the parser must find things where the API server puts them.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture,inner",
    [
        ("live_exit_gate_template.yaml", "exit-gate"),
        ("live_images_downloader_template.yaml", "images-downloader"),
        ("live_predictor_template.yaml", "predictor"),
    ],
)
def test_real_captured_templates_parse_to_the_expected_inner_template(fixture, inner):
    doc = yaml.safe_load((_FIXTURES / fixture).read_text(encoding="utf-8"))
    assert mod.inner_template(doc, inner) is not None


def test_real_exit_gate_declares_three_required_inputs():
    """If this ever changes upstream, the required-input assertion is what catches it."""
    doc = yaml.safe_load((_FIXTURES / "live_exit_gate_template.yaml").read_text(encoding="utf-8"))
    required = mod.required_inputs(mod.inner_template(doc, "exit-gate"))
    assert required == {"images-downloader-code", "predictor-code", "trait-extractor-code"}


def test_real_images_downloader_mounts_and_references_are_found():
    doc = yaml.safe_load(
        (_FIXTURES / "live_images_downloader_template.yaml").read_text(encoding="utf-8")
    )
    inner = mod.inner_template(doc, "images-downloader")
    assert mod.mount_names(inner) == {"images-input-dir", "bloom-credentials"}
    assert mod.workflow_parameter_refs(inner) == {"scan-ids"}


def test_real_predictor_digest_env_agrees_with_its_own_image():
    doc = yaml.safe_load((_FIXTURES / "live_predictor_template.yaml").read_text(encoding="utf-8"))
    inner = mod.inner_template(doc, "predictor")
    assert mod.digest_disagreements(inner) == []


def test_the_real_vendored_workflow_and_captured_templates_satisfy_the_contract(
    tmp_path, cluster
):
    """An end-to-end pass over real artifacts: the 2026-09-22 cluster shape against the
    committed vendored Workflow. This is the mocked stand-in for the live run."""
    wf = yaml.safe_load(VENDORED.read_text(encoding="utf-8"))
    for fixture in (
        "live_exit_gate_template.yaml",
        "live_images_downloader_template.yaml",
        "live_predictor_template.yaml",
    ):
        doc = yaml.safe_load((_FIXTURES / fixture).read_text(encoding="utf-8"))
        cluster["objects"][doc["metadata"]["name"]] = doc
    # The two not captured are asserted by the live run, not here.
    for name, inner in (("trait-extractor", "trait-extractor"), ("write-back", "write-back")):
        t = _tmpl(inner, mounts=["predictions-output-dir", "traits-output-dir"])
        cluster["objects"][f"sleap-roots-{name}-template"] = t

    path = tmp_path / "sleap-roots-pipeline.yaml"
    path.write_text(yaml.safe_dump(wf), encoding="utf-8")
    assert mod.check_contract(path, "runai-busch-lab") == mod.EXIT_OK

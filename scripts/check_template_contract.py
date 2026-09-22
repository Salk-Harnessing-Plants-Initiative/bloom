"""Verify the registered Argo `WorkflowTemplate`s satisfy the vendored Workflow's contract.

The vendored `sleap-roots-pipeline.yaml` resolves its five stages by `templateRef` against
objects that live only in the cluster and are applied by hand, separately from merging.
Nothing else in either repository compares those two things:

| check                                              | compares                                   |
| -------------------------------------------------- | ------------------------------------------ |
| `sleap-roots-pipeline/scripts/check_cluster_drift.sh` | cluster ↔ *upstream's* template files    |
| `sleap-roots-pipeline/scripts/check_manifests.py`  | upstream's files ↔ themselves (digests)    |
| `services/workflows/tests/test_k8s_client.py`      | vendored Workflow ↔ a constant in this repo |
| `scripts/check_vendored_workflow_drift.py`         | vendored file ↔ upstream Workflow at the pin |
| **this script**                                    | **vendored Workflow ↔ the cluster**        |

Why that gap matters: a `templateRef` naming a template that does not exist, or whose inner
`template:` name is wrong, **does not fail at submit**. Measured 2026-09-16 by server-side
dry-run — the Kubernetes API server accepts it (`created (server dry run)`, exit 0), because
resolution is the Argo controller's job and the dispatch worker POSTs to the raw K8s API
rather than the Argo Server. The batch's queue message is then deleted, the controller errors
the Workflow, the poller concludes `failed`, and every scan row is marked failed with no
requeue and no dead letter.

**This is not a drift check, and deliberately says nothing about drift** (bloom #879). An
earlier version compared the registered templates against upstream's template *files* at the
SHA pinned in `SLEAP_ROOTS_PIPELINE_REF`. That pin is the *Workflow*'s; the templates resolve
by `templateRef` at submit time precisely so they can advance independently of it. Asserting
that non-existent invariant reported DRIFT on all five correct templates from 2026-09-17
onward, and an operator who learns to wave DRIFT through will wave a real defect through with
it. Staleness is upstream's question and `check_cluster_drift.sh` answers it correctly.

So every expectation here is derived from the vendored Workflow in *this* repository, and this
script makes no network request at all. Per DAG task it asserts:

1. the `templateRef.name` `WorkflowTemplate` exists;
2. it declares an inner template whose `name` equals `templateRef.template`;
3. every parameter the task passes is declared in that template's `inputs.parameters`;
4. every input that template requires (no `default`, no `value`) is supplied, by the task or
   by the Workflow's own `spec.arguments.parameters`;
5. every `volumeMounts[].name` is declared in the Workflow's `spec.volumes`;
6. every `{{workflow.parameters.<name>}}` referenced is declared by the Workflow.

Each can produce the submit-accepted, controller-errored, whole-batch-failed outcome above.
(3) alone is vacuous for four of the five templates — only `exit-gate` passes parameters —
which is why (4), the direction that actually fires, matters more.

Image tags and digests are **not** compared against anything recorded in this repository;
that comparison is bloom #879. The one image property asserted is self-referential and so
cannot be disturbed by a legitimate upstream bump: a `*_CONTAINER_DIGEST` env var must equal
the digest on its own container's `image`. Those values become the provenance of the trait
rows the pipeline writes, and are stored without validation, so a disagreement durably
attributes trait data to an image that did not produce it.

Not asserted, by design: `timeout`, `retryStrategy`, `resources`, and whether a template is
stale relative to upstream. Those need an upstream reference, which is the coupling removed
here; upstream's two scripts cover them against the repo that owns them. Note both are manual
— `sleap-roots-pipeline` has no CI.

Needs cluster access. On this workstation `kubectl` and the kubeconfig live in WSL, not
Windows — see `sleap-roots-pipeline/.claude/skills/runai/SKILL.md` §1a.

Usage:  python scripts/check_template_contract.py [--namespace runai-busch-lab]
                                                  [--workflow PATH]
Exit:   0 = contract satisfied, 1 = contract violated, 2 = check could not be completed.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_WORKFLOW = (
    REPO_ROOT / "services" / "workflows" / "vendored" / "sleap-roots-pipeline.yaml"
)

# Mirrors `WORKFLOWS_K8S_NAMESPACE` in .env.staging.defaults / .env.prod.defaults. If those
# ever diverge per environment, pass --namespace rather than trusting this default.
DEFAULT_NAMESPACE = "runai-busch-lab"

KUBECTL_TIMEOUT_SECONDS = 30

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_UNAVAILABLE = 2

# The `templateRef` set the pipeline requires, as an independent anchor. Compared by set
# EQUALITY against what the vendored Workflow yields. An earlier draft compared the
# discovered count against "what the Workflow declares" — but that number *is* the parse
# result, so the guard was a tautology that could never fire: precisely the "failure mode is
# everything is fine" this script exists to avoid. Equality, not `>=`, so a restructured DAG
# fails loud and names both sets instead of passing quietly.
# `test_expected_refs_constant_matches_the_real_vendored_workflow` keeps the two in step.
EXPECTED_TEMPLATE_REFS = frozenset(
    {
        ("sleap-roots-images-downloader-template", "images-downloader"),
        ("sleap-roots-predictor-template", "predictor"),
        ("sleap-roots-trait-extractor-template", "trait-extractor"),
        ("sleap-roots-write-back-template", "write-back"),
        ("sleap-roots-exit-gate-template", "exit-gate"),
    }
)

_WORKFLOW_PARAM_REF = re.compile(r"\{\{\s*workflow\.parameters\.([A-Za-z0-9_.-]+)\s*\}\}")
_DIGEST_ENV_SUFFIX = "_CONTAINER_DIGEST"

# Kubernetes object names are DNS-1123 subdomains. Enforced before a name reaches `kubectl`
# as a positional argument: the `--` terminator already stops a leading dash being read as a
# flag, and this refuses the malformed name outright rather than asking the cluster about it.
_DNS1123 = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def _is_dns1123(value: object) -> bool:
    return isinstance(value, str) and bool(_DNS1123.match(value))


class CheckUnavailable(Exception):
    """The check could not be completed — never reported as a contract violation.

    A check whose failure mode is "everything is fine" is worse than no check, and one whose
    failure mode is "the cluster is broken" trains the operator to distrust real findings.
    Both are exit 2, distinct from both other outcomes.
    """


# ---------------------------------------------------------------------------------------
# Reading the vendored Workflow. Every expectation comes from here.
# ---------------------------------------------------------------------------------------


def load_workflow(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CheckUnavailable(f"cannot read {path}: {exc}") from exc
    try:
        doc = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CheckUnavailable(f"cannot parse {path}: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("spec"), dict):
        raise CheckUnavailable(f"{path} is not a Workflow mapping with a `spec`")

    # A structurally invalid vendored file is "could not check", not "the cluster is broken".
    # Without this, `spec.volumes` written as a mapping silently yields an empty set and every
    # mount in every template is reported as an undeclared volume — a configuration error in
    # this repo, dressed up as nine cluster contract violations.
    spec = doc["spec"]
    for key, container in (
        ("spec.volumes", spec.get("volumes")),
        ("spec.arguments.parameters", (spec.get("arguments") or {}).get("parameters")),
    ):
        if container is None:
            continue
        if not isinstance(container, list):
            raise CheckUnavailable(f"{path}: {key} is {type(container).__name__}, not a list")
        for entry in container:
            if not isinstance(entry, dict) or not entry.get("name"):
                raise CheckUnavailable(f"{path}: an entry in {key} has no `name`")
    return doc


def resolve_dag_tasks(workflow: dict) -> list[dict]:
    """Resolve the DAG *through* `spec.entrypoint`.

    Not `spec.templates[0]`: that indexing passes even when a second, gateless template is
    appended and the entrypoint repointed at it — the bug `_dag_tasks` in
    `services/workflows/tests/test_k8s_client.py` was written to forbid.
    """
    spec = workflow["spec"]
    entrypoint = spec.get("entrypoint")
    if not entrypoint:
        raise CheckUnavailable("the vendored Workflow declares no `spec.entrypoint`")
    templates = spec.get("templates")
    if not isinstance(templates, list):
        raise CheckUnavailable("the vendored Workflow declares no `spec.templates` list")
    for template in templates:
        if isinstance(template, dict) and template.get("name") == entrypoint:
            dag = template.get("dag")
            if not isinstance(dag, dict) or not isinstance(dag.get("tasks"), list):
                raise CheckUnavailable(
                    f"entrypoint template {entrypoint!r} has no `dag.tasks` list"
                )
            return dag["tasks"]
    raise CheckUnavailable(f"no template named {entrypoint!r} to match `spec.entrypoint`")


def discover_template_refs(workflow: dict) -> frozenset[tuple[str, str]]:
    refs = set()
    for task in resolve_dag_tasks(workflow):
        ref = task.get("templateRef")
        if isinstance(ref, dict) and ref.get("name") and ref.get("template"):
            refs.add((ref["name"], ref["template"]))
    return frozenset(refs)


def _named(entries) -> set[str]:
    if not isinstance(entries, list):
        return set()
    return {e["name"] for e in entries if isinstance(e, dict) and e.get("name")}


def task_parameters(task: dict) -> set[str]:
    """Names the task passes. Deliberately NOT including workflow-level globals.

    Folding `spec.arguments.parameters` in here would report a violation on today's correct
    cluster, because `images-downloader`'s template declares no inputs at all while the
    Workflow declares the `scan-ids` global — bloom #879 in a new costume.
    """
    return _named((task.get("arguments") or {}).get("parameters"))


def workflow_globals(workflow: dict) -> set[str]:
    return _named((workflow["spec"].get("arguments") or {}).get("parameters"))


def workflow_volumes(workflow: dict) -> set[str]:
    return _named(workflow["spec"].get("volumes"))


# ---------------------------------------------------------------------------------------
# Reading a registered WorkflowTemplate. Note the API server stores `inputs: {}` —
# present but empty — on the four templates that declare none, so every accessor below
# tolerates absent, `{}` and `{parameters: []}` alike.
# ---------------------------------------------------------------------------------------


def inner_template(document: dict, name: str) -> dict | None:
    for template in (document.get("spec") or {}).get("templates") or []:
        if isinstance(template, dict) and template.get("name") == name:
            return template
    return None


def declared_inputs(template: dict) -> set[str]:
    return _named((template.get("inputs") or {}).get("parameters"))


def required_inputs(template: dict) -> set[str]:
    """Declared inputs the caller must supply: no `default`, no `value`, no `valueFrom`.

    All three of `exit-gate`'s are of this kind, so an upstream template gaining a fourth is
    an accepted submission that the controller then fails to resolve, at the DAG's only leaf,
    after `write-back` has already committed trait rows.

    `valueFrom` counts as supplied. Argo's own resolution treats an input as unsatisfied only
    when `Value == nil && ValueFrom == nil`, so an input drawing from a `configMapKeyRef` (or
    `supplied`, on a suspend template) needs no argument from the caller. Omitting that check
    would raise `MISSING PARAMETER` on a correct cluster — a false alarm in a gate whose whole
    thesis is that false alarms train an operator to wave real ones through.
    """
    entries = (template.get("inputs") or {}).get("parameters")
    if not isinstance(entries, list):
        return set()
    return {
        e["name"]
        for e in entries
        if isinstance(e, dict)
        and e.get("name")
        and "default" not in e
        and "value" not in e
        and "valueFrom" not in e
    }


def _containers(template: dict) -> list[dict]:
    """Every container-shaped member of an inner template.

    `script:` is a Container plus `source`, and `initContainers`/`sidecars` carry their own
    `volumeMounts` and `image`. Reaching only into `template["container"]` would make the
    volume, image and digest assertions silently vacuous the moment upstream converts a stage
    to a `script:` template — three checks turning into no-ops while the comparator still
    prints OK. `_assert_inspectable` refuses that outcome outright.
    """
    found = [template.get("container"), template.get("script")]
    for key in ("initContainers", "sidecars"):
        found.extend(template.get(key) or [])
    return [c for c in found if isinstance(c, dict)]


def mount_names(template: dict) -> set[str]:
    names: set[str] = set()
    for container in _containers(template):
        names |= _named(container.get("volumeMounts"))
    return names


def workflow_parameter_refs(template: dict) -> set[str]:
    rendered = yaml.safe_dump(template, default_flow_style=False)
    return set(_WORKFLOW_PARAM_REF.findall(rendered))


def container_images(document: dict) -> list[tuple[str, str]]:
    found = []
    for template in (document.get("spec") or {}).get("templates") or []:
        if not isinstance(template, dict):
            continue
        for container in _containers(template):
            image = container.get("image")
            if image:
                found.append((template.get("name", "?"), image))
    return found


def _assert_inspectable(template: dict, label: str) -> None:
    """Refuse to pass an inner template whose shape this comparator cannot read.

    An unrecognised shape must not read as a clean pass — that is the "failure mode is
    everything is fine" this whole module exists to outlaw, and it would be especially
    galling here because the template would not even appear in the printed image list.
    """
    if not _containers(template):
        raise CheckUnavailable(
            f"{label} declares no container, script, initContainers or sidecars — "
            "this comparator cannot verify its volumes or images"
        )


def digest_disagreements(template: dict) -> list[tuple[str, str, str]]:
    """`*_CONTAINER_DIGEST` env vars that disagree with their own container's image digest.

    Self-referential on purpose: both sides move together on a legitimate upstream bump, so
    this cannot reproduce bloom #879, unlike comparing against a recorded pin.

    Deliberately narrow, because every broadening of it is a way to cry wolf on a correct
    cluster. A container is compared only when all three hold:

    * the env entry carries a literal `value` — a `valueFrom` digest is unknowable here, and
      reading it as the empty string would report a mismatch against a correct pin;
    * its `image` is digest-pinned — with no `@sha256:` there is nothing to compare against,
      and three of the five templates run a tag-pinned `bloomctl` today;
    * exactly one such variable is present — two means the template is recording some *other*
      image's digest for provenance chaining, which is a legitimate thing for a write-back
      stage to do and which no bloom-side change could ever satisfy.

    Anything skipped is reported by the caller as an advisory, never as a violation.
    """
    problems = []
    for container in _containers(template):
        image = container.get("image") or ""
        image_digest = image.partition("@")[2]
        digest_vars = [
            e
            for e in container.get("env") or []
            if isinstance(e, dict) and (e.get("name") or "").endswith(_DIGEST_ENV_SUFFIX)
        ]
        if len(digest_vars) != 1 or not image_digest:
            continue
        entry = digest_vars[0]
        if "value" not in entry:
            continue
        declared = str(entry.get("value") or "")
        if declared != image_digest:
            problems.append((entry["name"], declared, image_digest))
    return problems


# ---------------------------------------------------------------------------------------
# Talking to the cluster. Only a genuine NotFound is a missing template; every other
# failure is a check that could not be completed.
# ---------------------------------------------------------------------------------------


def _kubectl(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["kubectl", *args],
            capture_output=True,
            text=True,
            timeout=KUBECTL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        # The likeliest workstation failure: kubectl lives in WSL, not Windows.
        raise CheckUnavailable("`kubectl` is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CheckUnavailable(
            f"`kubectl {' '.join(args)}` timed out after {KUBECTL_TIMEOUT_SECONDS}s"
        ) from exc


def _probe_cluster(namespace: str) -> None:
    """Fail fast and unambiguously when the cluster is not reachable, or is the wrong one.

    A `Forbidden` is treated as reachable: a kubeconfig may hold `get` without `list`, and
    reporting could-not-check on a perfectly checkable cluster is its own false alarm.

    The listing is *kept*, not discarded. `kubectl get` against a namespace that does not
    exist — or the wrong one — exits 0 with no output, so a mistyped `--namespace` would
    otherwise sail past this probe and turn every subsequent `NotFound` into a fabricated
    `NOT REGISTERED`: five contract violations, exit 1, on a perfectly healthy cluster. That
    is the same fabrication this comparator was rewritten to eliminate, one layer out, and
    it is the operator's single most likely mistake.
    """
    proc = _kubectl(["get", "workflowtemplates", "-n", namespace, "-o", "name"])
    if "Forbidden" in proc.stderr:
        return
    if proc.returncode != 0:
        raise CheckUnavailable(
            f"cannot list workflowtemplates in {namespace} "
            f"(VPN down, or wrong KUBECONFIG): {proc.stderr.strip()}"
        )
    if not proc.stdout.strip():
        raise CheckUnavailable(
            f"namespace {namespace!r} holds no WorkflowTemplates at all — wrong namespace, "
            "or wrong kubeconfig context? Refusing to report five missing templates."
        )


def _fetch_live(obj: str, namespace: str) -> dict | None:
    """Return the stored object, or None if it genuinely does not exist.

    The version this replaced returned None on *any* non-zero exit, so an unreachable
    cluster was reported as five missing templates and exit 1 — a fabricated contract
    violation. Only a `NotFound` means absent; in particular a success with an empty body
    is *not* absence, and must not reuse the same sentinel.

    `--` terminates the flag list, so the positional arguments follow it and every flag
    precedes it. Measured 2026-09-22: `get workflowtemplate -- <obj> -n ns -o yaml` silently
    ignores `-o yaml` and returns the table form, because `--` swallows the later flags too.
    The object name comes from the vendored `Workflow`, which is copied from another GitHub
    org, and without the terminator `kubectl` reads a leading dash as a flag — verified live,
    a name of `--server=http://127.0.0.1:9/` was honoured and dialled. `--kubeconfig=`,
    `--token=` and `--as=` are reachable the same way, which would send the operator's bearer
    token somewhere the file chooses. `obj` is shape-checked by the caller as well.
    """
    proc = _kubectl(["get", "-n", namespace, "-o", "yaml", "--", "workflowtemplate", obj])
    if proc.returncode != 0:
        if "NotFound" in proc.stderr:
            return None
        raise CheckUnavailable(f"`kubectl get {obj}` failed: {proc.stderr.strip()}")
    try:
        document = yaml.safe_load(proc.stdout)
    except yaml.YAMLError as exc:
        raise CheckUnavailable(f"`kubectl get {obj}` returned unparseable YAML: {exc}") from exc
    if document is None:
        raise CheckUnavailable(f"`kubectl get {obj}` exited 0 but returned no object")
    return document


# ---------------------------------------------------------------------------------------
# The check itself.
# ---------------------------------------------------------------------------------------


def check_contract(
    workflow_path: Path,
    namespace: str = DEFAULT_NAMESPACE,
    expected_refs: frozenset[tuple[str, str]] | None = None,
) -> int:
    expected = EXPECTED_TEMPLATE_REFS if expected_refs is None else expected_refs

    try:
        workflow = load_workflow(Path(workflow_path))
        tasks = resolve_dag_tasks(workflow)
        discovered = discover_template_refs(workflow)
    except CheckUnavailable as exc:
        print(f"CHECK FAILED  {exc}", file=sys.stderr)
        _summarise(EXIT_UNAVAILABLE)
        return EXIT_UNAVAILABLE

    if discovered != expected:
        print(
            "CHECK FAILED  the vendored Workflow's templateRefs are not the expected set; "
            "refusing to report a verdict",
            file=sys.stderr,
        )
        for label, values in (("expected", expected), ("discovered", discovered)):
            for name, inner in sorted(values):
                print(f"    {label:<10} {name} / {inner}", file=sys.stderr)
        _summarise(EXIT_UNAVAILABLE)
        return EXIT_UNAVAILABLE

    globals_ = workflow_globals(workflow)
    volumes = workflow_volumes(workflow)

    try:
        _probe_cluster(namespace)
    except CheckUnavailable as exc:
        print(f"CHECK FAILED  {exc}", file=sys.stderr)
        _summarise(EXIT_UNAVAILABLE)
        return EXIT_UNAVAILABLE

    print(f"Checking {namespace} against {workflow_path}\n")

    violations: list[str] = []
    unavailable: list[str] = []

    for task in tasks:
        if not isinstance(task, dict):
            unavailable.append(f"a DAG task is {type(task).__name__}, not a mapping")
            continue
        ref = task.get("templateRef") or {}
        obj = ref.get("name") if isinstance(ref, dict) else None
        inner_name = ref.get("template") if isinstance(ref, dict) else None
        label = f"{obj} / {inner_name}"

        # A task with no resolvable `templateRef` — an ordinary inline `template:` step, an
        # onExit handler — is invisible to the expected-set guard, because discovery only
        # collects well-formed refs. Without this it reached `_fetch_live(None, ...)`, and
        # `subprocess.run` rejects a None argv element with an uncaught TypeError, exiting 1:
        # the violation code, for something that is not a violation, discarding every result
        # already collected. Exactly the collision this comparator was rewritten to remove.
        if not _is_dns1123(obj) or not isinstance(inner_name, str) or not inner_name:
            unavailable.append(
                f"task {task.get('name')!r} has no resolvable templateRef "
                f"(name={obj!r}, template={inner_name!r}) — this comparator only checks "
                "tasks that reference a WorkflowTemplate"
            )
            continue

        try:
            document = _fetch_live(obj, namespace)
        except CheckUnavailable as exc:
            unavailable.append(f"{label}: {exc}")
            continue

        if document is None:
            violations.append(f"NOT REGISTERED      {obj} (referenced by task {task.get('name')!r})")
            continue
        if not isinstance(document, dict) or document.get("kind") != "WorkflowTemplate":
            unavailable.append(f"{label}: kubectl returned something other than a WorkflowTemplate")
            continue

        # Informational, on every path: a tag can be re-pushed underneath a template, and
        # the images-downloader pin in particular determines whether exit 3 is emitted at
        # all. Printed, never compared against a recorded pin.
        for inner_label, image in container_images(document):
            print(f"  {obj:<42} template={inner_label} image={image}")
            if ":" in image and "@" not in image:
                policy = (inner_template(document, inner_label) or {}).get("container", {})
                if policy.get("imagePullPolicy") == "IfNotPresent":
                    print(
                        f"  {'':<42} advisory: tag-pinned with IfNotPresent "
                        "(talmolab/sleap-roots-pipeline#72)"
                    )

        template = inner_template(document, inner_name)
        if template is None:
            declared = [t.get("name") for t in (document.get("spec") or {}).get("templates") or []]
            violations.append(
                f"WRONG INNER TEMPLATE {obj} declares no template named {inner_name!r} "
                f"(it declares {declared}) — this would be ACCEPTED at submit and fail in the controller"
            )
            continue

        try:
            _assert_inspectable(template, label)
        except CheckUnavailable as exc:
            unavailable.append(str(exc))
            continue

        passed = task_parameters(task)
        declared_names = declared_inputs(template)

        for name in sorted(passed - declared_names):
            violations.append(
                f"UNDECLARED PARAMETER {label} is passed {name!r}, which it does not declare"
            )
        # NOT `- globals_`. A workflow-level `spec.arguments.parameters` entry is bound to the
        # ENTRYPOINT template's inputs and is otherwise available only for
        # `{{workflow.parameters.*}}` substitution; it is not auto-supplied to a template
        # invoked through a DAG task's `templateRef`, which must pass its callee's
        # non-defaulted inputs itself or the controller fails with
        # `inputs.parameters.<name> was not supplied`. Subtracting the globals would exempt
        # any required input whose name merely collided with a global — silently disabling
        # the one assertion this design argues matters most. Inert today (`scan-ids` is the
        # only global and no template requires an input by that name), which is exactly why
        # it is cheap to get right now.
        for name in sorted(required_inputs(template) - passed):
            violations.append(
                f"MISSING PARAMETER    {label} requires {name!r} (no default) and nothing supplies it"
            )
        for name in sorted(mount_names(template) - volumes):
            violations.append(
                f"UNDECLARED VOLUME    {label} mounts {name!r}, absent from the Workflow's spec.volumes"
            )
        for name in sorted(workflow_parameter_refs(template) - globals_):
            violations.append(
                f"UNRESOLVABLE REF     {label} references "
                f"{{{{workflow.parameters.{name}}}}}, which the Workflow does not declare"
            )
        for env_name, declared_digest, image_digest in digest_disagreements(template):
            violations.append(
                f"PROVENANCE MISMATCH  {label} env {env_name}={declared_digest} disagrees with "
                f"its own image digest {image_digest} — trait rows would record the wrong image"
            )

    print()
    for line in violations:
        print(f"CONTRACT VIOLATION  {line}", file=sys.stderr)
    for line in unavailable:
        print(f"CHECK FAILED        {line}", file=sys.stderr)

    # A partial check must never be reportable as a complete verdict, so an unchecked
    # condition outranks a violation — while still printing the violation above.
    if unavailable:
        _summarise(EXIT_UNAVAILABLE)
        return EXIT_UNAVAILABLE
    if violations:
        _summarise(EXIT_VIOLATION)
        return EXIT_VIOLATION
    _summarise(EXIT_OK)
    return EXIT_OK


def _summarise(code: int) -> None:
    if code == EXIT_OK:
        print("\nOK: every registered template satisfies the vendored Workflow's contract.")
    elif code == EXIT_VIOLATION:
        print(
            "\nCONTRACT VIOLATION: a dispatch against this cluster would be ACCEPTED by the "
            "API server and then fail in the controller, marking every scan in the batch "
            "failed with no requeue. This is not drift — see `check_cluster_drift.sh` in "
            "sleap-roots-pipeline for that question.",
            file=sys.stderr,
        )
    else:
        print(
            "\nCHECK FAILED: this is NOT a clean result — do not treat it as one. Nothing "
            "above establishes that the contract holds.",
            file=sys.stderr,
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument(
        "--workflow",
        type=Path,
        default=VENDORED_WORKFLOW,
        help="vendored Workflow to derive the contract from (default: the committed one)",
    )
    args = parser.parse_args()
    try:
        return check_contract(args.workflow, args.namespace)
    except Exception as exc:  # noqa: BLE001 — see below
        # Deliberately broad, and the narrow alternative is worse. An uncaught exception
        # leaves the interpreter exiting 1, which this script defines as "contract violated"
        # — so any unanticipated input shape would masquerade as a real defect and send an
        # operator hunting a cluster problem that does not exist. Whatever escapes, the
        # honest report is "the check did not complete".
        print(f"CHECK FAILED  unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        _summarise(EXIT_UNAVAILABLE)
        return EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())

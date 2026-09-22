"""Compare the five registered Argo `WorkflowTemplate`s against the pinned upstream.

The vendored `sleap-roots-pipeline.yaml` resolves its five stages by `templateRef`
against objects that live only in the cluster — this repo neither vendors nor
drift-checks them. `check_vendored_workflow_drift.py` covers the Workflow; this
covers the templates it points at.

Why it matters more than it looks: a `templateRef` naming a template that does
not exist, or whose inner `template:` name is wrong, **does not fail at submit**.
Measured 2026-09-16 by server-side dry-run — the Kubernetes API server accepts it
(`created (server dry run)`, exit 0), because resolution is the Argo controller's
job and the dispatch worker POSTs to the raw K8s API rather than the Argo Server.
The batch's queue message is then deleted, the controller errors the Workflow, the
poller concludes `failed`, and every scan row is marked failed with no requeue and
no dead letter. So this check is the only thing standing between a registration
defect and that outcome.

Run it, rather than eyeballing `kubectl get -o yaml`: the API server defaults a
handful of empty keys onto every stored object and canonicalises CPU quantities,
so a naive comparison reports DRIFT on templates that are perfectly in sync —
and an operator who learns to wave that through will wave a real defect through
with it.

Needs cluster access. On this workstation `kubectl` and the kubeconfig live in
WSL, not Windows — see `sleap-roots-pipeline/.claude/skills/runai/SKILL.md` §1a.

Usage:  python scripts/check_registered_templates.py [--namespace runai-busch-lab]
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REF_FILE = (
    REPO_ROOT / "services" / "workflows" / "vendored" / "SLEAP_ROOTS_PIPELINE_REF"
)
RAW_URL = (
    "https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/{sha}/{obj}.yaml"
)
STAGES = (
    "images-downloader",
    "predictor",
    "trait-extractor",
    "write-back",
    "exit-gate",
)

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_UNAVAILABLE = 2

# Keys the API server defaults onto a stored object that are absent from the
# pinned source. Measured against all five templates, 2026-09-16. Dropping an
# empty value is safe in a way dropping a populated one would not be: each is
# removed only when it is falsy, so a template that genuinely declares
# `inputs` (exit-gate does) still has them compared.
_DEFAULTED_EMPTY = ("arguments", "inputs", "outputs", "metadata")


def _strip_server_defaults(node):
    """Recursively drop empty keys the API server injects, and canonicalise CPU.

    Kubernetes stores `cpu: "0.5"` as `"500m"`; both mean half a core, and the
    pinned source uses the former. Normalising here rather than telling an
    operator to ignore it by eye is the whole point of this script.
    """
    if isinstance(node, list):
        return [_strip_server_defaults(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in _DEFAULTED_EMPTY and not value:
            continue
        if key == "name" and value == "":
            continue  # container.name, defaulted empty
        if key == "cpu" and isinstance(value, str):
            try:
                out[key] = f"{int(round(float(value) * 1000))}m"
                continue
            except ValueError:
                pass
        out[key] = _strip_server_defaults(value)
    return out


def _fetch_pinned(sha: str, obj: str) -> dict:
    url = RAW_URL.format(sha=sha, obj=obj)
    with urllib.request.urlopen(url, timeout=15) as resp:
        return yaml.safe_load(resp.read())


def _fetch_live(obj: str, namespace: str) -> dict | None:
    proc = subprocess.run(
        ["kubectl", "get", "workflowtemplate", obj, "-n", namespace, "-o", "yaml"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return yaml.safe_load(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="runai-busch-lab")
    args = parser.parse_args()

    sha = REF_FILE.read_text(encoding="utf-8").strip()
    print(f"Comparing {args.namespace} against pinned {sha}\n")

    drift = False
    for stage in STAGES:
        obj = f"sleap-roots-{stage}-template"
        try:
            pinned = _strip_server_defaults(_fetch_pinned(sha, obj)["spec"])
        except (urllib.error.URLError, OSError, KeyError, TypeError) as exc:
            print(f"{obj:<42} UPSTREAM FETCH FAILED: {exc}", file=sys.stderr)
            return EXIT_UNAVAILABLE

        live_obj = _fetch_live(obj, args.namespace)
        if live_obj is None:
            print(f"{obj:<42} NOT REGISTERED", file=sys.stderr)
            drift = True
            continue
        live = _strip_server_defaults(live_obj["spec"])

        # Report the image pin regardless of drift: a template can match the pin
        # while running an image whose tag has been re-pushed underneath it.
        for tmpl in live_obj["spec"].get("templates", []):
            image = (tmpl.get("container") or {}).get("image")
            if image:
                print(f"{obj:<42} template={tmpl.get('name')} image={image}")

        if pinned == live:
            print(f"{obj:<42} IN SYNC\n")
        else:
            drift = True
            print(f"{obj:<42} DRIFT", file=sys.stderr)
            a = json.dumps(pinned, indent=1, sort_keys=True).splitlines()
            b = json.dumps(live, indent=1, sort_keys=True).splitlines()
            for line in difflib.unified_diff(a, b, "pinned", "live", lineterm="", n=1):
                print(f"    {line}", file=sys.stderr)
            print(file=sys.stderr)

    if drift:
        print(
            "DRIFT: at least one registered template does not match the pin. A "
            "dispatch against it will be accepted by the API server and then fail "
            "in the controller, marking every scan in the batch failed with no "
            "requeue.",
            file=sys.stderr,
        )
        return EXIT_DRIFT
    print("OK: all five registered templates match the pin.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

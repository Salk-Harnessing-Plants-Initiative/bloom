"""Generate the golden vectors for cyl_trait_recipe_key_v1 (add-cyl-trait-recipe-key).

Each vector is a contracts-valid Provenance, stored as the raw JSON text pydantic
emits (so an integer 1 and a float 1.0 stay distinct; no JSON parser in the test
path may rewrite them). Its ``partition`` is the contracts idempotency key computed
with the three per-scan inputs held fixed, so two vectors share a partition exactly
when contracts would call them the same computation. Bloom's recipe key must
partition the vectors the same way, except inside a ``divergence_group``.

Not collected by pytest (only test_*.py is). Run from bloomcli, whose lockfile pins
sleap-roots-contracts 0.1.0a9:

    cd bloomcli && uv run --frozen python ../tests/integration/fixtures/gen_recipe_key_vectors.py
    cd bloomcli && uv run --frozen python ../tests/integration/fixtures/gen_recipe_key_vectors.py --check
"""

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from sleap_roots_contracts.identity import compute_idempotency_key
from sleap_roots_contracts.models import (
    InputRef,
    ModelRef,
    Provenance,
    ResolvedParams,
)

CONTRACTS_VERSION = "0.1.0a9"
OUT = Path(__file__).with_name("recipe_key_v1_vectors.json")

PRIMARY = ModelRef(
    registry_id="org/registry/canola-primary",
    version="v0",
    sleap_nn_version="0.3.0",
    root_type="primary",
    weights_checksum="a6b27968b01c7640335359d2539a8ae3",
)
LATERAL = ModelRef(
    registry_id="org/registry/canola-lateral",
    version="v0",
    sleap_nn_version="0.3.0",
    root_type="lateral",
    weights_checksum="75e1b1e7671e0b0c01176165ed6f4076",
)


def _prov(**overrides) -> Provenance:
    fields = dict(
        contract_version=CONTRACTS_VERSION,
        scan_key="scan_1",
        inputs=InputRef(image_ids=["1", "2"], images_checksum="sha256:aa"),
        predict_models=[PRIMARY, LATERAL],
        predict_container_digest="sha256:predict",
        predict_code_sha="9a6f20c00762e327b9aea39c8e06a81443252d01",
        predict_output_params={"peak_threshold": 0.2},
        predict_inference_config={"device": "cpu", "batch_size": 4},
        traits_sleap_roots_version="0.1.4",
        traits_container_digest="sha256:traits",
        traits_code_sha="e373b0f92639ce13feb90d619b9fd14172ce296d",
        params=ResolvedParams(
            values={"species": "canola", "mode": "cylinder", "age": 7}
        ),
    )
    fields.update(overrides)
    return Provenance(**fields)


def _partition(p: Provenance) -> str:
    return compute_idempotency_key(
        scan_key="X",
        images_checksum="X",
        param_hash="X",
        models=[
            (m.registry_id, m.version, m.weights_checksum) for m in p.predict_models
        ],
        predict_code_sha=p.predict_code_sha,
        traits_code_sha=p.traits_code_sha,
        predict_output_params=p.predict_output_params,
    )


def _cases() -> list[tuple[str, Provenance, str | None]]:
    """(name, provenance, divergence_group)."""
    return [
        ("base", _prov(), None),
        (
            "other_scan_same_recipe",
            _prov(
                scan_key="scan_2",
                inputs=InputRef(image_ids=["3"], images_checksum="sha256:bb"),
                params=ResolvedParams(
                    values={"species": "canola", "mode": "cylinder", "age": 9}
                ),
                pipeline_run_id="wf-abc",
                argo_workflow_uid="uid-1",
                argo_node_id="node-1",
                worker_request_id="req-1",
                predict_container_digest="",
                traits_container_digest="",
                predict_inference_config={"device": "cuda", "batch_size": 16},
                traits_sleap_roots_version="0.1.5",
            ),
            None,
        ),
        ("models_reordered", _prov(predict_models=[LATERAL, PRIMARY]), None),
        (
            "root_type_changed",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(update={"root_type": "crown"}),
                    LATERAL,
                ]
            ),
            None,
        ),
        (
            "sleap_nn_version_changed",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(update={"sleap_nn_version": "0.4.0"}),
                    LATERAL,
                ]
            ),
            None,
        ),
        (
            "duplicate_triple",
            _prov(
                predict_models=[
                    PRIMARY,
                    PRIMARY.model_copy(update={"root_type": "lateral"}),
                ]
            ),
            None,
        ),
        ("single_primary", _prov(predict_models=[PRIMARY]), None),
        (
            "registry_id_changed",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(
                        update={"registry_id": "org/registry/rice-primary"}
                    ),
                    LATERAL,
                ]
            ),
            None,
        ),
        (
            "version_changed",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(update={"version": "v1"}),
                    LATERAL,
                ]
            ),
            None,
        ),
        (
            "checksum_none",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(update={"weights_checksum": None}),
                    LATERAL,
                ]
            ),
            None,
        ),
        (
            "checksum_empty",
            _prov(
                predict_models=[
                    PRIMARY.model_copy(update={"weights_checksum": ""}),
                    LATERAL,
                ]
            ),
            None,
        ),
        ("predict_sha_changed", _prov(predict_code_sha="4a70e599"), None),
        ("traits_sha_changed", _prov(traits_code_sha="689cffb8"), None),
        ("output_params_none", _prov(predict_output_params=None), None),
        ("output_params_empty", _prov(predict_output_params={}), None),
        (
            "output_params_changed",
            _prov(predict_output_params={"peak_threshold": 0.3}),
            None,
        ),
        ("no_models", _prov(predict_models=[]), None),
        (
            "output_params_int",
            _prov(predict_output_params={"peak_threshold": 1}),
            "int-vs-float",
        ),
        (
            "output_params_float",
            _prov(predict_output_params={"peak_threshold": 1.0}),
            "int-vs-float",
        ),
    ]


def build() -> str:
    installed = importlib.metadata.version("sleap-roots-contracts")
    if installed != CONTRACTS_VERSION:
        sys.exit(
            f"sleap-roots-contracts {installed} installed; vectors need {CONTRACTS_VERSION}"
        )
    vectors = [
        {
            "name": name,
            "raw_provenance": p.model_dump_json(),
            "partition": _partition(p),
            "divergence_group": group,
        }
        for name, p, group in _cases()
    ]
    doc = {
        "contracts_version": CONTRACTS_VERSION,
        "provenance_fields": sorted(Provenance.model_fields),
        "model_ref_fields": sorted(ModelRef.model_fields),
        "vectors": vectors,
    }
    return json.dumps(doc, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="fail if the file would change"
    )
    args = parser.parse_args()
    text = build()
    if args.check:
        current = json.loads(OUT.read_text(encoding="utf-8"))
        if current != json.loads(text):
            print(f"{OUT.name} is stale; regenerate it", file=sys.stderr)
            return 1
        print(f"{OUT.name} is current")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(json.loads(text)['vectors'])} vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

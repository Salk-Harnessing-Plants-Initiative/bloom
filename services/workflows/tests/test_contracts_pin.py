"""The sleap-roots-contracts version this service needs.

`compute_param_hash` feeds the trigger's dedup preview, so its value must not move
when the pin does. `ModelCard` with `selectors` (a8+) is what `GET /model-cards`
validates production cards against.
"""

from sleap_roots_contracts import compute_param_hash

# compute_param_hash({"species": "canola", "mode": "cylinder", "age": 2}) at 0.1.0a5.
CANOLA_CYLINDER_DAY_2_HASH = (
    "0ad8b8668ff7cbf6ccfa78afb079645fe8fedcb75339729b02b86ff2b3b5f462"
)


def test_param_hash_is_stable_across_the_contracts_pin():
    assert (
        compute_param_hash({"species": "canola", "mode": "cylinder", "age": 2})
        == CANOLA_CYLINDER_DAY_2_HASH
    )


def test_model_card_has_selectors():
    # Imported here, not at module level, so the hash test above still collects on a
    # contracts release that predates Selector.
    from sleap_roots_contracts import ModelCard, Selector

    card = ModelCard.model_validate(
        {
            "root_type": "lateral",
            "selectors": [
                {
                    "species": "arabidopsis",
                    "mode": "cylinder",
                    "age_min": 2,
                    "age_max": 14,
                }
            ],
            "registry_id": "org/registry/arabidopsis-lateral",
            "version": "v0",
        }
    )

    assert card.selectors == (
        Selector(species="arabidopsis", mode="cylinder", age_min=2, age_max=14),
    )

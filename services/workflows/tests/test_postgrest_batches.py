"""Unit tests for postgrest_batches.id_batches, the trigger's copy of bloomctl's id-filter
batching (bloom#901)."""

import ast
from pathlib import Path

import postgrest_batches
from postgrest_batches import ID_FILTER_BUDGET_CHARS, id_batches

BLOOMCTL_POSTGREST = (
    Path(__file__).resolve().parents[3]
    / "bloomcli"
    / "src"
    / "bloomctl"
    / "_postgrest.py"
)


def _rendered(batch):
    """The id list exactly as it appears inside `in.(…)`."""
    return ",".join(str(v) for v in batch)


def test_empty_input_gives_no_batches():
    assert id_batches([]) == []


def test_every_batch_renders_within_the_budget_counting_separators():
    ids = list(range(1000, 4000))  # 3000 four-digit ids: 14,999 rendered characters
    batches = id_batches(ids)
    assert len(batches) > 1
    for batch in batches:
        assert len(_rendered(batch)) <= ID_FILTER_BUDGET_CHARS


def test_a_small_budget_is_respected_too():
    batches = id_batches(list(range(100, 200)), budget=20)
    assert len(batches) > 1
    for batch in batches:
        assert len(_rendered(batch)) <= 20


def test_order_is_preserved_with_no_loss_or_duplication():
    ids = [7, 3, 12345, 3, 99, 1, 400000]
    flat = [v for batch in id_batches(ids, budget=8) for v in batch]
    assert flat == ids


def test_order_is_preserved_across_many_batches():
    ids = list(range(5000, 1000, -1))
    flat = [v for batch in id_batches(ids) for v in batch]
    assert flat == ids


def test_an_id_longer_than_the_budget_gets_its_own_batch():
    long_id = 10**30
    batches = id_batches([1, long_id, 2], budget=10)
    assert [long_id] in batches
    assert [v for batch in batches for v in batch] == [1, long_id, 2]


def test_nineteen_digit_ids_batch_within_the_budget():
    base = 9_000_000_000_000_000_000  # bigint-sized, 19 digits
    ids = [base + i for i in range(1000)]
    batches = id_batches(ids)
    assert len(batches) > 1
    for batch in batches:
        assert len(_rendered(batch)) <= ID_FILTER_BUDGET_CHARS
    assert [v for batch in batches for v in batch] == ids


def test_ids_that_fit_stay_in_one_batch():
    assert id_batches([1, 2, 3]) == [[1, 2, 3]]


def test_default_budget_is_the_module_constant():
    ids = list(range(1000, 4000))
    assert id_batches(ids) == id_batches(ids, budget=ID_FILTER_BUDGET_CHARS)


def test_budget_matches_bloomctl_so_the_two_cannot_drift():
    """(characterization) The service copies bloomctl's helper instead of importing it (design
    D9); this reads bloomctl's source so a change to either budget fails here."""
    tree = ast.parse(BLOOMCTL_POSTGREST.read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "ID_FILTER_BUDGET_CHARS"
            for t in node.targets
        )
        and isinstance(node.value, ast.Constant)
    ]
    assert values == [postgrest_batches.ID_FILTER_BUDGET_CHARS]

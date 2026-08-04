"""bloomctl cyl _resolve — classify() turns search rows into Resolved/Ambiguous/NoMatch."""

import bloomctl.cyl._resolve as r


def _row(id, name, species="Soybean", created="2026-01-15T00:00:00+00:00"):
    return {"id": id, "name": name, "species_name": species, "created_at": created}


def test_no_rows_is_nomatch():
    assert isinstance(r.classify([]), r.NoMatch)


def test_single_row_resolves_with_rich_fields():
    res = r.classify([_row(2, "Drought Response 2024", "Arabidopsis")])
    assert isinstance(res, r.Resolved)
    assert res.match.id == 2
    assert res.match.label == "Drought Response 2024 (Arabidopsis)"
    assert res.match.created == "2026-01-15"  # created_at truncated to a date


def test_multiple_rows_are_ambiguous_order_preserved():
    res = r.classify([_row(6, "soybean A"), _row(18, "soybean B")])
    assert isinstance(res, r.Ambiguous)
    assert [m.id for m in res.candidates] == [6, 18]  # server ORDER BY name preserved


def test_match_tolerates_missing_species_and_date():
    res = r.classify([{"id": 5, "name": "X"}])
    assert isinstance(res, r.Resolved)
    assert res.match.label == "X (?)"  # null species -> "?"
    assert res.match.created is None

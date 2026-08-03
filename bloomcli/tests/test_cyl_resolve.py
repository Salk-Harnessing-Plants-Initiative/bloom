"""bloomctl cyl _resolve — resolve_experiment (substring, fuzzy, ambiguity, species narrowing)."""

import bloomctl.cyl._resolve as r

EXPS = [
    {"id": 2, "name": "Drought Response 2024", "species": {"common_name": "Arabidopsis"}},
    {"id": 6, "name": "2025-11-20_soybean_cylinders", "species": {"common_name": "Soybean"}},
    {"id": 18, "name": "2026-01-15 Cquesta soy", "species": {"common_name": "Soybean"}},
]


def test_single_substring_match_resolves():
    res = r.resolve_experiment(EXPS, "drought")  # case-insensitive substring
    assert isinstance(res, r.Resolved)
    assert res.experiment_id == 2
    assert res.label == "Drought Response 2024 (Arabidopsis)"


def test_no_match_returns_nomatch():
    assert isinstance(r.resolve_experiment(EXPS, "sunflower field"), r.NoMatch)


def test_empty_name_is_no_match():
    assert isinstance(r.resolve_experiment(EXPS, ""), r.NoMatch)  # never matches everything


def test_multiple_matches_are_ambiguous_and_sorted():
    res = r.resolve_experiment(EXPS, "soy")  # both Soybean experiments contain "soy"
    assert isinstance(res, r.Ambiguous)
    assert res.candidates == [
        (6, "2025-11-20_soybean_cylinders (Soybean)"),
        (18, "2026-01-15 Cquesta soy (Soybean)"),
    ]  # sorted by label


def test_species_narrows_before_matching():
    # "soy" matches two Soybean experiments; narrowing to Arabidopsis leaves none
    assert isinstance(r.resolve_experiment(EXPS, "soy", species="Arabidopsis"), r.NoMatch)


def test_species_narrowing_is_case_insensitive():
    res = r.resolve_experiment(EXPS, "cquesta", species="soybean")  # lower-case species
    assert isinstance(res, r.Resolved)
    assert res.experiment_id == 18


def test_fuzzy_fallback_on_typo():
    exps = [{"id": 4, "name": "Root Architecture Panel", "species": {"common_name": "Arabidopsis"}}]
    res = r.resolve_experiment(exps, "Root Architecture Panl")  # typo, not a substring
    assert isinstance(res, r.Resolved)
    assert res.experiment_id == 4


def test_substring_beats_fuzzy():
    # an exact substring hit must win, never fall through to the fuzzy branch
    res = r.resolve_experiment(EXPS, "Cquesta")
    assert isinstance(res, r.Resolved)
    assert res.experiment_id == 18

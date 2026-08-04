"""bloomctl cyl _select — shared interactive menu helper (mapping, range, stderr)."""

import bloomctl.cyl._select as sel


def test_maps_choice_to_value(monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: 2)
    # menu 1) A(10) 2) B(20) → choosing 2 returns 20
    assert sel.select_from_menu([(10, "A"), (20, "B")], title="x", prompt_label="X") == 20


def test_first_choice_maps_to_first_value(monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: 1)
    assert sel.select_from_menu([(10, "A"), (20, "B")], title="x", prompt_label="X") == 10


def test_all_label_zero_returns_none(monkeypatch):
    monkeypatch.setattr("click.prompt", lambda *a, **k: 0)
    assert sel.select_from_menu([(10, "A")], title="x", prompt_label="X", all_label="All") is None


def test_all_label_offers_zero_in_range(monkeypatch):
    captured = {}

    def fake_prompt(text, type=None, err=None):
        captured["range"] = (type.min, type.max)
        return 0

    monkeypatch.setattr("click.prompt", fake_prompt)
    sel.select_from_menu([(10, "A"), (20, "B")], title="x", prompt_label="X", all_label="All")
    assert captured["range"] == (0, 2)  # 0 = All, 1..2 = items


def test_no_all_label_range_starts_at_one(monkeypatch):
    captured = {}

    def fake_prompt(text, type=None, err=None):
        captured["range"] = (type.min, type.max)
        return 1

    monkeypatch.setattr("click.prompt", fake_prompt)
    sel.select_from_menu([(10, "A"), (20, "B")], title="x", prompt_label="X")
    assert captured["range"] == (1, 2)  # no "all" entry → concrete choice only


def test_menu_written_to_stderr(capsys, monkeypatch):
    # The menu must go to stderr so machine output on stdout stays clean.
    monkeypatch.setattr("click.prompt", lambda *a, **k: 1)
    sel.select_from_menu(
        [(10, "Arabidopsis"), (20, "Soybean")],
        title="a species",
        prompt_label="Species",
        all_label="All species",
    )
    out = capsys.readouterr()
    assert out.out == ""  # nothing on stdout
    assert "Select a species:" in out.err
    assert "0) All species" in out.err
    assert "1) Arabidopsis" in out.err


# --- resolve_by_name (shared typed-name resolution: case-insensitive + trimmed) ---


def test_resolve_by_name_case_insensitive():
    items = [(3, "Canola"), (2, "Rice")]
    assert sel.resolve_by_name(items, "canola") == 3  # case-insensitive
    assert sel.resolve_by_name(items, "RICE") == 2


def test_resolve_by_name_trims_whitespace():
    assert sel.resolve_by_name([(3, "Canola")], "  canola  ") == 3


def test_resolve_by_name_no_match_returns_none():
    assert sel.resolve_by_name([(3, "Canola")], "Sorghum") is None


def test_resolve_by_name_returns_first_on_duplicate_label():
    # two case-variant labels resolve to the same casefold → the first item's value
    assert sel.resolve_by_name([(3, "Rice"), (9, "rice")], "RICE") == 3

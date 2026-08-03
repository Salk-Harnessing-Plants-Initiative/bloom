"""Tests for the shared `cyl` machine-readable output renderer."""

from __future__ import annotations

import csv
import io
import json

import click
import pytest

from bloomctl.cyl._output import render, resolve_output_format


def test_resolve_output_format_selection_and_alias():
    assert resolve_output_format(None, False) is None  # no flag → default table
    assert resolve_output_format("csv", False) == "csv"  # --output csv
    assert resolve_output_format(None, True) == "json"  # --json aliases --output json
    assert resolve_output_format("json", True) == "json"  # --json + --output json agree


def test_resolve_output_format_conflict_raises():
    with pytest.raises(click.UsageError):
        resolve_output_format("csv", True)  # --json with a conflicting --output


FIELDS = ["name", "species", "experiment", "experiment_id", "qc_code_count"]

RECORDS = [
    {
        "name": "canola-gh-qc",
        "species": "Canola",
        "experiment": "GH 284",
        "experiment_id": 12,
        "qc_code_count": 847,
    },
    {
        "name": "soy-rp-qc",
        "species": "Soybean",
        "experiment": "RP Soy",
        "experiment_id": 31,
        "qc_code_count": 0,
    },
]


def test_csv_has_header_and_one_row_per_record() -> None:
    parsed = list(csv.DictReader(io.StringIO(render(RECORDS, FIELDS, "csv"))))
    assert [r["name"] for r in parsed] == ["canola-gh-qc", "soy-rp-qc"]
    assert parsed[0]["qc_code_count"] == "847"


def test_csv_columns_are_in_declared_order() -> None:
    header = render(RECORDS, FIELDS, "csv").splitlines()[0]
    assert header.split(",") == FIELDS


def test_json_is_an_array_of_objects() -> None:
    parsed = json.loads(render(RECORDS, FIELDS, "json"))
    assert isinstance(parsed, list)
    assert parsed[0]["experiment_id"] == 12
    assert parsed[1]["qc_code_count"] == 0


def test_json_preserves_value_types() -> None:
    """Counts and ids stay numeric in JSON — only CSV stringifies."""
    parsed = json.loads(render(RECORDS, FIELDS, "json"))
    assert isinstance(parsed[0]["experiment_id"], int)
    assert isinstance(parsed[0]["qc_code_count"], int)


def test_empty_csv_is_header_only() -> None:
    out = render([], FIELDS, "csv")
    assert out.splitlines() == [",".join(FIELDS)]


def test_empty_json_is_an_empty_array() -> None:
    assert json.loads(render([], FIELDS, "json")) == []


def test_csv_escapes_commas_quotes_and_newlines() -> None:
    """The whole reason for the stdlib csv writer: special chars round-trip inside one field
    instead of corrupting the row."""
    records = [
        {
            "name": 'a,b "c"\nd',
            "species": None,
            "experiment": "x",
            "experiment_id": 1,
            "qc_code_count": 2,
        }
    ]
    parsed = list(csv.DictReader(io.StringIO(render(records, FIELDS, "csv"))))
    assert len(parsed) == 1  # the embedded newline did not split the row
    assert parsed[0]["name"] == 'a,b "c"\nd'  # comma, quote, and newline all preserved
    assert parsed[0]["species"] == ""  # None → empty cell, not the string "None"


def test_csv_ignores_extra_record_keys() -> None:
    """extrasaction='ignore': a record richer than the declared fields doesn't raise, and the
    extra keys never leak into the output."""
    records = [
        {**RECORDS[0], "id": 999, "internal": "SHOULD_NOT_APPEAR"},
    ]
    out = render(records, FIELDS, "csv")
    assert "999" not in out and "SHOULD_NOT_APPEAR" not in out
    parsed = list(csv.DictReader(io.StringIO(out)))
    assert set(parsed[0].keys()) == set(FIELDS)


def test_json_ignores_extra_record_keys() -> None:
    records = [{**RECORDS[0], "id": 999, "internal": "SHOULD_NOT_APPEAR"}]
    parsed = json.loads(render(records, FIELDS, "json"))
    assert set(parsed[0].keys()) == set(FIELDS)


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError):
        render(RECORDS, FIELDS, "yaml")

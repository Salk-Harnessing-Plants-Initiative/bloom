"""Tests for the shared `cyl` machine-readable output renderer."""

from __future__ import annotations

import csv
import io
import json

import pytest

from bloomctl.cyl._output import render

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


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError):
        render(RECORDS, FIELDS, "yaml")

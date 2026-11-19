"""bloommcp's gene lookup against the live stack: every column it reads exists, and a lookup
runs as bloom_agent without a read error."""

from __future__ import annotations

import pytest

from tests.integration.test_bloommcp_data_tools import _public_columns, _run_in_bloommcp

pytestmark = pytest.mark.integration

_SCRIPT = """
import json
from bloom_mcp.contract.errors import BloomMCPError
from bloom_mcp.data_access import gene_reads
from bloom_mcp.sections.bloom_data.gene_lookup import gene_lookup

def look(gene):
    try:
        return gene_lookup({"gene": gene}).model_dump()
    except BloomMCPError as exc:
        return exc.to_dict()

print(json.dumps({
    "columns": {t: list(c) for t, c in gene_reads.COLUMNS.items()},
    "known": look("AT1G01010"),
    "unknown": look("TEST_BLOOMMCP_NO_SUCH_GENE"),
}, default=str))
"""


@pytest.fixture(scope="module")
def results() -> dict:
    return _run_in_bloommcp(_SCRIPT)


def test_every_column_the_lookup_reads_exists(pg_conn, results):
    present = _public_columns(pg_conn)
    missing = sorted(
        f"{table}.{column}"
        for table, columns in results["columns"].items()
        for column in columns
        if (table, column) not in present
    )
    assert missing == [], f"the gene lookup reads columns that don't exist: {missing}"


def test_a_lookup_runs_as_bloom_agent(results):
    for name in ("known", "unknown"):
        assert "code" not in results[name], results[name]
        assert results[name]["links"] is not None


def test_an_unknown_gene_answers_with_a_note(results):
    unknown = results["unknown"]
    assert unknown["genes"] == [] and unknown["expression"] == []
    assert unknown["notes"]

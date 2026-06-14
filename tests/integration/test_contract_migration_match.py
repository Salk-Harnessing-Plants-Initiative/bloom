"""
Migration-matches-schema check for change `pin-sleap-roots-contract` (#294).

Asserts Bloom's applied DB schema agrees with the pinned sleap-roots-contracts
`result_envelope.schema.json` for the contract<->DB mappings BUILT TODAY (change
A only: `cyl_trait_sources.metadata` jsonb + `idempotency_key` text with UNIQUE +
non-empty CHECK). It also asserts the contract-side schema facts that justify
those DB choices (the `Provenance` envelope-home designation, the required
`contract_version` anchor, and the `idempotency_key` default of "").

Mappings introduced by later changes (B `source_id` FK, C blob table, D RPC key
equality, `contract_version` runtime validation, `scan_key` resolution) are
recorded as DEFERRED rows and skipped — never asserted against objects that do
not exist yet. As each lands, flip its row to `status="active"`.

Collection safety: `MAPPINGS` holds LITERALS ONLY (no schema-derived values), so
importing this module never reads the schema — that is what keeps collection safe
when the schema is not vendored yet. The schema is read lazily inside each test
via `_load_schema()`, which skips (does not crash) when the file is absent.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT and
mutates nothing — every DB test rolls back. Runs in CI's `compose-health-check`
after migrations are applied (`uv run --extra test pytest tests/integration/ -v`).
"""

import json
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the change-A test).
# This does not read the schema, so it is collection-safe.
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "contracts" / "schema" / "result_envelope.schema.json"

# Declarative contract<->DB mapping. LITERALS ONLY — no schema-derived values, so
# importing this module never touches the schema file (collection stays safe).
MAPPINGS = [
    # --- Active: built by change A (#290) ---
    {
        "contract_field": "Provenance (envelope)",
        "db_table": "cyl_trait_sources",
        "db_column": "metadata",
        "db_type": "jsonb",
        "status": "active",
        "reason": "change A: opaque Provenance envelope home",
    },
    {
        "contract_field": "Provenance.idempotency_key",
        "db_table": "cyl_trait_sources",
        "db_column": "idempotency_key",
        "db_type": "text",
        "status": "active",
        "reason": "change A: per-run idempotency anchor",
    },
    # --- Deferred: skipped, never asserted. Flip to active as each change lands. ---
    {
        "contract_field": "source_id FK",
        "db_table": "cyl_scan_traits / cyl_image_traits",
        "db_column": "source_id",
        "db_type": None,
        "status": "deferred",
        "reason": "#295 change B: source_id FK on the trait tables",
    },
    {
        "contract_field": "BlobRef",
        "db_table": "(intermediates/blob table)",
        "db_column": None,
        "db_type": None,
        "status": "deferred",
        "reason": "#296 change C: intermediates/blob table",
    },
    {
        "contract_field": "idempotency_key == metadata->>'idempotency_key'",
        "db_table": "cyl_trait_sources",
        "db_column": None,
        "db_type": None,
        "status": "deferred",
        "reason": "change D: RPC-enforced key equality (RPC-only)",
    },
    {
        "contract_field": "Provenance.contract_version (runtime row value)",
        "db_table": "cyl_trait_sources",
        "db_column": None,
        "db_type": None,
        "status": "deferred",
        "reason": "D/G consumer: validate provenance.contract_version == pinned version",
    },
    {
        "contract_field": "Provenance.scan_key -> cyl_scans.id",
        "db_table": "cyl_scans",
        "db_column": None,
        "db_type": None,
        "status": "deferred",
        "reason": "caller-side scan_key resolution via inputs.image_ids",
    },
]

ACTIVE = [m for m in MAPPINGS if m["status"] == "active"]
DEFERRED = [m for m in MAPPINGS if m["status"] == "deferred"]


def _load_schema() -> dict:
    """Lazily read the vendored contract schema. Skip (do NOT crash) if absent."""
    if not SCHEMA_PATH.exists():
        pytest.skip(
            "contract schema not vendored (contracts/schema/result_envelope.schema.json)"
        )
    return json.loads(SCHEMA_PATH.read_text())


def _column_type(cur, table: str, column: str) -> str | None:
    cur.execute(
        """
        SELECT data_type
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _constraint_types_on_column(cur, table: str, column: str) -> set[str]:
    """Return the set of constraint types (pg_constraint.contype) whose key columns
    include `column`. Keyed on the column, NOT on constraint names — so a legitimate
    constraint rename can't false-positive this assertion."""
    cur.execute(
        """
        SELECT c.contype::text
          FROM pg_constraint c
          JOIN pg_attribute a
            ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
         WHERE c.conrelid = %s::regclass AND a.attname = %s
        """,
        (f"public.{table}", column),
    )
    return {row[0] for row in cur.fetchall()}


# --------------------------------------------------------------------------- #
# Contract-side sanity — schema facts that justify change A's DB choices.
# (Schema-only; no DB needed.)
# --------------------------------------------------------------------------- #


def test_contract_designates_metadata_as_provenance_home():
    # The ONLY contract-side net for a description-level re-home: json2ts drops
    # descriptions, so the byte-equal TS guard is blind to this. Runs against the
    # frozen vendored copy, so it only fires on a deliberate re-pin.
    schema = _load_schema()
    assert "cyl_trait_sources.metadata" in schema["$defs"]["Provenance"]["description"]


def test_contract_requires_contract_version_anchor():
    # contract_version is the per-row provenance-of-origin anchor; D/G's future
    # validation has nothing to validate if a re-pin ever makes it optional.
    schema = _load_schema()
    provenance = schema["$defs"]["Provenance"]
    assert "contract_version" in provenance["required"]
    assert provenance["properties"]["contract_version"]["type"] == "string"


def test_contract_idempotency_key_default_is_empty_string():
    # The documented basis for change A's non-empty CHECK: an unset key arrives
    # as "" (never NULL). If this default changed, the CHECK rationale would rot.
    schema = _load_schema()
    idem = schema["$defs"]["Provenance"]["properties"]["idempotency_key"]
    assert idem.get("default") == ""
    assert idem["type"] == "string"


# --------------------------------------------------------------------------- #
# Active contract<->DB mappings — introspect the live applied schema.
# --------------------------------------------------------------------------- #


def test_active_mapping_columns_have_expected_types(pg_conn):
    """Declarative sweep: every active mapping naming a column+type holds in the DB."""
    checked = 0
    with pg_conn.cursor() as cur:
        for m in ACTIVE:
            if m["db_column"] and m["db_type"]:
                actual = _column_type(cur, m["db_table"], m["db_column"])
                assert actual == m["db_type"], (
                    f"{m['contract_field']}: expected {m['db_table']}.{m['db_column']} "
                    f"to be {m['db_type']}, got {actual!r}"
                )
                checked += 1
    pg_conn.rollback()
    # Floor: a future edit that empties ACTIVE (or drops every column) must not let
    # this test pass vacuously. Change A ships exactly two column-typed active rows.
    assert checked >= 2, f"expected >= 2 active column mappings checked, got {checked}"


def test_idempotency_anchor_constraints_by_type(pg_conn):
    # Assert by contype on the COLUMN, NOT by constraint name — the _key/_nonempty
    # suffixes are convention, not enforcement, and a legitimate rename must not
    # false-positive. A UNIQUE is the 1-envelope:1-row anchor; the CHECK rejects the
    # contract's "" default.
    with pg_conn.cursor() as cur:
        types = _constraint_types_on_column(cur, "cyl_trait_sources", "idempotency_key")
    assert "u" in types, "UNIQUE anchor on idempotency_key missing"
    assert "c" in types, "non-empty CHECK on idempotency_key missing"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Deferred mappings — skipped with a reason, never asserted.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mapping", DEFERRED, ids=[m["contract_field"] for m in DEFERRED]
)
def test_deferred_mapping_is_skipped(mapping):
    pytest.skip(f"deferred: {mapping['reason']}")

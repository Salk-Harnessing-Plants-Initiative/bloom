"""
Migration-matches-schema check for change `pin-sleap-roots-contract` (#294).

Asserts Bloom's applied DB schema agrees with the pinned sleap-roots-contracts
`result_envelope.schema.json` for the contract<->DB mappings BUILT TODAY (change
A only: `cyl_trait_sources.metadata` jsonb + `idempotency_key` text with UNIQUE +
non-empty CHECK). It also asserts the contract-side schema facts that justify
those DB choices (the `Provenance` envelope-home designation, the required
`contract_version` anchor, and the `idempotency_key` default of "").

Change C (#296) activated the `BlobRef` mapping: the `cyl_scan_intermediates`
table (columns/types, the two FKs, the at-least-one-location CHECK, and the `kind`
vocabulary == the contract `BlobRef.kind` enum). Changes D/E (#13/#297) activated
the write-back-RPC mappings — their STRUCTURAL support is asserted here (the
`insert_cyl_result_envelope` function exists; the `cyl_images.scan_id -> cyl_scans`
resolution FK exists), while the RPC's runtime behavior (key equality,
`contract_version` validation, scan resolution) is verified by
`test_cyl_writeback_rpc.py`. The remaining deferred row is change B's
`cyl_image_traits.source_id` FK; as it lands, flip its row to `status="active"`.

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
    # --- Active: built by change C (#296) ---
    {
        "contract_field": "BlobRef",
        "db_table": "cyl_scan_intermediates",
        "db_column": None,  # table-level mapping — asserted by dedicated tests below
        "db_type": None,
        "status": "active",
        "reason": "#296 change C: per-scan intermediates/blob table",
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
    # --- Active: built by change D (#13/#297) — structural support for the RPC. ---
    {
        "contract_field": "idempotency_key == metadata->>'idempotency_key'",
        "db_table": "cyl_trait_sources",
        "db_column": None,
        "db_type": None,
        "status": "active",
        "reason": "change D: RPC enforces key equality (structural support: the RPC exists)",
    },
    {
        "contract_field": "Provenance.contract_version (runtime row value)",
        "db_table": "cyl_trait_sources",
        "db_column": None,
        "db_type": None,
        "status": "active",
        "reason": "change D: RPC validates contract_version (structural support: the RPC exists)",
    },
    {
        "contract_field": "Provenance.scan_key -> cyl_scans.id",
        "db_table": "cyl_scans",
        "db_column": None,
        "db_type": None,
        "status": "active",
        "reason": "change D: image_ids -> cyl_images.scan_id resolution (FK present)",
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


def test_deferred_set_is_non_empty():
    # After changes C and D flipped their rows -> active, DEFERRED must still hold the
    # remaining change-B row so the parametrized skip test above keeps exercising a real row.
    assert len(DEFERRED) >= 1
    assert all(m["status"] == "deferred" for m in DEFERRED)


# --------------------------------------------------------------------------- #
# Change D structural mappings (#13/#297): the write-back RPC and the
# scan-resolution FK. These assert the STRUCTURAL precondition for the RPC's
# runtime enforcement (key equality, contract_version validation, scan
# resolution) — the behavior itself is verified by test_cyl_writeback_rpc.py.
# --------------------------------------------------------------------------- #

WRITEBACK_RPC = "insert_cyl_result_envelope"


def _rpc_exists(cur) -> bool:
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = %s", (WRITEBACK_RPC,))
    return cur.fetchone() is not None


def _scan_resolution_fk_exists(cur) -> bool:
    """cyl_images.scan_id -> cyl_scans.id — the image_ids resolution path."""
    cur.execute(
        """
        SELECT 1
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
          JOIN pg_class cf     ON cf.oid = c.confrelid
         WHERE c.conrelid = 'public.cyl_images'::regclass AND c.contype = 'f'
           AND a.attname = 'scan_id' AND cf.relname = 'cyl_scans'
        """
    )
    return cur.fetchone() is not None


def test_writeback_rpc_structural_support_present(pg_conn):
    with pg_conn.cursor() as cur:
        assert _rpc_exists(cur), "write-back RPC insert_cyl_result_envelope is missing"
        assert _scan_resolution_fk_exists(cur), (
            "cyl_images.scan_id -> cyl_scans FK (the image_ids resolution path) is missing"
        )
    pg_conn.rollback()


def test_writeback_rpc_regression_is_detected(pg_conn):
    """A regression in the newly-active mapping fails the check: drop the RPC inside a
    SAVEPOINT and assert the structural check then fails; roll back so nothing changes."""
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT before_regression")
        cur.execute("DROP FUNCTION public.insert_cyl_result_envelope(jsonb)")
        assert not _rpc_exists(cur)
        cur.execute("ROLLBACK TO SAVEPOINT before_regression")
        assert _rpc_exists(cur)  # restored — the check passes again
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# BlobRef active mapping (change C): cyl_scan_intermediates.
# --------------------------------------------------------------------------- #

INTERMEDIATES = "cyl_scan_intermediates"

# Column -> information_schema.data_type expected for the BlobRef mapping.
_BLOBREF_COLUMNS = {
    "source_id": "bigint",
    "scan_id": "bigint",
    "kind": "text",
    "root_type": "text",
    "s3_location": "text",
    "box_link": "text",
    "checksum": "text",
    "file_size": "bigint",
}


def _foreign_keys(cur, table: str) -> set[tuple[str, str]]:
    cur.execute(
        """
        SELECT a.attname, cf.relname
          FROM pg_constraint c
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
          JOIN pg_class cf     ON cf.oid = c.confrelid
         WHERE c.conrelid = %s::regclass AND c.contype = 'f'
        """,
        (f"public.{table}",),
    )
    return {(row[0], row[1]) for row in cur.fetchall()}


def _has_location_check(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT pg_get_constraintdef(c.oid)
          FROM pg_constraint c
         WHERE c.conrelid = %s::regclass AND c.contype = 'c'
        """,
        (f"public.{table}",),
    )
    return any(
        "s3_location" in d and "box_link" in d for d in (row[0] for row in cur.fetchall())
    )


def test_blobref_maps_to_intermediates_table(pg_conn):
    """The active BlobRef mapping: the table exists with the expected columns/types,
    both FKs (by contype/confrelid), and the at-least-one-location CHECK."""
    with pg_conn.cursor() as cur:
        for column, expected in _BLOBREF_COLUMNS.items():
            actual = _column_type(cur, INTERMEDIATES, column)
            assert actual == expected, (
                f"BlobRef: expected {INTERMEDIATES}.{column} to be {expected}, got {actual!r}"
            )
        fks = _foreign_keys(cur, INTERMEDIATES)
        assert ("source_id", "cyl_trait_sources") in fks
        assert ("scan_id", "cyl_scans") in fks
        assert _has_location_check(cur, INTERMEDIATES), (
            "at-least-one-location CHECK on cyl_scan_intermediates missing"
        )
    pg_conn.rollback()


def test_blobref_regression_is_detected(pg_conn):
    """A regression in the built mapping fails the check: drop the at-least-one-location
    CHECK inside a SAVEPOINT and assert the mapping assertion then fails; roll back so
    the schema is untouched."""
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT before_regression")
        # Drop whichever CHECK references both location columns.
        cur.execute(
            """
            SELECT conname FROM pg_constraint
             WHERE conrelid = %s::regclass AND contype = 'c'
               AND pg_get_constraintdef(oid) LIKE '%%s3_location%%'
               AND pg_get_constraintdef(oid) LIKE '%%box_link%%'
            """,
            (f"public.{INTERMEDIATES}",),
        )
        names = [row[0] for row in cur.fetchall()]
        assert names, "expected a location CHECK to exist before the regression test"
        for name in names:
            cur.execute(f'ALTER TABLE {INTERMEDIATES} DROP CONSTRAINT "{name}"')
        assert not _has_location_check(cur, INTERMEDIATES)
        cur.execute("ROLLBACK TO SAVEPOINT before_regression")
        # After rollback the CHECK is back — the sweep passes again.
        assert _has_location_check(cur, INTERMEDIATES)
    pg_conn.rollback()


def _blobref_enum(schema: dict, prop: str) -> set[str]:
    """Read a BlobRef property's allowed-value set defensively. Pydantic renders a
    single-value Literal as JSON-Schema `const` and a multi-value one as `enum`, so accept
    either — a `KeyError` here would mask the real ("DB vocab != contract vocab") signal."""
    p = schema["$defs"]["BlobRef"]["properties"][prop]
    values = p.get("enum")
    if values is None and "const" in p:
        values = [p["const"]]
    assert values, f"BlobRef.{prop} has no enum/const in the vendored schema"
    return set(values)


def _probe_accepted(cur, source_id, scan_id, *, column, fixed, candidates) -> set[str]:
    """Behavioral CHECK probe: try one INSERT per candidate value for `column` (with the
    other vocabulary column held at `fixed`), each in its own SAVEPOINT that always rolls
    back — so no row persists and the UNIQUE 4-tuple can never collide across iterations.
    Returns the set the DB CHECK accepted. No constraint-text parsing (Postgres rewrites
    `IN (...)` to `= ANY(ARRAY[...])`, which is brittle)."""
    accepted = set()
    for value in candidates:
        cur.execute("SAVEPOINT probe")
        try:
            cur.execute(
                f"INSERT INTO {INTERMEDIATES} "
                f"(source_id, scan_id, kind, root_type, s3_location) "
                f"VALUES (%(src)s, %(scan)s, %(kind)s, %(root_type)s, 's3://b/k.slp')",
                {"src": source_id, "scan": scan_id, **fixed, column: value},
            )
        except psycopg.errors.CheckViolation:
            cur.execute("ROLLBACK TO SAVEPOINT probe")
        else:
            accepted.add(value)
            cur.execute("ROLLBACK TO SAVEPOINT probe")  # MUST roll back to stay re-entrant
    return accepted


def test_db_kind_vocab_matches_contract_blobref_enum(pg_conn):
    """The DB `kind` CHECK vocabulary equals the pinned contract `BlobRef.kind` enum,
    proved by a behavioral INSERT probe. Unconditional since the contract was re-pinned to
    `v0.1.0a2` (`BlobRef.kind == {predictions_slp}`); a future re-pin diverging from the DB
    CHECK fails loudly."""
    contract_kinds = _blobref_enum(_load_schema(), "kind")
    negatives = {"h5", "labels", "qc_image", "bogus"} - contract_kinds
    assert negatives, "negative-control set is empty — the probe would not test rejection"
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('kind-probe') RETURNING id")
        source_id = cur.fetchone()[0]
        cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
        scan_id = cur.fetchone()[0]
        accepted = _probe_accepted(
            cur, source_id, scan_id,
            column="kind", fixed={"root_type": "primary"}, candidates=contract_kinds | negatives,
        )
    pg_conn.rollback()
    assert accepted == contract_kinds, (
        f"DB kind vocabulary {sorted(accepted)} != contract BlobRef.kind {sorted(contract_kinds)}"
    )


def test_db_root_type_vocab_matches_contract_blobref_enum(pg_conn):
    """The DB `root_type` CHECK vocabulary equals the pinned contract `BlobRef.root_type`
    enum — symmetric with the `kind` probe. As of `v0.1.0a2` the contract owns `root_type`
    (`{primary, lateral, crown}`), so it is contract-anchored (design D3, revised): a
    contract/DB divergence on `root_type` fails CI just like `kind`."""
    contract_root_types = _blobref_enum(_load_schema(), "root_type")
    negatives = {"seminal", "tap", "bogus"} - contract_root_types
    assert negatives, "negative-control set is empty — the probe would not test rejection"
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('rt-probe') RETURNING id")
        source_id = cur.fetchone()[0]
        cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
        scan_id = cur.fetchone()[0]
        accepted = _probe_accepted(
            cur, source_id, scan_id,
            column="root_type", fixed={"kind": "predictions_slp"},
            candidates=contract_root_types | negatives,
        )
    pg_conn.rollback()
    assert accepted == contract_root_types, (
        f"DB root_type vocabulary {sorted(accepted)} != contract BlobRef.root_type "
        f"{sorted(contract_root_types)}"
    )

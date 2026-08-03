"""cyl_experiment_search RPC — server-side experiment name lookup for `cyl download`.

Exercises the actual SQL (not the CLI's mocked `client.rpc`): the LIKE-metacharacter
escaping, the eff_limit clamp, deleted_at exclusion, exact case-insensitive species
narrowing, the empty-query / length guards, and the anon-role lockdown.

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin`
(BYPASSRLS); every test rolls back. Seed names are uniquified with a token so the
assertions hold whether or not the DB carries seed data. Mirrors
`test_cyl_plant_search_rpc.py`.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260803000000_add_cyl_experiment_search"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
SIG = "cyl_experiment_search(text, text, integer)"


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed_exp(cur, name, *, species="Sp", deleted=False):
    """Seed one species + one cyl_experiment; return (experiment_id, species_id)."""
    cur.execute(
        "INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,)
    )
    species_id = cur.fetchone()[0]
    deleted_at = "now()" if deleted else "NULL"
    cur.execute(
        f"INSERT INTO cyl_experiments (name, species_id, deleted_at) "
        f"VALUES (%s, %s, {deleted_at}) RETURNING id",
        (name, species_id),
    )
    return cur.fetchone()[0], species_id


def _seed_many(cur, token, n, *, species="Sp"):
    """Seed n experiments whose names all contain `clamp<token>` (one shared species)."""
    cur.execute(
        "INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,)
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) "
        "SELECT %s || g, %s FROM generate_series(1, %s) g",
        (f"clamp{token}-", species_id, n),
    )


def _call(cur, query, *, species=None, limit="__default__"):
    """Call the RPC; return the result rows as (id, name, species_name, created_at) tuples."""
    if limit == "__default__":
        cur.execute(
            "SELECT id, name, species_name, created_at "
            "FROM cyl_experiment_search(p_query := %s, p_species := %s)",
            (query, species),
        )
    else:
        cur.execute(
            "SELECT id, name, species_name, created_at "
            "FROM cyl_experiment_search(p_query := %s, p_species := %s, p_limit := %s)",
            (query, species, limit),
        )
    return cur.fetchall()


def _ids(rows):
    return {r[0] for r in rows}


# --------------------------------------------------------------------------- #
# Matching: substring, case-insensitive, joined species + created_at
# --------------------------------------------------------------------------- #


def test_substring_match_case_insensitive_returns_joined_fields(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        eid, _ = _seed_exp(cur, f"DroughtScreen{t}", species=f"Soybean{t}")
        rows = _call(
            cur, f"droughtscreen{t}"
        )  # lower-case query hits a mixed-case name
        assert _ids(rows) == {eid}
        assert rows[0][1] == f"DroughtScreen{t}"
        assert rows[0][2] == f"Soybean{t}"  # species_name comes from the LEFT JOIN
        assert rows[0][3] is not None  # created_at present
    pg_conn.rollback()


def test_partial_substring_matches(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        eid, _ = _seed_exp(cur, f"2025-11-20_soybean_cylinders_{t}")
        assert _ids(_call(cur, f"soybean_cylinders_{t}")) == {eid}
    pg_conn.rollback()


def test_results_ordered_by_name(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"zzz-ord{t}")
        _seed_exp(cur, f"aaa-ord{t}")
        names = [r[1] for r in _call(cur, f"ord{t}")]
        assert names == sorted(names)  # ORDER BY name, id
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# LIKE-metacharacter escaping: the query is a LITERAL substring, not a pattern.
# The escaping order (\ first, then % and _) is what makes this hold.
# --------------------------------------------------------------------------- #


def test_underscore_in_query_is_literal_not_wildcard(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        lit, _ = _seed_exp(cur, f"a_b{t}")  # literal underscore
        _seed_exp(cur, f"axb{t}")  # would match if `_` were a wildcard
        assert _ids(_call(cur, f"a_b{t}")) == {lit}
    pg_conn.rollback()


def test_percent_in_query_is_literal_not_wildcard(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        lit, _ = _seed_exp(cur, f"c%d{t}")  # literal percent
        _seed_exp(cur, f"cZZZd{t}")  # would match if `%` were a wildcard
        assert _ids(_call(cur, f"c%d{t}")) == {lit}
    pg_conn.rollback()


def test_backslash_in_query_is_literal(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        lit, _ = _seed_exp(cur, f"g\\h{t}")  # one literal backslash
        # `\` is escaped first, so it matches literally and doesn't corrupt the pattern
        assert _ids(_call(cur, f"g\\h{t}")) == {lit}
    pg_conn.rollback()


def test_lone_percent_query_matches_nothing(pg_conn):
    # A bare '%' would match every experiment if unescaped; escaped, it only matches
    # names containing a literal percent — of which the token guarantees none.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"plain{t}")
        assert (
            _call(cur, f"%{t}") == []
        )  # `%<token>` is a literal, matches no plain name
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# eff_limit clamp: LEAST(GREATEST(COALESCE(p_limit, 50), 1), 50) -> [1, 50].
# The RPC is callable directly through PostgREST, so the page size is clamped
# server-side rather than trusted from the caller.
# --------------------------------------------------------------------------- #


def test_default_limit_caps_at_50(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 55)
        assert len(_call(cur, f"clamp{t}")) == 50
    pg_conn.rollback()


def test_limit_above_ceiling_is_clamped_to_50(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 55)
        assert len(_call(cur, f"clamp{t}", limit=99999)) == 50
    pg_conn.rollback()


def test_explicit_small_limit_is_honored(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 5)
        assert len(_call(cur, f"clamp{t}", limit=2)) == 2
    pg_conn.rollback()


def test_nonpositive_limit_clamps_to_one(pg_conn):
    # GREATEST(..., 1): a 0 or negative page size returns one row, never zero/everything.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 5)
        assert len(_call(cur, f"clamp{t}", limit=0)) == 1
        assert len(_call(cur, f"clamp{t}", limit=-5)) == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# deleted_at exclusion, empty-query and length guards
# --------------------------------------------------------------------------- #


def test_soft_deleted_experiment_excluded(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        live, _ = _seed_exp(cur, f"live{t}")
        _seed_exp(
            cur, f"live{t}-dead", deleted=True
        )  # matches the substring but is soft-deleted
        assert _ids(_call(cur, f"live{t}")) == {live}
    pg_conn.rollback()


def test_empty_or_blank_query_returns_nothing(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"anything{t}")
        assert _call(cur, "") == []  # must never match everything
        assert _call(cur, "   ") == []  # btrim -> empty
    pg_conn.rollback()


def test_query_over_200_chars_raises(pg_conn):
    with pg_conn.cursor() as cur:
        with pytest.raises(Exception, match="too long"):
            _call(cur, "x" * 201)
    pg_conn.rollback()


def test_query_at_200_chars_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        assert _call(cur, "x" * 200) == []  # boundary: allowed, just matches nothing
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Species narrowing: exact, case-insensitive, whitespace-trimmed (NOT substring)
# --------------------------------------------------------------------------- #


def test_species_filter_exact_case_insensitive_and_trimmed(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        soy, _ = _seed_exp(cur, f"salt{t}", species=f"SoyCap{t}")
        _seed_exp(cur, f"salt{t}x", species=f"RiceCap{t}")
        assert _ids(_call(cur, f"salt{t}", species=f"soycap{t}")) == {
            soy
        }  # case-insensitive
        assert _ids(_call(cur, f"salt{t}", species=f"  SoyCap{t}  ")) == {soy}  # btrim
    pg_conn.rollback()


def test_species_filter_is_exact_not_substring(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"salt{t}", species=f"Soybean{t}")
        assert _call(cur, f"salt{t}", species="Soy") == []  # substring must NOT match
    pg_conn.rollback()


def test_species_null_does_not_filter(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        a, _ = _seed_exp(cur, f"mix{t}", species=f"SpA{t}")
        b, _ = _seed_exp(cur, f"mix{t}b", species=f"SpB{t}")
        assert _ids(_call(cur, f"mix{t}")) == {a, b}  # no species -> both
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Security posture: SECURITY INVOKER, pinned search_path, grants
# --------------------------------------------------------------------------- #


def test_function_is_security_invoker_with_pinned_search_path(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig FROM pg_proc WHERE proname='cyl_experiment_search'"
        )
        secdef, proconfig = cur.fetchone()
        assert secdef is False, "must be SECURITY INVOKER so RLS applies as the caller"
        assert any(c.startswith("search_path=") for c in (proconfig or []))
    pg_conn.rollback()


def test_execute_not_granted_to_public_or_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "anon must not execute the RPC"
    pg_conn.rollback()


@pytest.mark.parametrize(
    "role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"]
)
def test_execute_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
        assert cur.fetchone()[0] is True, f"{role} should hold EXECUTE on the RPC"
    pg_conn.rollback()


def test_bloom_user_can_call_the_rpc(pg_conn):
    # End-to-end through the grant + SECURITY INVOKER as the read role: it runs
    # without a permission error and returns a well-formed result set.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"role{t}")
        cur.execute("SET LOCAL ROLE bloom_user")
        rows = _call(cur, f"role{t}")
        cur.execute("RESET ROLE")
        assert isinstance(rows, list)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Migration idempotency
# --------------------------------------------------------------------------- #


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_migration_body_is_idempotent(pg_conn):
    # CREATE EXTENSION / INDEX IF NOT EXISTS + CREATE OR REPLACE FUNCTION + REVOKE/GRANT
    # all re-apply cleanly.
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='cyl_experiment_search'")
        assert cur.fetchone() is not None
    pg_conn.rollback()

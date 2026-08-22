"""gravi_experiment_search RPC — server-side name lookup for `bloomctl plate download`.

Exercises the actual SQL (not the CLI's mocked `client.rpc`): the LIKE-metacharacter escaping,
the eff_limit clamp, exact case-insensitive species narrowing, the empty-query / length guards,
the anon-role lockdown, and the one thing that differs from the cyl RPC — system_name on every
row, because the same experiment name on two rigs is a legal state.

LOCAL ONLY: the `pg_conn` fixture (see conftest) connects as `supabase_admin` (BYPASSRLS);
every test rolls back. Seed names are uniquified with a token so the assertions hold whether or
not the DB carries seed data. Mirrors `test_cyl_experiment_search_rpc.py`.
"""

import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
_TS = "20260817000100_add_gravi_experiment_search"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
SIG = "gravi_experiment_search(text, text, integer)"


def _tok() -> str:
    return uuid.uuid4().hex[:10]


def _seed_exp(cur, name, *, species=None, system_name=None):
    """Seed one species + one gravi_experiment; return (experiment_id, species_name)."""
    # species.common_name is UNIQUE, so the default has to be per-call unique.
    species = species or f"Sp{_tok()}"
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (species,))
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO gravi_experiments (name, species_id, system_name) "
        "VALUES (%s, %s, %s) RETURNING id",
        (name, species_id, system_name),
    )
    return cur.fetchone()[0], species


def _seed_many(cur, token, n):
    """Seed n experiments whose names all contain `clamp<token>` (one shared species)."""
    cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (f"Sp{_tok()}",))
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO gravi_experiments (name, species_id, system_name) "
        "SELECT %s || g, %s, 'GRAV-01' FROM generate_series(1, %s) g",
        (f"clamp{token}-", species_id, n),
    )


def _call(cur, query, *, species=None, limit="__default__"):
    """Call the RPC; return rows as (id, name, species_name, system_name, created_at)."""
    columns = "id, name, species_name, system_name, created_at"
    if limit == "__default__":
        cur.execute(
            f"SELECT {columns} FROM gravi_experiment_search(p_query := %s, p_species := %s)",
            (query, species),
        )
    else:
        cur.execute(
            f"SELECT {columns} "
            f"FROM gravi_experiment_search(p_query := %s, p_species := %s, p_limit := %s)",
            (query, species, limit),
        )
    return cur.fetchall()


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_substring_match_is_case_insensitive(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"GraviTest{t}")
        rows = _call(cur, f"gravitest{t}")
        assert [r[1] for r in rows] == [f"GraviTest{t}"]
    pg_conn.rollback()


def test_same_name_on_two_rigs_is_distinguishable(pg_conn):
    # gravi_experiments is UNIQUE(species_id, name, system_name): one name, two rigs is legal.
    # Without system_name on the result the caller sees two identical-looking rows.
    t = _tok()
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO species (common_name) VALUES (%s) RETURNING id", (f"Sp{t}",))
        species_id = cur.fetchone()[0]
        for rig in ("GRAV-01", "GRAV-02"):
            cur.execute(
                "INSERT INTO gravi_experiments (name, species_id, system_name) "
                "VALUES (%s, %s, %s)",
                (f"twin{t}", species_id, rig),
            )

        rows = _call(cur, f"twin{t}")
        assert len(rows) == 2
        assert {r[3] for r in rows} == {"GRAV-01", "GRAV-02"}
        assert len({r[0] for r in rows}) == 2, "two distinct experiments"
    pg_conn.rollback()


def test_species_narrowing_is_exact_and_case_insensitive(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _, wanted = _seed_exp(cur, f"narrow{t}-a")
        _seed_exp(cur, f"narrow{t}-b")

        rows = _call(cur, f"narrow{t}", species=wanted.upper())
        assert [r[1] for r in rows] == [f"narrow{t}-a"]

        rows = _call(cur, f"narrow{t}", species=f"  {wanted}  ")
        assert [r[1] for r in rows] == [f"narrow{t}-a"], "species is trimmed before comparing"
    pg_conn.rollback()


def test_species_narrowing_is_not_a_substring_match(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _, wanted = _seed_exp(cur, f"exact{t}")
        assert _call(cur, f"exact{t}", species=wanted[:4]) == []
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("query", [None, "", "   "])
def test_blank_query_matches_nothing(pg_conn, query):
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"blank{_tok()}")
        assert _call(cur, query) == [], "an empty query must never return every experiment"
    pg_conn.rollback()


def test_over_long_query_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        with pytest.raises(psycopg.errors.RaiseException, match="too long"):
            _call(cur, "x" * 201)
    pg_conn.rollback()


def test_query_at_the_length_limit_is_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        assert _call(cur, "x" * 200) == []
    pg_conn.rollback()


def test_like_metacharacters_are_literal(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"100%{t}")
        _seed_exp(cur, f"100{t}-not-a-match")

        rows = _call(cur, f"100%{t}")
        assert [r[1] for r in rows] == [f"100%{t}"], "'%' must be a literal, not a wildcard"
    pg_conn.rollback()


def test_underscore_is_literal(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"a_b{t}")
        _seed_exp(cur, f"axb{t}")

        rows = _call(cur, f"a_b{t}")
        assert [r[1] for r in rows] == [f"a_b{t}"], "'_' must not match any single character"
    pg_conn.rollback()


def test_limit_is_clamped_server_side(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 60)
        assert len(_call(cur, f"clamp{t}", limit=10000)) == 50
        assert len(_call(cur, f"clamp{t}")) == 50, "the default is the cap too"
    pg_conn.rollback()


def test_limit_below_one_returns_one_row(pg_conn):
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_many(cur, t, 5)
        assert len(_call(cur, f"clamp{t}", limit=0)) == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #


def test_execute_not_granted_to_public_or_anon(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (SIG,))
        assert cur.fetchone()[0] is False, "anon must not execute the RPC"
    pg_conn.rollback()


@pytest.mark.parametrize("role", ["authenticated", "bloom_user", "bloom_admin", "bloom_agent"])
def test_execute_granted_to_bloom_roles(pg_conn, role):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
        assert cur.fetchone()[0] is True, f"{role} should hold EXECUTE on the RPC"
    pg_conn.rollback()


def test_bloom_user_can_call_the_rpc(pg_conn):
    # End-to-end through the grant + SECURITY INVOKER as the read role.
    t = _tok()
    with pg_conn.cursor() as cur:
        _seed_exp(cur, f"role{t}")
        cur.execute("SET LOCAL ROLE bloom_user")
        rows = _call(cur, f"role{t}")
        cur.execute("RESET ROLE")
        assert isinstance(rows, list)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Index + migration idempotency
# --------------------------------------------------------------------------- #


def test_trigram_index_exists_on_the_name(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM pg_indexes WHERE tablename = 'gravi_experiments' "
            "AND indexname = 'idx_gravi_experiments_name_trgm'"
        )
        assert cur.fetchone() is not None, "the substring search needs a trigram index"
    pg_conn.rollback()


def _sql_body(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_migration_body_is_idempotent(pg_conn):
    # Apply the migration body TWICE in this transaction and assert both succeed. Applying once
    # would only prove a clean re-apply if CI had already pushed it; twice proves idempotency
    # regardless of prior state.
    body = _sql_body(MIGRATION)
    with pg_conn.cursor() as cur:
        cur.execute(body)
        cur.execute(body)
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='gravi_experiment_search'")
        assert cur.fetchone() is not None
    pg_conn.rollback()

"""
Integration tests for add_species, which lets a signed-in scientist add a species from the
scRNA job form: it stores the name in one casing with the caller as created_by; returns the
species already stored under the same genus and species, or the same common name (compared
in lower case), instead of adding a second; refuses bad names and callers who aren't signed
in; and only signed-in Bloom roles may call it.

Each test re-applies the migration inside its own transaction and rolls it back, so the
database is left unchanged.
"""

import json
import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")

SIG = "public.add_species(text, text, text)"
MIGRATION = _find_one("migrations", "*_add_species_function.sql")
ROLLBACK = _find_one("rollbacks", "*_add_species_function_rollback.sql")
CALLERS = ("bloom_user", "bloom_writer", "bloom_admin")
NOT_CALLERS = ("anon", "authenticated", "bloom_agent", "bloom_workflows")


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


@pytest.fixture
def user(cur):
    """A signed-up user, so created_by's foreign key to auth.users holds."""
    user_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO auth.users (id, email) VALUES (%s, %s)",
        (user_id, f"species-{user_id[:8]}@salk.edu"),
    )
    return user_id


def _add(cur, user_id, genus, species, common_name, role="bloom_user"):
    """add_species as `role` signed in as `user_id`; returns its one row."""
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": user_id, "role": role}),),
    )
    cur.execute(f"SET LOCAL ROLE {role}")
    cur.execute("SELECT * FROM add_species(%s, %s, %s)", (genus, species, common_name))
    rows = cur.fetchall()
    cur.execute("RESET ROLE")
    assert len(rows) == 1
    species_id, common, stored_genus, stored_species, result = rows[0]
    return {
        "id": species_id,
        "common_name": common,
        "genus": stored_genus,
        "species": stored_species,
        "result": result,
    }


def _refused(cur, user_id, args, error):
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": user_id, "role": "bloom_user"}) if user_id else "",),
    )
    cur.execute("SET LOCAL ROLE bloom_user")
    cur.execute("SAVEPOINT refused")
    try:
        with pytest.raises(error):
            cur.execute("SELECT * FROM add_species(%s, %s, %s)", args)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT refused")
        cur.execute("RESET ROLE")


def _stored(cur, genus):
    cur.execute(
        "SELECT genus, species, common_name, created_by::text FROM public.species "
        "WHERE lower(genus) = lower(%s) ORDER BY id",
        (genus,),
    )
    return cur.fetchall()


def _seed(cur, genus, species, common_name, deleted=False):
    cur.execute(
        "INSERT INTO public.species (genus, species, common_name, deleted_at) "
        "VALUES (%s, %s, %s, CASE WHEN %s THEN now() END) RETURNING id",
        (genus, species, common_name, deleted),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# Adding
# --------------------------------------------------------------------------- #


def test_a_new_species_is_stored_tidied_with_the_caller_as_created_by(cur, user):
    row = _add(cur, user, " zzztestia ", "Probus", "  Zzz   test plant ")
    assert row["result"] == "added"
    assert (row["genus"], row["species"], row["common_name"]) == (
        "Zzztestia",
        "probus",
        "Zzz test plant",
    )
    assert _stored(cur, "Zzztestia") == [("Zzztestia", "probus", "Zzz test plant", user)]


def test_the_common_name_is_stored_with_a_capital_first_letter_only(cur, user):
    row = _add(cur, user, "Zzzcase", "probus", "zzz shepherd's IR64 plant")
    assert row["common_name"] == "Zzz shepherd's IR64 plant"


def test_a_hyphenated_epithet_is_accepted(cur, user):
    assert _add(cur, user, "Zzzcapsella", "bursa-pastoris", "Zzz purse")["result"] == "added"


@pytest.mark.parametrize("role", CALLERS)
def test_each_signed_in_role_can_add(cur, user, role):
    genus = f"Zzz{role.replace('_', '')}"
    assert _add(cur, user, genus, "probus", f"Zzz {role}", role)["result"] == "added"


# --------------------------------------------------------------------------- #
# Duplicates
# --------------------------------------------------------------------------- #


def test_the_same_species_in_any_case_returns_the_stored_one(cur, user):
    first = _add(cur, user, "Zzztestia", "probus", "Zzz test plant")
    again = _add(cur, user, "ZZZTESTIA", "PROBUS", "Another name")
    assert again == {**first, "result": "existing"}
    assert len(_stored(cur, "Zzztestia")) == 1


def test_the_same_common_name_in_any_case_returns_the_stored_one(cur, user):
    first = _add(cur, user, "Zzztestia", "probus", "Zzz test plant")
    again = _add(cur, user, "Zzzother", "alter", "ZZZ TEST PLANT")
    assert again == {**first, "result": "existing"}
    assert _stored(cur, "Zzzother") == []


def test_a_species_stored_in_other_casing_is_found(cur, user):
    seeded = _seed(cur, "zzzlower", "PROBUS", "zzz lower")
    assert _add(cur, user, "Zzzlower", "probus", "Something else")["id"] == seeded


def test_the_scientific_name_wins_over_a_common_name_match(cur, user):
    _seed(cur, "Zzzfirst", "alpha", "Zzz shared")
    by_name = _seed(cur, "Zzzsecond", "beta", "Zzz second")
    assert _add(cur, user, "Zzzsecond", "beta", "Zzz shared")["id"] == by_name


def test_a_removed_species_is_not_returned_and_its_exact_name_is_refused(cur, user):
    _seed(cur, "Zzzgone", "probus", "Zzz gone", deleted=True)
    _refused(cur, user, ("Zzzgone", "probus", "Zzz new"), psycopg.errors.UniqueViolation)
    assert len(_stored(cur, "Zzzgone")) == 1


# --------------------------------------------------------------------------- #
# Other species with nearby names
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "genus, species, common_name",
    [
        ("Zzzshared", "beta", "Zzz beta"),
        ("Zzzother", "alpha", "Zzz other"),
        ("Zzzshared", "alphas", "Zzz alphas"),
    ],
)
def test_only_an_exact_match_counts_as_the_same_species(cur, user, genus, species, common_name):
    # Same genus, same epithet in another genus, or a longer epithet: all different species.
    _seed(cur, "Zzzshared", "alpha", "Zzz shared")
    assert _add(cur, user, genus, species, common_name)["result"] == "added"


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "genus, species, common_name",
    [
        ("", "probus", "Zzz"),
        ("Zzz testia", "probus", "Zzz"),
        ("Zzz1", "probus", "Zzz"),
        (None, "probus", "Zzz"),
        ("Zzztestia", "", "Zzz"),
        ("Zzztestia", "sp.", "Zzz"),
        ("Zzztestia", "-probus", "Zzz"),
        ("Zzztestia", "pro bus", "Zzz"),
        ("Zzztestia", None, "Zzz"),
        ("Zzztestia", "probus", "   "),
        ("Zzztestia", "probus", None),
        ("Zzztestia", "probus", "x" * 101),
    ],
)
def test_bad_names_are_refused_and_nothing_is_stored(cur, user, genus, species, common_name):
    _refused(cur, user, (genus, species, common_name), psycopg.errors.InvalidParameterValue)
    assert _stored(cur, "Zzztestia") == []


def test_a_common_name_of_100_characters_is_accepted(cur, user):
    assert _add(cur, user, "Zzzlong", "probus", "x" * 100)["result"] == "added"


def test_a_caller_who_is_not_signed_in_is_refused(cur):
    _refused(cur, None, ("Zzztestia", "probus", "Zzz"), psycopg.errors.InsufficientPrivilege)
    assert _stored(cur, "Zzztestia") == []


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role, allowed",
    [(r, True) for r in CALLERS] + [(r, False) for r in NOT_CALLERS],
)
def test_only_signed_in_bloom_roles_may_call_it(cur, role, allowed):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, SIG))
    assert cur.fetchone()[0] is allowed


def test_it_is_security_definer_with_a_fixed_search_path(cur):
    cur.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (SIG,))
    secdef, config = cur.fetchone()
    assert secdef is True
    assert config == ["search_path=pg_catalog, public"]


def test_a_direct_insert_as_bloom_user_still_fails(cur, user):
    # Why the function exists: species' created_by trigger needs the auth schema.
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": user, "role": "bloom_user"}),),
    )
    cur.execute("SET LOCAL ROLE bloom_user")
    cur.execute("SAVEPOINT direct")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO public.species (genus, species, common_name) "
                "VALUES ('Zzzdirect', 'probus', 'Zzz direct')"
            )
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT direct")
        cur.execute("RESET ROLE")


# --------------------------------------------------------------------------- #
# Re-run and rollback
# --------------------------------------------------------------------------- #


def test_the_migration_can_be_run_again(cur, user):
    cur.execute(_sql_body(MIGRATION))
    assert _add(cur, user, "Zzzrerun", "probus", "Zzz rerun")["result"] == "added"
    cur.execute("SELECT has_function_privilege('anon', %s, 'EXECUTE')", (SIG,))
    assert cur.fetchone()[0] is False


def test_the_rollback_drops_the_function_and_keeps_the_species(cur, user):
    _add(cur, user, "Zzzkept", "probus", "Zzz kept")
    cur.execute(_sql_body(ROLLBACK))
    cur.execute("SELECT to_regprocedure(%s)", (SIG,))
    assert cur.fetchone()[0] is None
    assert len(_stored(cur, "Zzzkept")) == 1

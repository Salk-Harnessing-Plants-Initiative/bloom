"""
Integration tests for bloom_user_for_email: bloommcp's lookup from a Salk email to the one
active Bloom account with it. It is the gate for the Salk connector, so anything unexpected
returns NULL. Only bloom_agent may call it; the denials are checked with
has_function_privilege, never by calling it as a denied role.

Each test applies the migration inside its own transaction and rolls it back, so the
database is left unchanged.
"""

import uuid

import pytest

from tests.integration.test_rnaseq_runs import _find_one, _sql_body

psycopg = pytest.importorskip("psycopg")
from psycopg.types.json import Jsonb  # noqa: E402

MIGRATION = _find_one("migrations", "*_add_bloom_user_for_email.sql")
ROLLBACK = _find_one("rollbacks", "*_add_bloom_user_for_email_rollback.sql")
FUNCTION = "public.bloom_user_for_email(text)"


def _add_user(cur, email, *, meta=None, **columns) -> str:
    """An auth.users row, confirmed unless email_confirmed_at is given."""
    uid = str(uuid.uuid4())
    values = {
        "id": uid,
        "email": email,
        "raw_app_meta_data": Jsonb(meta or {}),
        "email_confirmed_at": "2026-11-01T00:00:00Z",
        **columns,
    }
    names = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    cur.execute(f"INSERT INTO auth.users ({names}) VALUES ({marks})", list(values.values()))
    return uid


def _lookup(cur, email):
    cur.execute("SELECT public.bloom_user_for_email(%s)", (email,))
    found = cur.fetchone()[0]
    return None if found is None else str(found)


def _salk(name="jdoe") -> str:
    # Unique per test, so rows already in the database never match.
    return f"{name}.{uuid.uuid4().hex[:8]}@salk.edu"


def _upper_local(email: str) -> str:
    """The same address with the local part upper-cased; check_email_trigger needs `@salk.edu`."""
    local, domain = email.split("@")
    return f"{local.upper()}@{domain}"


@pytest.fixture
def cur(pg_conn):
    with pg_conn.cursor() as c:
        c.execute(_sql_body(MIGRATION))
        yield c
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Finding the account
# --------------------------------------------------------------------------- #


def test_it_returns_the_account_for_its_email(cur):
    email = _salk()
    uid = _add_user(cur, email)
    assert _lookup(cur, email) == uid


def test_it_matches_in_any_casing(cur):
    email = _salk()
    uid = _add_user(cur, email)
    assert _lookup(cur, email.upper()) == uid
    assert _lookup(cur, email.replace("@salk.edu", "@Salk.EDU")) == uid


def test_a_mixed_case_stored_address_is_found_in_lower_case(cur):
    stored = _upper_local(_salk())
    uid = _add_user(cur, stored)
    assert _lookup(cur, stored.lower()) == uid


def test_an_unknown_email_returns_null(cur):
    assert _lookup(cur, _salk("nobody")) is None


# --------------------------------------------------------------------------- #
# Input it refuses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "email",
    [
        None,
        "",
        "jdoe",
        "@salk.edu",
        "jdoe@",
        "a@b@salk.edu",
        "jdoe@gmail.com",
        "jdoe@salk.edu.evil.com",
        "jdoe@notsalk.edu",
        " jdoe@salk.edu",
        "jdoe@salk.edu ",
        "jdoe@salk.edu\n",
        "jdoe\r\n@salk.edu",
        "jd oe@salk.edu",
        "jdoe@salk.edu,other@salk.edu",
        "j\tdoe@salk.edu",
        "j\x01doe@salk.edu",
        "a" * 250 + "@salk.edu",
    ],
)
def test_malformed_or_other_domain_input_returns_null(cur, email):
    assert _lookup(cur, email) is None


def _refusable(kind: str) -> str:
    """A storable address (it ends in `@salk.edu`) that only the named check refuses."""
    tag = uuid.uuid4().hex[:8]
    return {
        "leading space": f" jdoe.{tag}@salk.edu",
        "inner space": f"jd oe.{tag}@salk.edu",
        "tab": f"j\tdoe.{tag}@salk.edu",
        "control character": f"j\x01doe.{tag}@salk.edu",
        "line break": f"jdoe.{tag}\r\n@salk.edu",
        "non-breaking space": f"jdoe.{tag}\u00a0@salk.edu",
        "zero-width space": f"jdoe.{tag}\u200b@salk.edu",
        "no local part": "@salk.edu",
        "two addresses": f"a.{tag}@salk.edu,b.{tag}@salk.edu",
        # auth.users.email is varchar(255); 255 is one over the limit.
        "255 characters": f"{tag}{'a' * (255 - len('@salk.edu') - len(tag))}@salk.edu",
    }[kind]


@pytest.mark.parametrize(
    "kind",
    [
        "leading space",
        "inner space",
        "tab",
        "control character",
        "line break",
        "non-breaking space",
        "zero-width space",
        "no local part",
        "two addresses",
        "255 characters",
    ],
)
def test_refused_input_stays_refused_when_an_account_has_it(cur, kind):
    """Each input is stored as an account, so only the function's own check refuses it."""
    email = _refusable(kind)
    _add_user(cur, email)
    assert _lookup(cur, email) is None


def test_a_non_ascii_letter_folding_to_ascii_matches_no_one(cur):
    """lower() turns the Kelvin sign into k; non-ASCII input is refused before that."""
    local = f"jdoe.{uuid.uuid4().hex[:8]}"
    _add_user(cur, f"{local}@salk.edu")
    assert _lookup(cur, f"{local}@sal\u212a.edu") is None


def test_a_254_character_address_is_found(cur):
    tag = uuid.uuid4().hex[:8]
    email = f"{tag}{'a' * (254 - len('@salk.edu') - len(tag))}@salk.edu"
    assert len(email) == 254
    uid = _add_user(cur, email)
    assert _lookup(cur, email) == uid


def test_wildcards_match_no_one(cur):
    """Each pattern would match only the stored account under LIKE or ILIKE."""
    local = f"jdoe.{uuid.uuid4().hex[:8]}"
    _add_user(cur, f"{local}@salk.edu")
    for pattern in (
        f"_{local[1:]}@salk.edu",
        f"%{local[1:]}@salk.edu",
        f"{local[:-2]}%@salk.edu",
        f"{local[:-1]}_@salk.edu",
    ):
        assert _lookup(cur, pattern) is None, pattern


def test_another_domain_never_matches_even_with_an_account(cur):
    """Accounts from before check_email_trigger (2023-07-12) can have other domains."""
    cur.execute("ALTER TABLE auth.users DISABLE TRIGGER check_email_trigger")
    email = f"jdoe.{uuid.uuid4().hex[:8]}@example.org"
    _add_user(cur, email)
    assert _lookup(cur, email) is None


# --------------------------------------------------------------------------- #
# Accounts it refuses
# --------------------------------------------------------------------------- #


def test_a_deleted_account_returns_null(cur):
    email = _salk()
    _add_user(cur, email, deleted_at="2026-11-01T00:00:00Z")
    assert _lookup(cur, email) is None


def test_a_banned_account_returns_null_until_the_ban_ends(cur):
    banned = _salk("banned")
    _add_user(cur, banned, banned_until="2999-01-01T00:00:00Z")
    assert _lookup(cur, banned) is None
    lifted = _salk("lifted")
    uid = _add_user(cur, lifted, banned_until="2020-01-01T00:00:00Z")
    assert _lookup(cur, lifted) == uid


def test_an_unconfirmed_account_returns_null(cur):
    email = _salk()
    _add_user(cur, email, email_confirmed_at=None)
    assert _lookup(cur, email) is None


def test_an_anonymous_account_returns_null(cur):
    email = _salk()
    _add_user(cur, email, is_anonymous=True)
    assert _lookup(cur, email) is None


@pytest.mark.parametrize("flag", [True, "true", "yes", 1, "abc", {}])
def test_the_workflows_login_or_an_unreadable_flag_returns_null(cur, flag):
    email = _salk("workflows")
    _add_user(cur, email, meta={"is_workflows": flag})
    assert _lookup(cur, email) is None


@pytest.mark.parametrize("meta", [{"is_workflows": False}, {"is_workflows": "false"}, {}, None])
def test_an_account_without_the_workflows_flag_is_found(cur, meta):
    email = _salk()
    uid = _add_user(cur, email, raw_app_meta_data=None if meta is None else Jsonb(meta))
    assert _lookup(cur, email) == uid


def test_an_unreadable_flag_on_another_account_breaks_no_lookup(cur):
    _add_user(cur, _salk("odd"), meta={"is_workflows": "abc"})
    email = _salk()
    uid = _add_user(cur, email)
    assert _lookup(cur, email) == uid


def test_two_accounts_with_one_address_return_null(cur):
    """An SSO account can share an address with a password account; never pick one."""
    email = _salk()
    _add_user(cur, email)
    _add_user(cur, _upper_local(email), is_sso_user=True)
    assert _lookup(cur, email) is None


def test_a_removed_duplicate_does_not_hide_the_active_account(cur):
    email = _salk()
    uid = _add_user(cur, email)
    _add_user(cur, _upper_local(email), is_sso_user=True, deleted_at="2026-11-01T00:00:00Z")
    assert _lookup(cur, email) == uid


# --------------------------------------------------------------------------- #
# Who may call it, and how it is built
# --------------------------------------------------------------------------- #


def test_bloommcp_may_call_it(cur):
    cur.execute("SELECT has_function_privilege('bloom_agent', %s, 'EXECUTE')", (FUNCTION,))
    assert cur.fetchone()[0] is True


@pytest.mark.parametrize(
    "role",
    [
        "anon",
        "authenticated",
        "service_role",
        "bloom_user",
        "bloom_writer",
        "bloom_admin",
        "bloom_workflows",
    ],
)
def test_no_other_role_may_call_it(cur, role):
    cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, FUNCTION))
    assert cur.fetchone()[0] is False


def test_it_runs_as_postgres_with_an_empty_search_path(cur):
    cur.execute(
        "SELECT p.prosecdef, p.provolatile, p.proconfig, pg_get_userbyid(p.proowner) "
        "FROM pg_proc p WHERE p.oid = %s::regprocedure",
        (FUNCTION,),
    )
    definer, volatility, config, owner = cur.fetchone()
    assert definer is True
    assert volatility == "s"
    assert config == ['search_path=""']
    assert owner == "postgres"


def test_it_returns_only_an_id(cur):
    cur.execute("SELECT pg_get_function_result(%s::regprocedure)", (FUNCTION,))
    assert cur.fetchone()[0] == "uuid"


def test_reapplying_the_migration_keeps_it_working(cur):
    email = _salk()
    uid = _add_user(cur, email)
    cur.execute(_sql_body(MIGRATION))
    assert _lookup(cur, email) == uid
    cur.execute(
        "SELECT has_function_privilege('bloom_agent', %s, 'EXECUTE'), "
        "has_function_privilege('authenticated', %s, 'EXECUTE')",
        (FUNCTION, FUNCTION),
    )
    assert cur.fetchone() == (True, False)


def test_the_rollback_drops_it_and_can_run_twice(cur):
    cur.execute(_sql_body(ROLLBACK))
    cur.execute(_sql_body(ROLLBACK))
    cur.execute("SELECT to_regprocedure(%s)", (FUNCTION,))
    assert cur.fetchone()[0] is None

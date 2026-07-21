"""Integration tests for the custom_access_token_hook role mapping.

Verifies the runtime role a login token receives, against the live compose DB —
in particular that raw_app_meta_data.is_workflows resolves to bloom_workflows
(the B2 fix), and that the existing admin/writer/user mappings still hold.

Seeds a temporary auth.users row, calls the hook, asserts, then rolls back so no
row persists. Uses the `pg_conn` fixture (connects as supabase_admin).
"""

import json
import uuid

import pytest

# (raw_app_meta_data flags) -> expected JWT role claim.
CASES = [
    ({"is_workflows": True}, "bloom_workflows"),
    ({"is_admin": True}, "bloom_admin"),
    ({"is_writer": True}, "bloom_writer"),
    ({}, "bloom_user"),
    # A service identity is distinct from the human hierarchy — workflows wins.
    ({"is_workflows": True, "is_admin": True}, "bloom_workflows"),
]


@pytest.mark.parametrize("app_meta,expected_role", CASES)
def test_custom_access_token_hook_role_mapping(pg_conn, app_meta, expected_role):
    uid = str(uuid.uuid4())
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO auth.users (id, raw_app_meta_data) VALUES (%s, %s::jsonb)",
                (uid, json.dumps(app_meta)),
            )
            event = {"user_id": uid, "claims": {"sub": uid, "role": "authenticated"}}
            cur.execute(
                "SELECT public.custom_access_token_hook(%s::jsonb)",
                (json.dumps(event),),
            )
            result = cur.fetchone()[0]
    finally:
        # Never persist the seeded user, pass or fail.
        pg_conn.rollback()

    assert result["claims"]["role"] == expected_role

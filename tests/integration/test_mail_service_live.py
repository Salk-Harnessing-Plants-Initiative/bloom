"""bloommcp reaches the internal mail service on the running stack, with the shared secret.

Proves what the unit tests can't: the two containers share a network and the same
MAIL_SERVICE_SECRET, and the route and body match. CI's relay refuses connections (502) and
dev has none (200, not sent), so no real email goes out either way.
"""

from __future__ import annotations

import pytest

from tests.integration.test_bloommcp_data_tools import _run_in_bloommcp

pytestmark = pytest.mark.integration

_SCRIPT = """
import json, os
import httpx
url = os.environ.get("MAIL_SERVICE_URL", "")
secret = os.environ.get("MAIL_SERVICE_SECRET", "")
out = {"configured": bool(url and secret)}
if out["configured"]:
    route = url.rstrip("/") + "/connector-signup"
    ok = {"Authorization": "Bearer " + secret}
    def post(**kw):
        r = httpx.post(route, timeout=30, **kw)
        return {"status": r.status_code, "body": r.json()}
    out["no_secret"] = post(json={"email": "nobody-ci@salk.edu"})
    out["not_salk"] = post(json={"email": "nobody-ci@example.com"}, headers=ok)
    out["salk"] = post(json={"email": "nobody-ci@salk.edu"}, headers=ok)
    out["health"] = httpx.get(url.rstrip("/") + "/health", timeout=10).status_code
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def calls() -> dict:
    result = _run_in_bloommcp(_SCRIPT)
    if not result["configured"]:
        pytest.skip("MAIL_SERVICE_URL or MAIL_SERVICE_SECRET is unset in bloommcp here")
    return result


def test_without_the_secret_the_mail_service_refuses(calls):
    assert calls["no_secret"]["status"] == 401


def test_a_non_salk_address_is_refused(calls):
    assert calls["not_salk"]["status"] == 400


def test_a_salk_address_reaches_the_send_step(calls):
    salk = calls["salk"]
    # CI: the relay refuses (502). Dev: no relay, so not sent (200). Never a 401 or 400.
    assert (salk["status"], salk["body"]) in [
        (502, {"detail": "relay_error"}),
        (200, {"sent": False}),
    ]


def test_health_answers(calls):
    assert calls["health"] == 200

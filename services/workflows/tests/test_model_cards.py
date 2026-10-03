"""Unit tests for model_cards.py — the production model-card listing behind
GET /model-cards (bloom#971 phase 1; caching per design D3 as revised after the
PR #1028 review). HTTP is an httpx.MockTransport injected through
`model_cards._client`; the clock through `_monotonic`/`_utcnow`. Refreshes run on
the module's single worker thread, so tests wait on `_current_refresh()`. No real
network call is made."""

import base64
import datetime
import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

import model_cards
from model_cards import ModelCatalogNotConfigured, ModelCatalogUnavailable

KEY = "test-wandb-key"
REGISTRY_PREFIX = (
    "eberrigan-salk-institute-for-biological-studies-org/"
    "wandb-registry-sleap-roots-models"
)
LATERAL_META = {
    "root_type": "lateral",
    "selectors": [
        {"species": "arabidopsis", "mode": "cylinder", "age_min": 2, "age_max": 14}
    ],
}
PRIMARY_META = {
    "root_type": "primary",
    "selectors": [
        {"species": "soybean", "mode": "cylinder", "age_min": 2, "age_max": 8}
    ],
}
# A pre-selector (flat) card, as the retired collections still carry.
FLAT_META = {
    "root_type": "primary",
    "species": "soybean",
    "mode": "cylinder",
    "age_min": 2,
    "age_max": 8,
}


def _edge(name, meta=None, version_index=0):
    membership = (
        None
        if meta is None
        else {"versionIndex": version_index, "artifact": {"metadata": meta}}
    )
    return {"node": {"name": name, "artifactMembership": membership}}


def _page(edges, has_next=False, end_cursor=None):
    return {
        "data": {
            "project": {
                "artifactType": {
                    "artifactCollections": {
                        "pageInfo": {"endCursor": end_cursor, "hasNextPage": has_next},
                        "edges": edges,
                    }
                }
            }
        }
    }


GOOD_PAGE = _page([_edge("arabidopsis-lateral", LATERAL_META)])


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    model_cards._reset()
    monkeypatch.setenv("WANDB_API_KEY", KEY)
    yield
    model_cards._reset()


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(model_cards, "_monotonic", c)
    return c


@pytest.fixture
def transport(monkeypatch):
    """Install a MockTransport whose responses come from `responder(request)`,
    recording every request and the timeout each client was created with."""
    state = {"requests": [], "client_timeouts": [], "responder": None}

    def handler(request):
        state["requests"].append(request)
        return state["responder"](request)

    def fake_client(timeout):
        state["client_timeouts"].append(timeout)
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(model_cards, "_client", fake_client)
    return state


def _json(payload, status=200):
    return lambda request: httpx.Response(status, json=payload)


def _settle():
    """Wait for the background refresh, if any, to finish."""
    future = model_cards._current_refresh()
    if future is not None:
        try:
            future.result(timeout=5)
        except Exception:
            pass


def _cards():
    return model_cards.list_production_cards()


# --- listing --------------------------------------------------------------


def test_lists_production_memberships_as_cards(transport, clock):
    transport["responder"] = _json(
        _page([_edge("arabidopsis-lateral", LATERAL_META), _edge("old-flat")])
    )

    cards, fetched_at, skipped = _cards()

    assert cards == [
        {
            "root_type": "lateral",
            "registry_id": f"{REGISTRY_PREFIX}/arabidopsis-lateral",
            "version": "v0",
            "selectors": [
                {
                    "species": "arabidopsis",
                    "mode": "cylinder",
                    "age_min": 2,
                    "age_max": 14,
                }
            ],
        }
    ]
    assert skipped == 0
    # The real _utcnow: an ISO-8601 time in UTC.
    assert datetime.datetime.fromisoformat(
        fetched_at
    ).utcoffset() == datetime.timedelta(0)


def test_request_matches_wandbs_own_client(transport, clock):
    transport["responder"] = _json(GOOD_PAGE)

    _cards()

    (request,) = transport["requests"]
    assert request.method == "POST"
    assert str(request.url) == "https://api.wandb.ai/graphql"
    expected = base64.b64encode(f"api:{KEY}".encode()).decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    body = json.loads(request.content)
    assert body["variables"] == {
        "entity": "eberrigan-salk-institute-for-biological-studies-org",
        "project": "wandb-registry-sleap-roots-models",
        "cursor": None,
    }
    assert 'artifactMembership(aliasName: "production")' in body["query"]
    assert "first: 100" in body["query"]
    assert transport["client_timeouts"] == [5]


def test_follows_pages_and_parses_string_metadata(transport, clock):
    pages = iter(
        [
            _page(
                [_edge("arabidopsis-lateral", LATERAL_META)],
                has_next=True,
                end_cursor="c1",
            ),
            _page([_edge("soybean-primary", json.dumps(PRIMARY_META), 3)]),
        ]
    )
    transport["responder"] = lambda request: httpx.Response(200, json=next(pages))

    cards, _, _ = _cards()

    cursors = [
        json.loads(r.content)["variables"]["cursor"] for r in transport["requests"]
    ]
    assert cursors == [None, "c1"]
    assert [(c["root_type"], c["version"]) for c in cards] == [
        ("lateral", "v0"),
        ("primary", "v3"),
    ]


# --- skipped cards --------------------------------------------------------


@pytest.mark.parametrize(
    "bad_edge",
    [
        _edge("retired-flat", FLAT_META),
        _edge("bad-json", "{not json"),
        {
            "node": {
                "name": "no-version",
                "artifactMembership": {"artifact": {"metadata": LATERAL_META}},
            }
        },
    ],
    ids=["flat-metadata", "invalid-json-string", "missing-versionIndex"],
)
def test_skips_and_counts_an_unbuildable_card(transport, clock, caplog, bad_edge):
    transport["responder"] = _json(
        _page([bad_edge, _edge("arabidopsis-lateral", LATERAL_META)])
    )

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        cards, _, skipped = _cards()

    assert [c["registry_id"].rsplit("/", 1)[1] for c in cards] == [
        "arabidopsis-lateral"
    ]
    assert skipped == 1
    assert bad_edge["node"]["name"] in caplog.text


def test_no_readable_card_is_unavailable(transport, clock):
    transport["responder"] = _json(_page([_edge("retired-flat", FLAT_META)]))

    with pytest.raises(ModelCatalogUnavailable):
        _cards()


def test_no_production_membership_is_an_empty_list(transport, clock):
    transport["responder"] = _json(_page([_edge("old-a"), _edge("old-b")]))

    cards, fetched_at, skipped = _cards()

    assert (cards, skipped) == ([], 0)
    assert fetched_at


# --- registry failures ----------------------------------------------------


def _timeout(request):
    raise httpx.ReadTimeout("read timed out", request=request)


def _endless_pages(request):
    return httpx.Response(
        200, json=_page([_edge("x", LATERAL_META)], has_next=True, end_cursor="more")
    )


@pytest.mark.parametrize(
    "responder",
    [
        _json(GOOD_PAGE, status=401),
        lambda request: httpx.Response(302, headers={"Location": "/x"}, json=GOOD_PAGE),
        _json({"detail": "boom"}, status=500),
        _json({"errors": [{"message": "secret-detail"}]}),
        lambda request: httpx.Response(200, text="<html>not json</html>"),
        _json({"data": {"project": None}}),
        _json(_page(None)),
        _json(
            {
                "data": {
                    "project": {
                        "artifactType": {
                            "artifactCollections": {
                                "pageInfo": {"hasNextPage": False},
                                "edges": ["junk"],
                            }
                        }
                    }
                }
            }
        ),
        _timeout,
        _endless_pages,
        _json(_page([_edge("x", LATERAL_META)], has_next=True, end_cursor=None)),
    ],
    ids=[
        "401-with-a-valid-body",
        "302-with-a-valid-body",
        "500",
        "graphql-errors",
        "not-json",
        "project-null",
        "edges-null",
        "non-dict-edge",
        "timeout",
        "too-many-pages",
        "next-page-without-cursor",
    ],
)
def test_registry_failures_are_unavailable_logged_and_never_log_the_key(
    transport, clock, caplog, responder
):
    transport["responder"] = responder

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelCatalogUnavailable):
            _cards()

    assert "Model-card refresh failed" in caplog.text
    assert KEY not in caplog.text


def test_graphql_error_message_is_logged(transport, clock, caplog):
    transport["responder"] = _json({"errors": [{"message": "secret-detail"}]})

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        with pytest.raises(ModelCatalogUnavailable):
            _cards()

    assert "secret-detail" in caplog.text


def test_page_guards(transport, clock):
    transport["responder"] = _endless_pages
    with pytest.raises(ModelCatalogUnavailable):
        _cards()
    assert len(transport["requests"]) == model_cards.MAX_PAGES == 20

    model_cards._reset()
    transport["requests"].clear()
    transport["responder"] = _json(
        _page([_edge("x", LATERAL_META)], has_next=True, end_cursor=None)
    )
    with pytest.raises(ModelCatalogUnavailable):
        _cards()
    assert len(transport["requests"]) == 1


# --- deadline -------------------------------------------------------------


def test_limits_are_five_and_fifteen_seconds():
    assert model_cards.REQUEST_TIMEOUT_SECONDS == 5
    assert model_cards.REFRESH_DEADLINE_SECONDS == 15


def test_later_pages_get_a_shrinking_timeout(transport, clock):
    def slow_page(request):
        clock.now += 12
        return httpx.Response(
            200,
            json=_page([_edge("x", LATERAL_META)], has_next=True, end_cursor="more"),
        )

    transport["responder"] = slow_page

    with pytest.raises(ModelCatalogUnavailable):
        _cards()

    # Page 1 at t=0 with 5 s, page 2 at t=12 with 3 s left, none at t=24.
    read_timeouts = [r.extensions["timeout"]["read"] for r in transport["requests"]]
    assert read_timeouts == [5, 3]


def test_a_trickling_body_is_cut_off_at_the_deadline(transport, clock):
    body = json.dumps(GOOD_PAGE).encode()

    def trickle():
        for i in range(0, len(body), 16):
            clock.now += 2  # each chunk arrives 2 s after the last
            yield body[i : i + 16]

    transport["responder"] = lambda request: httpx.Response(200, content=trickle())

    started = clock.now
    with pytest.raises(ModelCatalogUnavailable):
        _cards()

    assert clock.now - started <= model_cards.REFRESH_DEADLINE_SECONDS + 2


# --- configuration --------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_is_not_configured_and_sends_nothing(
    monkeypatch, transport, clock, value
):
    if value is None:
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
    else:
        monkeypatch.setenv("WANDB_API_KEY", value)
    transport["responder"] = _json(GOOD_PAGE)

    with pytest.raises(ModelCatalogNotConfigured):
        _cards()

    assert transport["requests"] == []


def test_key_is_sent_unchanged(monkeypatch, transport, clock):
    monkeypatch.setenv("WANDB_API_KEY", " key\n")
    transport["responder"] = _json(_page([]))

    _cards()

    expected = base64.b64encode(b"api: key\n").decode()
    assert transport["requests"][0].headers["authorization"] == f"Basic {expected}"


# --- fresh and stale ------------------------------------------------------


def test_cache_limits():
    assert model_cards.FRESH_SECONDS == 300
    assert model_cards.MAX_STALE_SECONDS == 3600
    assert model_cards.COLD_WAIT_SECONDS == 6
    assert model_cards.BACKOFF_SECONDS == 60
    assert model_cards.AUTH_BACKOFF_SECONDS == 300


def test_fresh_listing_is_served_without_a_request(transport, clock):
    transport["responder"] = _json(GOOD_PAGE)

    _, first, _ = _cards()
    clock.now += 60
    _, second, _ = _cards()

    assert len(transport["requests"]) == 1
    assert second == first


def test_stale_listing_is_served_at_once_while_one_refresh_runs(
    monkeypatch, transport, clock
):
    stamps = iter(
        [
            datetime.datetime(2026, 10, 3, 12, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 10, 3, 12, 5, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(model_cards, "_utcnow", lambda: next(stamps))
    transport["responder"] = _json(GOOD_PAGE)
    _, first, _ = _cards()

    entered = threading.Event()
    release = threading.Event()

    def blocking(request):
        entered.set()
        release.wait(timeout=5)
        return httpx.Response(200, json=GOOD_PAGE)

    transport["responder"] = blocking
    clock.now += 301
    try:
        _, served, _ = _cards()
        assert served == first
        assert entered.wait(5)
        _, again, _ = _cards()  # a second request during the refresh
        assert again == first
        assert len(transport["requests"]) == 2  # the first listing plus one refresh
    finally:
        release.set()
    _settle()

    _, refreshed, _ = _cards()
    assert refreshed != first
    assert len(transport["requests"]) == 2


def test_a_failed_refresh_keeps_the_last_good_listing_and_backs_off(transport, clock):
    transport["responder"] = _json(GOOD_PAGE)
    _, first, _ = _cards()

    transport["responder"] = _json({}, status=500)
    clock.now += 301
    assert _cards()[1] == first  # starts the refresh, which fails
    _settle()
    assert len(transport["requests"]) == 2

    clock.now += 30  # inside the 60 s backoff
    assert _cards()[1] == first
    _settle()
    assert len(transport["requests"]) == 2

    clock.now += 31  # past the backoff
    assert _cards()[1] == first
    _settle()
    assert len(transport["requests"]) == 3


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_rate_limit_failures_back_off_longer(transport, clock, status):
    transport["responder"] = _json({}, status=status)
    with pytest.raises(ModelCatalogUnavailable):
        _cards()

    clock.now += 61
    with pytest.raises(ModelCatalogUnavailable):
        _cards()
    assert len(transport["requests"]) == 1

    clock.now += 240  # 301 s after the failure
    with pytest.raises(ModelCatalogUnavailable):
        _cards()
    assert len(transport["requests"]) == 2


def test_a_very_old_listing_is_not_served(transport, clock):
    transport["responder"] = _json(GOOD_PAGE)
    _cards()

    transport["responder"] = _json({}, status=500)
    clock.now += 3601
    with pytest.raises(ModelCatalogUnavailable):
        _cards()


# --- cold requests --------------------------------------------------------


def test_concurrent_cold_requests_share_one_listing(transport, clock):
    entered = threading.Event()
    release = threading.Event()

    def blocking(request):
        entered.set()
        release.wait(timeout=5)
        return httpx.Response(200, json=GOOD_PAGE)

    transport["responder"] = blocking
    barrier = threading.Barrier(5)
    results = []

    def call():
        barrier.wait(timeout=5)
        results.append(_cards()[0])

    threads = [threading.Thread(target=call) for _ in range(5)]
    for t in threads:
        t.start()
    assert entered.wait(5)
    time.sleep(0.2)  # let the other four reach the wait
    release.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert len(transport["requests"]) == 1
    assert len(results) == 5
    assert all(r == results[0] for r in results)


def test_a_slow_cold_refresh_answers_503_at_the_limit_and_keeps_running(
    monkeypatch, transport, clock
):
    monkeypatch.setattr(model_cards, "COLD_WAIT_SECONDS", 0.2)
    release = threading.Event()

    def blocking(request):
        release.wait(timeout=5)
        return httpx.Response(200, json=GOOD_PAGE)

    transport["responder"] = blocking
    started = time.monotonic()
    try:
        with pytest.raises(ModelCatalogUnavailable):
            _cards()
        assert time.monotonic() - started < 2
    finally:
        release.set()
    _settle()

    cards, _, _ = _cards()  # the refresh finished in the background
    assert len(cards) == 1
    assert len(transport["requests"]) == 1


def test_a_cold_request_during_a_backoff_fails_at_once(transport, clock):
    transport["responder"] = _json({}, status=500)
    with pytest.raises(ModelCatalogUnavailable):
        _cards()

    clock.now += 10
    started = time.monotonic()
    with pytest.raises(ModelCatalogUnavailable):
        _cards()
    assert time.monotonic() - started < 1
    assert len(transport["requests"]) == 1


# --- warm -----------------------------------------------------------------


def test_warm_without_a_key_does_nothing(monkeypatch, transport, clock):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    transport["responder"] = _json(_page([]))

    model_cards.warm()
    _settle()

    assert transport["requests"] == []


def test_warm_fills_the_cache_without_waiting(transport, clock):
    transport["responder"] = _json(GOOD_PAGE)

    model_cards.warm()
    _settle()
    _cards()

    assert len(transport["requests"]) == 1


def test_warm_swallows_a_failure(transport, clock, caplog):
    transport["responder"] = _json({}, status=500)

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        model_cards.warm()
        _settle()

    assert "Model-card refresh failed" in caplog.text


# --- no wandb import ------------------------------------------------------


def test_importing_the_service_does_not_import_wandb():
    service_dir = Path(model_cards.__file__).resolve().parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import main, model_cards, sys; assert 'wandb' not in sys.modules",
        ],
        cwd=service_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

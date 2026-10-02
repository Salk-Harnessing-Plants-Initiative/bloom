"""Unit tests for model_cards.py — the production model-card listing behind
GET /model-cards (bloom#971 phase 1). HTTP is an httpx.MockTransport injected
through `model_cards._client`; the clock through `_monotonic`/`_utcnow`. No real
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


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    model_cards._reset_cache()
    monkeypatch.setenv("WANDB_API_KEY", KEY)
    yield
    model_cards._reset_cache()


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


# --- 2.1 listing ----------------------------------------------------------


def test_lists_production_memberships_as_cards(transport, clock):
    transport["responder"] = _json(
        _page([_edge("arabidopsis-lateral", LATERAL_META), _edge("old-flat")])
    )

    cards, fetched_at = model_cards.list_production_cards()

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
    assert isinstance(fetched_at, str)


def test_request_matches_wandbs_own_client(transport, clock):
    transport["responder"] = _json(_page([_edge("arabidopsis-lateral", LATERAL_META)]))

    model_cards.list_production_cards()

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


# --- 2.2 paging and string metadata ---------------------------------------


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

    cards, _ = model_cards.list_production_cards()

    cursors = [
        json.loads(r.content)["variables"]["cursor"] for r in transport["requests"]
    ]
    assert cursors == [None, "c1"]
    assert [(c["root_type"], c["version"]) for c in cards] == [
        ("lateral", "v0"),
        ("primary", "v3"),
    ]


# --- 2.3 validation -------------------------------------------------------


def test_skips_a_non_conforming_card_and_names_it(transport, clock, caplog):
    transport["responder"] = _json(
        _page(
            [
                _edge("retired-flat", FLAT_META),
                _edge("arabidopsis-lateral", LATERAL_META),
            ]
        )
    )

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        cards, _ = model_cards.list_production_cards()

    assert [c["registry_id"].rsplit("/", 1)[1] for c in cards] == [
        "arabidopsis-lateral"
    ]
    assert "retired-flat" in caplog.text


def test_no_readable_card_is_unavailable(transport, clock):
    transport["responder"] = _json(_page([_edge("retired-flat", FLAT_META)]))

    with pytest.raises(ModelCatalogUnavailable):
        model_cards.list_production_cards()


def test_no_production_membership_is_an_empty_list(transport, clock):
    transport["responder"] = _json(_page([_edge("old-a"), _edge("old-b")]))

    cards, fetched_at = model_cards.list_production_cards()

    assert cards == []
    assert fetched_at


# --- 2.4 errors -----------------------------------------------------------


def _endless_pages(request):
    return httpx.Response(
        200, json=_page([_edge("x", LATERAL_META)], has_next=True, end_cursor="more")
    )


def _timeout(request):
    raise httpx.ReadTimeout("read timed out", request=request)


@pytest.mark.parametrize(
    "responder",
    [
        _json({"detail": "nope"}, status=401),
        _json({"detail": "boom"}, status=500),
        _json({"errors": [{"message": "secret-detail"}]}),
        lambda request: httpx.Response(200, text="<html>not json</html>"),
        _json({"data": {"project": None}}),
        _timeout,
        _endless_pages,
    ],
    ids=[
        "401",
        "500",
        "graphql-errors",
        "not-json",
        "unexpected-shape",
        "timeout",
        "too-many-pages",
    ],
)
def test_registry_failures_are_unavailable_and_never_log_the_key(
    transport, clock, caplog, responder
):
    transport["responder"] = responder

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelCatalogUnavailable):
            model_cards.list_production_cards()

    assert KEY not in caplog.text


def test_graphql_error_message_is_logged(transport, clock, caplog):
    transport["responder"] = _json({"errors": [{"message": "secret-detail"}]})

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        with pytest.raises(ModelCatalogUnavailable):
            model_cards.list_production_cards()

    assert "secret-detail" in caplog.text


def test_page_guard_stops_at_max_pages(transport, clock):
    transport["responder"] = _endless_pages

    with pytest.raises(ModelCatalogUnavailable):
        model_cards.list_production_cards()

    assert len(transport["requests"]) == model_cards.MAX_PAGES == 20


# --- 2.5 deadline ---------------------------------------------------------


def test_timeouts_are_five_and_fifteen_seconds():
    assert model_cards.REQUEST_TIMEOUT_SECONDS == 5
    assert model_cards.REFRESH_DEADLINE_SECONDS == 15


def test_listing_stops_at_the_deadline(transport, clock):
    def slow_page(request):
        clock.now += 10
        return httpx.Response(
            200,
            json=_page([_edge("x", LATERAL_META)], has_next=True, end_cursor="more"),
        )

    transport["responder"] = slow_page

    with pytest.raises(ModelCatalogUnavailable):
        model_cards.list_production_cards()

    # Page 1 at t=0, page 2 at t=10 (5 s left), none at t=20.
    assert len(transport["requests"]) == 2
    assert transport["client_timeouts"] == [5]
    read_timeouts = [r.extensions["timeout"]["read"] for r in transport["requests"]]
    assert all(t <= 5 for t in read_timeouts)


# --- 2.6 configuration ----------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_key_is_not_configured_and_sends_nothing(
    monkeypatch, transport, clock, value
):
    if value is None:
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
    else:
        monkeypatch.setenv("WANDB_API_KEY", value)
    transport["responder"] = _json(_page([]))

    with pytest.raises(ModelCatalogNotConfigured):
        model_cards.list_production_cards()

    assert transport["requests"] == []


def test_key_is_sent_unchanged(monkeypatch, transport, clock):
    monkeypatch.setenv("WANDB_API_KEY", " key\n")
    transport["responder"] = _json(_page([]))

    model_cards.list_production_cards()

    expected = base64.b64encode(b"api: key\n").decode()
    assert transport["requests"][0].headers["authorization"] == f"Basic {expected}"


# --- 2.7 cache ------------------------------------------------------------


def test_cache_serves_repeat_calls_until_it_expires(monkeypatch, transport, clock):
    stamps = iter(
        [
            datetime.datetime(2026, 10, 2, 12, 0, tzinfo=datetime.timezone.utc),
            datetime.datetime(2026, 10, 2, 12, 5, tzinfo=datetime.timezone.utc),
        ]
    )
    monkeypatch.setattr(model_cards, "_utcnow", lambda: next(stamps))
    transport["responder"] = _json(_page([_edge("arabidopsis-lateral", LATERAL_META)]))

    _, first = model_cards.list_production_cards()
    clock.now += 60
    _, second = model_cards.list_production_cards()
    assert len(transport["requests"]) == 1
    assert second == first

    clock.now += 240  # exactly 300.0 s after the listing
    _, third = model_cards.list_production_cards()
    assert len(transport["requests"]) == 2
    assert third != first
    parsed = datetime.datetime.fromisoformat(third)
    assert parsed.utcoffset() == datetime.timedelta(0)


def test_a_failure_is_not_cached(transport, clock):
    responses = iter(
        [
            httpx.Response(500, json={}),
            httpx.Response(
                200, json=_page([_edge("arabidopsis-lateral", LATERAL_META)])
            ),
        ]
    )
    transport["responder"] = lambda request: next(responses)

    with pytest.raises(ModelCatalogUnavailable):
        model_cards.list_production_cards()
    cards, _ = model_cards.list_production_cards()

    assert len(cards) == 1
    assert len(transport["requests"]) == 2


def test_an_expired_listing_is_not_served_after_a_failed_refresh(transport, clock):
    responses = iter(
        [
            httpx.Response(
                200, json=_page([_edge("arabidopsis-lateral", LATERAL_META)])
            ),
            httpx.Response(500, json={}),
        ]
    )
    transport["responder"] = lambda request: next(responses)

    model_cards.list_production_cards()
    clock.now += 301
    with pytest.raises(ModelCatalogUnavailable):
        model_cards.list_production_cards()


# --- 2.8 single flight ----------------------------------------------------


def test_concurrent_cold_calls_share_one_listing(transport, clock):
    entered = threading.Event()
    release = threading.Event()

    def blocking(request):
        entered.set()
        release.wait(timeout=5)
        return httpx.Response(
            200, json=_page([_edge("arabidopsis-lateral", LATERAL_META)])
        )

    transport["responder"] = blocking
    barrier = threading.Barrier(5)
    results = []

    def call():
        barrier.wait(timeout=5)
        results.append(model_cards.list_production_cards()[0])

    threads = [threading.Thread(target=call) for _ in range(5)]
    for t in threads:
        t.start()
    assert entered.wait(5)
    time.sleep(0.2)  # let the other four queue on the lock
    release.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive()

    assert len(transport["requests"]) == 1
    assert len(results) == 5
    assert all(r == results[0] for r in results)


# --- 2.9 bounded wait -----------------------------------------------------


def test_lock_wait_is_six_seconds():
    assert model_cards.LOCK_WAIT_SECONDS == 6


def test_a_waiter_gives_up_then_the_cache_serves(monkeypatch, transport, clock):
    monkeypatch.setattr(model_cards, "LOCK_WAIT_SECONDS", 0.2)
    entered = threading.Event()
    release = threading.Event()

    def blocking(request):
        entered.set()
        release.wait(timeout=5)
        return httpx.Response(
            200, json=_page([_edge("arabidopsis-lateral", LATERAL_META)])
        )

    transport["responder"] = blocking
    leader = threading.Thread(target=model_cards.list_production_cards)
    leader.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        with pytest.raises(ModelCatalogUnavailable):
            model_cards.list_production_cards()
        assert time.monotonic() - started < 2
    finally:
        release.set()
        leader.join(timeout=5)
    assert not leader.is_alive()

    cards, _ = model_cards.list_production_cards()
    assert len(cards) == 1
    assert len(transport["requests"]) == 1


# --- 2.10 warm ------------------------------------------------------------


def test_warm_without_a_key_does_nothing(monkeypatch, transport, clock):
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    transport["responder"] = _json(_page([]))

    model_cards.warm()

    assert transport["requests"] == []


def test_warm_swallows_a_failure_and_leaves_the_cache_empty(transport, clock, caplog):
    responses = iter(
        [
            httpx.Response(500, json={}),
            httpx.Response(
                200, json=_page([_edge("arabidopsis-lateral", LATERAL_META)])
            ),
        ]
    )
    transport["responder"] = lambda request: next(responses)

    with caplog.at_level(logging.WARNING, logger="model_cards"):
        model_cards.warm()

    assert caplog.records
    cards, _ = model_cards.list_production_cards()
    assert len(cards) == 1
    assert len(transport["requests"]) == 2


def test_warm_fills_the_cache(transport, clock):
    transport["responder"] = _json(_page([_edge("arabidopsis-lateral", LATERAL_META)]))

    model_cards.warm()
    model_cards.list_production_cards()

    assert len(transport["requests"]) == 1


# --- 2.11 no wandb import -------------------------------------------------


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

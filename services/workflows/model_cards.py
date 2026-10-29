"""
The production model cards behind GET /model-cards (bloom#971 phase 1).

The pipeline confirm dialog compares each scan group's age with these cards'
age windows, to warn about scans the cluster predicts past their models'
validated age (with the species' highest window) or can't predict at all.

The cards live only in the wandb model registry. This module asks for them the
way wandb's own client does -- POST {wandb}/graphql with HTTP Basic auth
("api", WANDB_API_KEY) -- rather than through the `wandb` library: from 0.26
the library routes its API through a bundled Go service (wandb-core), and older
releases retry internally for up to 7 days whatever timeout is passed. One
query reads every model collection's "production" alias directly
(`artifactMembership(aliasName:)`, the field wandb's generated operations use
to resolve name:alias), so a listing is a couple of requests.

Serving (design D3, revised after the PR #1028 review):
- A listing is fresh for FRESH_SECONDS and served without contacting wandb.
- Until MAX_STALE_SECONDS it is still served at once while one background
  refresh runs; after that it isn't served.
- Refreshes run on one worker thread, so at most one listing is in progress
  and a hung one never holds a request thread. A request with nothing to
  serve waits for the refresh at most COLD_WAIT_SECONDS.
- A failed refresh starts a backoff (longer after 401/403/429) and never
  replaces a held listing.
- Each listing streams its responses and checks REFRESH_DEADLINE_SECONDS as
  bytes arrive; httpx's per-phase timeout covers connect, write and stalls.
  httpx doesn't retry.
"""

import concurrent.futures
import datetime
import json
import logging
import os
import threading
import time
from dataclasses import dataclass

import httpx
from pydantic import ValidationError
from sleap_roots_contracts import ModelCard

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.wandb.ai/graphql"
ENTITY = "eberrigan-salk-institute-for-biological-studies-org"
PROJECT = "wandb-registry-sleap-roots-models"
PAGE_SIZE = 100
MAX_PAGES = 20
REQUEST_TIMEOUT_SECONDS = 5
REFRESH_DEADLINE_SECONDS = 15
FRESH_SECONDS = 300
MAX_STALE_SECONDS = 3600
COLD_WAIT_SECONDS = 6
BACKOFF_SECONDS = 60
AUTH_BACKOFF_SECONDS = 300
_AUTH_OR_RATE_STATUSES = frozenset({401, 403, 429})

QUERY = f"""
query ProductionModelCards($entity: String!, $project: String!, $cursor: String) {{
  project(name: $project, entityName: $entity) {{
    artifactType(name: "model") {{
      artifactCollections(after: $cursor, first: {PAGE_SIZE}) {{
        pageInfo {{ endCursor hasNextPage }}
        edges {{
          node {{
            name
            artifactMembership(aliasName: "production") {{
              versionIndex
              artifact {{ metadata }}
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


class ModelCatalogNotConfigured(Exception):
    """WANDB_API_KEY is unset or blank in this environment."""


class ModelCatalogUnavailable(Exception):
    """No card list can be served: the registry couldn't be read, held no
    readable production card, or a refresh is still running or backing off."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _monotonic() -> float:
    return time.monotonic()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def _api_key() -> str:
    key = os.environ.get("WANDB_API_KEY")
    if not key or not key.strip():
        raise ModelCatalogNotConfigured("WANDB_API_KEY is not set")
    # Sent as-is: wandb's own clients use the raw value too.
    return key


def _card_dict(card: ModelCard) -> dict:
    return {
        "root_type": card.root_type,
        "registry_id": card.registry_id,
        "version": card.version,
        "selectors": [
            {
                "species": s.species,
                "mode": s.mode,
                "age_min": s.age_min,
                "age_max": s.age_max,
            }
            for s in card.selectors
        ],
    }


def _post_page(
    client: httpx.Client, key: str, cursor: str | None, started: float
) -> dict:
    """One page of artifactCollections, or ModelCatalogUnavailable. The body is
    streamed so the listing deadline holds while it arrives."""
    remaining = REFRESH_DEADLINE_SECONDS - (_monotonic() - started)
    if remaining <= 0:
        raise ModelCatalogUnavailable(f"listing exceeded {REFRESH_DEADLINE_SECONDS}s")
    payload = {
        "query": QUERY,
        "variables": {"entity": ENTITY, "project": PROJECT, "cursor": cursor},
    }
    try:
        with client.stream(
            "POST",
            GRAPHQL_URL,
            json=payload,
            auth=("api", key),
            timeout=min(REQUEST_TIMEOUT_SECONDS, remaining),
        ) as response:
            if response.status_code != 200:
                hint = (
                    " (check WANDB_API_KEY)"
                    if response.status_code in (401, 403)
                    else ""
                )
                raise ModelCatalogUnavailable(
                    f"wandb answered HTTP {response.status_code}{hint}",
                    status=response.status_code,
                )
            chunks = []
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                if _monotonic() - started > REFRESH_DEADLINE_SECONDS:
                    raise ModelCatalogUnavailable(
                        f"listing exceeded {REFRESH_DEADLINE_SECONDS}s while a response arrived"
                    )
    except httpx.HTTPError as exc:
        raise ModelCatalogUnavailable(
            f"wandb request failed: {type(exc).__name__}"
        ) from exc
    try:
        body = json.loads(b"".join(chunks))
    except ValueError as exc:
        raise ModelCatalogUnavailable("wandb answered with a non-JSON body") from exc
    if not isinstance(body, dict):
        raise ModelCatalogUnavailable("wandb answered with an unexpected body")
    if body.get("errors"):
        messages = [
            e.get("message") if isinstance(e, dict) else str(e) for e in body["errors"]
        ]
        raise ModelCatalogUnavailable(f"wandb GraphQL errors: {messages}")
    project = (body.get("data") or {}).get("project")
    if project is None:
        raise ModelCatalogUnavailable(
            f"project {ENTITY}/{PROJECT} isn't visible to this key, or doesn't exist"
        )
    try:
        connection = project["artifactType"]["artifactCollections"]
        edges = connection["edges"]
        has_next = connection["pageInfo"]["hasNextPage"]
    except (KeyError, TypeError) as exc:
        raise ModelCatalogUnavailable(
            "wandb answered with an unexpected shape"
        ) from exc
    if (
        not isinstance(edges, list)
        or not all(isinstance(e, dict) for e in edges)
        or not isinstance(has_next, bool)
    ):
        raise ModelCatalogUnavailable("wandb answered with an unexpected shape")
    return connection


def _list_from_registry(key: str) -> tuple[list[dict], int]:
    """Every collection's production card, validated, within the deadline, and
    how many production memberships couldn't be built into a card."""
    started = _monotonic()
    cards: list[dict] = []
    skipped = 0
    cursor: str | None = None
    with _client(REQUEST_TIMEOUT_SECONDS) as client:
        for page in range(MAX_PAGES):
            connection = _post_page(client, key, cursor, started)
            for edge in connection["edges"]:
                node = edge.get("node") or {}
                name = node.get("name")
                membership = node.get("artifactMembership")
                if not membership:
                    continue
                try:
                    metadata = (membership.get("artifact") or {}).get("metadata")
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    card = ModelCard.model_validate(
                        {
                            **(metadata or {}),
                            "registry_id": f"{ENTITY}/{PROJECT}/{name}",
                            "version": f"v{membership['versionIndex']}",
                        }
                    )
                except (ValidationError, ValueError, TypeError, KeyError) as exc:
                    skipped += 1
                    logger.warning(
                        "Skipping production model card %r: %s",
                        name,
                        str(exc).splitlines()[0],
                    )
                    continue
                cards.append(_card_dict(card))
            page_info = connection["pageInfo"]
            if not page_info["hasNextPage"]:
                break
            cursor = page_info.get("endCursor")
            if not cursor:
                raise ModelCatalogUnavailable(
                    f"page {page + 1} has a next page but no endCursor"
                )
        else:
            raise ModelCatalogUnavailable(f"more than {MAX_PAGES} pages of collections")
    if skipped and not cards:
        raise ModelCatalogUnavailable(
            f"none of the {skipped} production model cards is readable"
        )
    return cards, skipped


@dataclass(frozen=True)
class _Listing:
    fetched_monotonic: float
    cards: list[dict]
    fetched_at: str
    skipped: int

    def view(self) -> tuple[list[dict], str, int]:
        return self.cards, self.fetched_at, self.skipped


_state_lock = threading.Lock()
_listing: _Listing | None = None
_backoff_until = 0.0
_refresh: concurrent.futures.Future | None = None
_executor: concurrent.futures.ThreadPoolExecutor | None = None


def _worker() -> concurrent.futures.ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="model-cards"
        )
    return _executor


def _run_refresh(key: str) -> None:
    """One listing on the worker thread. Updates the listing or the backoff."""
    global _listing, _backoff_until
    try:
        cards, skipped = _list_from_registry(key)
    except Exception as exc:
        status = getattr(exc, "status", None)
        backoff = (
            AUTH_BACKOFF_SECONDS
            if status in _AUTH_OR_RATE_STATUSES
            else BACKOFF_SECONDS
        )
        with _state_lock:
            _backoff_until = _monotonic() + backoff
        if isinstance(exc, ModelCatalogUnavailable):
            logger.warning("Model-card refresh failed (retry in %ss): %s", backoff, exc)
        else:
            logger.exception("Model-card refresh failed (retry in %ss)", backoff)
        raise
    listing = _Listing(_monotonic(), cards, _utcnow().isoformat(), skipped)
    with _state_lock:
        _listing = listing
        _backoff_until = 0.0
    logger.info(
        "Model-card refresh listed %d production cards (%d skipped)",
        len(cards),
        skipped,
    )


def _start_refresh_locked(key: str, now: float) -> concurrent.futures.Future | None:
    """The running refresh, a newly started one, or None while backing off.
    Call with _state_lock held."""
    global _refresh
    if _refresh is not None and not _refresh.done():
        return _refresh
    if now < _backoff_until:
        return None
    _refresh = _worker().submit(_run_refresh, key)
    return _refresh


def list_production_cards() -> tuple[list[dict], str, int]:
    """The production cards, when they were read (ISO-8601 UTC), and how many
    production memberships were skipped.

    Raises ModelCatalogNotConfigured without a key, and ModelCatalogUnavailable
    when no listing can be served.
    """
    key = _api_key()
    now = _monotonic()
    with _state_lock:
        listing = _listing
        age = None if listing is None else now - listing.fetched_monotonic
        if listing is not None and age < FRESH_SECONDS:
            return listing.view()
        refresh = _start_refresh_locked(key, now)
        if listing is not None and age < MAX_STALE_SECONDS:
            return listing.view()
    if refresh is None:
        raise ModelCatalogUnavailable("the last model-card refresh failed; backing off")
    try:
        refresh.result(timeout=COLD_WAIT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        raise ModelCatalogUnavailable("a model-card refresh is still running") from exc
    except Exception as exc:
        raise ModelCatalogUnavailable("the model-card refresh failed") from exc
    with _state_lock:
        listing = _listing
    if listing is None or _monotonic() - listing.fetched_monotonic >= MAX_STALE_SECONDS:
        raise ModelCatalogUnavailable("no model-card listing to serve")
    return listing.view()


def warm() -> None:
    """Start one background refresh at startup; never waits, never raises."""
    try:
        key = _api_key()
    except ModelCatalogNotConfigured:
        return
    with _state_lock:
        _start_refresh_locked(key, _monotonic())


def _current_refresh() -> concurrent.futures.Future | None:
    """For tests: the most recent refresh."""
    return _refresh


def _reset() -> None:
    """For tests: forget the listing, the backoff and the worker."""
    global _listing, _backoff_until, _refresh, _executor
    with _state_lock:
        _listing = None
        _backoff_until = 0.0
        _refresh = None
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)

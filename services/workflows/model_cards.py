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

httpx doesn't retry, so the bounds below hold: 5 s per request and 15 s for the
whole listing. A successful listing is cached for 300 s; one refresh runs at a
time, and a request that can't get the refresh lock within LOCK_WAIT_SECONDS
fails rather than queueing in the threadpool. See
openspec/changes/add-cyl-pipeline-model-window-warning/design.md (D1, D3).
"""

import datetime
import json
import logging
import os
import threading
import time

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
CACHE_TTL_SECONDS = 300
LOCK_WAIT_SECONDS = 6

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
    """The registry couldn't be read, or held no readable production card."""


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


def _collections_page(client: httpx.Client, key: str, cursor, timeout: float) -> dict:
    """One page of artifactCollections, or ModelCatalogUnavailable."""
    try:
        response = client.post(
            GRAPHQL_URL,
            json={
                "query": QUERY,
                "variables": {"entity": ENTITY, "project": PROJECT, "cursor": cursor},
            },
            auth=("api", key),
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ModelCatalogUnavailable(
            f"wandb request failed: {type(exc).__name__}"
        ) from exc
    if response.status_code != 200:
        raise ModelCatalogUnavailable(f"wandb answered HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise ModelCatalogUnavailable("wandb answered with a non-JSON body") from exc
    if not isinstance(body, dict):
        raise ModelCatalogUnavailable("wandb answered with an unexpected body")
    if body.get("errors"):
        messages = [
            e.get("message") if isinstance(e, dict) else str(e) for e in body["errors"]
        ]
        raise ModelCatalogUnavailable(f"wandb GraphQL errors: {messages}")
    try:
        connection = body["data"]["project"]["artifactType"]["artifactCollections"]
        connection["edges"], connection["pageInfo"]["hasNextPage"]
    except (KeyError, TypeError) as exc:
        raise ModelCatalogUnavailable(
            "wandb answered with an unexpected shape"
        ) from exc
    return connection


def _list_from_registry(key: str) -> list[dict]:
    """Every collection's production card, validated, within the deadline."""
    started = _monotonic()
    cards: list[dict] = []
    seen_production = 0
    cursor = None
    with _client(REQUEST_TIMEOUT_SECONDS) as client:
        for _ in range(MAX_PAGES):
            remaining = REFRESH_DEADLINE_SECONDS - (_monotonic() - started)
            if remaining <= 0:
                raise ModelCatalogUnavailable(
                    f"listing exceeded {REFRESH_DEADLINE_SECONDS}s"
                )
            connection = _collections_page(
                client, key, cursor, min(REQUEST_TIMEOUT_SECONDS, remaining)
            )
            for edge in connection["edges"]:
                node = (edge or {}).get("node") or {}
                name = node.get("name")
                membership = node.get("artifactMembership")
                if not membership:
                    continue
                seen_production += 1
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
        else:
            raise ModelCatalogUnavailable(f"more than {MAX_PAGES} pages of collections")
    if seen_production and not cards:
        raise ModelCatalogUnavailable(
            f"none of the {seen_production} production model cards is readable"
        )
    return cards


# (fetched_monotonic, cards, fetched_at) of the last successful listing.
_cache: tuple[float, list[dict], str] | None = None
_lock = threading.Lock()


def _fresh() -> tuple[list[dict], str] | None:
    entry = _cache
    if entry is None or _monotonic() - entry[0] >= CACHE_TTL_SECONDS:
        return None
    return entry[1], entry[2]


def list_production_cards() -> tuple[list[dict], str]:
    """The production cards and when they were read (ISO-8601 UTC).

    Raises ModelCatalogNotConfigured without a key, and ModelCatalogUnavailable
    when the registry can't be read or the refresh lock isn't free in time.
    """
    global _cache
    key = _api_key()
    cached = _fresh()
    if cached is not None:
        return cached
    if not _lock.acquire(timeout=LOCK_WAIT_SECONDS):
        raise ModelCatalogUnavailable("another model-card refresh is still running")
    try:
        cached = _fresh()
        if cached is not None:
            return cached
        try:
            cards = _list_from_registry(key)
        except ModelCatalogUnavailable as exc:
            # The cause is logged here, never returned to the caller (GET /model-cards
            # answers a fixed 503). httpx errors don't carry the auth header.
            logger.warning("Model-card listing failed: %s", exc)
            raise
        fetched_at = _utcnow().isoformat()
        _cache = (_monotonic(), cards, fetched_at)
        return cards, fetched_at
    finally:
        _lock.release()


def warm() -> None:
    """Fill the cache once at startup; never raises."""
    try:
        cards, _ = list_production_cards()
    except ModelCatalogNotConfigured:
        return
    except ModelCatalogUnavailable:
        return  # already logged by list_production_cards
    except Exception as exc:
        logger.warning("Model-card warm-up failed: %s", exc)
        return
    logger.info("Model-card warm-up listed %d production cards", len(cards))


def _reset_cache() -> None:
    """For tests."""
    global _cache
    _cache = None

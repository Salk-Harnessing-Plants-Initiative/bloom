"""The internal mail service stays internal: no Caddy route, no host port, a caller secret.

Bloom's mail service sends email for other containers. It must never be reachable from
outside the stack, so nothing in the Caddyfile names it and no compose file publishes it.
"""

from __future__ import annotations

import re

import yaml

from tests.unit._caddyfile_helpers import REPO_ROOT, strip_comments, text

COMPOSE_FILES = ("docker-compose.dev.yml", "docker-compose.prod.yml")


def _services(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text())["services"]


def test_no_caddy_route_reaches_the_mail_service():
    assert not re.search(r"\bmail:5200\b|reverse_proxy\s+mail\b", strip_comments(text()))


def test_the_mail_service_publishes_no_port():
    for name in COMPOSE_FILES:
        mail = _services(name)["mail"]
        assert "ports" not in mail, name
        assert mail["expose"] == ["5200"], name


def test_the_mail_service_requires_a_caller_secret():
    for name in COMPOSE_FILES:
        services = _services(name)
        assert "MAIL_SERVICE_SECRET" in services["mail"]["environment"], name
        bloommcp = services["bloommcp"]["environment"]
        assert bloommcp["MAIL_SERVICE_URL"] == "http://mail:5200", name
        assert "MAIL_SERVICE_SECRET" in bloommcp, name

#!/usr/bin/env python3
"""Verify that docker-compose.dev.yml pins the same image versions as docker-compose.prod.yml.

Dev and prod drifting apart means production-only behaviour is invisible to local
development and to the dev-stack-smoke CI job, and only surfaces in compose-health-check
— the single job that builds from the prod compose file. See issue #692.

Comparison rules:
  * services are matched by name, except for the database, whose service is named
    `db-dev` in the dev file and `db-prod` in the prod file (see DB_SERVICE_ALIASES)
  * only the image *version* is compared. A digest pin on one side and not the other is
    allowed, so prod can pin `image@sha256:...` for supply-chain reasons without forcing
    the same on dev
  * services present in only one file are ignored — see ALLOWED_ONLY_IN for the ones we
    expect, which are reported if they change

Usage:
    python3 scripts/verify_image_parity.py [dev_compose] [prod_compose]

Exit 0: versions align. Exit 1: one or more mismatches (each emits a stderr line and a
GitHub Actions `::error` annotation). Exit 2: usage or file-not-found error.
"""

import re
import sys
from pathlib import Path

DEV_DEFAULT = "docker-compose.dev.yml"
PROD_DEFAULT = "docker-compose.prod.yml"

# The database service is deliberately named differently in each file.
DB_SERVICE_ALIASES = {"db-dev": "db-prod"}

# Services we expect to exist in only one file. Anything else appearing on one side only
# is reported as informational, so a new service does not silently escape the check.
ALLOWED_ONLY_IN = {
    "dev": {"swagger-ui"},
    "prod": {"caddy"},
}

_SERVICE_RE = re.compile(r"^  ([a-zA-Z0-9_.-]+):\s*$")
_IMAGE_RE = re.compile(r"^\s+image:\s*(\S+)\s*$")


def parse_images(path: Path) -> dict[str, str]:
    """Map service name -> image reference, for services that pin an image."""
    images: dict[str, str] = {}
    service: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SERVICE_RE.match(line)
        if match:
            service = match.group(1)
            continue
        match = _IMAGE_RE.match(line)
        if match and service:
            images[service] = match.group(1)
    return images


def version_of(image: str) -> str:
    """Strip any digest pin, leaving `repo:tag` (or `repo` if untagged)."""
    return image.split("@", 1)[0]


def main(dev_path_str: str, prod_path_str: str) -> int:
    dev_path, prod_path = Path(dev_path_str), Path(prod_path_str)
    for path in (dev_path, prod_path):
        if not path.is_file():
            print(f"error: {path} not found", file=sys.stderr)
            return 2

    dev, prod = parse_images(dev_path), parse_images(prod_path)
    problems: list[str] = []

    for dev_service, dev_image in sorted(dev.items()):
        prod_service = DB_SERVICE_ALIASES.get(dev_service, dev_service)
        if prod_service not in prod:
            if dev_service not in ALLOWED_ONLY_IN["dev"]:
                problems.append(
                    f"{dev_service}: pinned in {dev_path.name} but has no counterpart "
                    f"in {prod_path.name} — add it to ALLOWED_ONLY_IN if intentional"
                )
            continue
        dev_version, prod_version = version_of(dev_image), version_of(prod[prod_service])
        if dev_version != prod_version:
            label = (
                dev_service
                if prod_service == dev_service
                else f"{dev_service} / {prod_service}"
            )
            problems.append(
                f"{label}: dev pins {dev_version} but prod pins {prod_version}"
            )

    aliased = set(DB_SERVICE_ALIASES.values())
    for prod_service in sorted(prod):
        if (
            prod_service not in dev
            and prod_service not in aliased
            and prod_service not in ALLOWED_ONLY_IN["prod"]
        ):
            problems.append(
                f"{prod_service}: pinned in {prod_path.name} but has no counterpart "
                f"in {dev_path.name} — add it to ALLOWED_ONLY_IN if intentional"
            )

    if problems:
        for problem in problems:
            print(f"image parity: {problem}", file=sys.stderr)
            print(f"::error title=Image parity::{problem}")
        print(
            f"\n{len(problems)} image-parity problem(s). Dev and prod must pin the same "
            "versions so production-only behaviour is reproducible locally (issue #692).",
            file=sys.stderr,
        )
        return 1

    print(f"Image parity OK: {len(dev)} dev service(s) checked against {prod_path.name}.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) > 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    sys.exit(main(args[0] if args else DEV_DEFAULT, args[1] if len(args) > 1 else PROD_DEFAULT))

"""Profile-based credential storage for bloomctl.

Mirrors the legacy ``packages/bloom-fs`` format: a dotenv file at
``~/.bloom/credentials.txt`` (profile ``prod``) or ``~/.bloom/credentials.<name>.txt``
(profile ``<name>``), with keys BLOOM_EMAIL / BLOOM_PASSWORD / BLOOM_API_URL /
BLOOM_ANON_KEY.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_PROFILE = "prod"
_REQUIRED_KEYS = ("BLOOM_API_URL", "BLOOM_ANON_KEY", "BLOOM_EMAIL", "BLOOM_PASSWORD")
# credentials.txt -> profile "prod"; credentials.<name>.txt -> profile "<name>"
_CRED_RE = re.compile(r"^credentials(?:\.(.+))?\.txt$")


@dataclass(frozen=True)
class Credentials:
    api_url: str
    anon_key: str
    email: str
    password: str


def default_config_dir() -> Path:
    """The Bloom config directory (``~/.bloom``)."""
    return Path.home() / ".bloom"


def filename_for_profile(profile: str) -> str:
    """Filename a profile is *written* to (`prod` -> credentials.txt)."""
    return "credentials.txt" if profile == DEFAULT_PROFILE else f"credentials.{profile}.txt"


def available_profiles(config_dir: Path) -> dict[str, Path]:
    """Map profile name -> credentials file found in ``config_dir``.

    Both ``credentials.txt`` and ``credentials.prod.txt`` resolve to ``prod`` —
    having both is ambiguous and raises.
    """
    profiles: dict[str, Path] = {}
    for path in sorted(config_dir.glob("credentials*.txt")):
        match = _CRED_RE.match(path.name)
        if not match:
            continue
        name = match.group(1) or DEFAULT_PROFILE
        if name in profiles:
            raise ValueError(
                f"Conflicting credential files for profile '{name}': "
                f"{profiles[name].name} and {path.name}. Keep only one."
            )
        profiles[name] = path
    return profiles


def resolve_profile_path(profile: str, config_dir: Path | None = None) -> Path:
    """Path to the credentials file for ``profile``, or raise with a login hint."""
    config_dir = config_dir or default_config_dir()
    profiles = available_profiles(config_dir)
    if profile not in profiles:
        hint = "bloomctl login" + ("" if profile == DEFAULT_PROFILE else f" --profile {profile}")
        raise FileNotFoundError(
            f"No credentials for profile '{profile}'. Run `{hint}` "
            f"(expected {config_dir / filename_for_profile(profile)})."
        )
    return profiles[profile]


def load_credentials(profile: str = DEFAULT_PROFILE, config_dir: Path | None = None) -> Credentials:
    """Load and validate the four credential keys for ``profile``."""
    path = resolve_profile_path(profile, config_dir)
    values = dotenv_values(path)
    missing = [k for k in _REQUIRED_KEYS if not values.get(k)]
    if missing:
        raise ValueError(f"{path} is missing required keys: {', '.join(missing)}")
    return Credentials(
        api_url=values["BLOOM_API_URL"],
        anon_key=values["BLOOM_ANON_KEY"],
        email=values["BLOOM_EMAIL"],
        password=values["BLOOM_PASSWORD"],
    )


def save_credentials(
    creds: Credentials, profile: str = DEFAULT_PROFILE, config_dir: Path | None = None
) -> Path:
    """Write ``creds`` to the profile's dotenv file (creating ``config_dir``)."""
    config_dir = config_dir or default_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / filename_for_profile(profile)
    path.write_text(
        f"BLOOM_EMAIL={creds.email}\n"
        f"BLOOM_PASSWORD={creds.password}\n"
        f"BLOOM_API_URL={creds.api_url}\n"
        f"BLOOM_ANON_KEY={creds.anon_key}\n"
    )
    return path

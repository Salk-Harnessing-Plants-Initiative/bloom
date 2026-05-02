"""Functions enabling correct Version-label allocation and on-disk directory-name construction."""
import re
from datetime import date
from typing import Optional

from .schema import Manifest

_SLUG_PATTERN = re.compile(r"[^a-z0-9_]+")
_MAX_SLUG_LEN = 32


def slugify(label: str) -> str:
    """Lowercase, alphanumerics + underscore only, max 32 chars, stripped of edge underscores."""
    s = label.strip().lower().replace("-", "_").replace(" ", "_")
    s = _SLUG_PATTERN.sub("", s)
    s = s.strip("_")
    return s[:_MAX_SLUG_LEN]


def next_version_id(manifest: Optional[Manifest]) -> str:
    """Return the next v<N> id, never reusing N even after deletion."""
    if manifest is None or not manifest.versions:
        return "v1"
    max_n = 0
    for entry in manifest.versions:
        vid = entry.id
        if not vid.startswith("v"):
            continue
        head = vid[1:].split("_", 1)[0]
        if head.isdigit():
            n = int(head)
            if n > max_n:
                max_n = n
    return f"v{max_n + 1}"


def version_dir_name(
    version_id: str,
    user_label: Optional[str] = None,
    today: Optional[date] = None,
) -> str:
    """Build v<N>_<YYYY-MM-DD>[_<slug>]."""
    d = (today or date.today()).isoformat()
    base = f"{version_id}_{d}"
    if user_label:
        slug = slugify(user_label)
        if slug:
            return f"{base}_{slug}"
    return base

"""Whether an object key stays inside its bucket.

`object_path` is written by the desktop apps and by any signed-in role, and both
renderers hand it to the storage client as the workflows app user. A key that
resolves outside its bucket therefore reads other paths on the internal gateway
with this service's privileges, and the bytes come back inside a video the
caller can download.
"""

from urllib.parse import unquote

# One decode is what escapes the bucket today: the client parses the key as a
# URL, which turns `%2e%2e` into `..`, and resolves it. Further passes are
# defence rather than a known hole — a key that keeps decoding is refused
# because nothing legitimate looks like that, not because storage would follow
# it.
MAX_KEY_DECODES = 3


def leaves_the_bucket(path: str) -> bool:
    """Whether a key resolves outside the bucket, decoded as storage sees it.

    The storage client parses the key as a URL before resolving it, which decodes
    `%2e%2e` into `..` — so a check that reads only literal segments passes the
    encoded form straight through, and the request leaves the bucket exactly as
    the literal form does. Every decoding pass is checked, so the raw and the
    decoded forms both have to be confined.
    """
    seen = path
    for _ in range(MAX_KEY_DECODES):
        if seen.startswith("/") or ".." in seen.split("/"):
            return True
        decoded = unquote(seen)
        if decoded == seen:
            return False
        seen = decoded
    return True

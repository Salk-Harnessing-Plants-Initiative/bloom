"""Whether an object key stays inside its bucket.

`object_path` is written by the desktop apps and by any signed-in role, and both
renderers hand it to the storage client as the workflows app user. A key that
resolves outside its bucket therefore reads other paths on the internal gateway
with this service's privileges, and the bytes come back inside a video the
caller can download.
"""

from urllib.parse import unquote

# Three passes is past anything legitimate: a real key carries at most one
# escape sequence, and a key still decoding after three is only ever an attempt
# to hide a `..` from this check.
MAX_KEY_DECODES = 3


def leaves_the_bucket(path: str) -> bool:
    """Whether a key resolves outside the bucket, decoded as storage sees it.

    The storage client percent-decodes before it resolves `..`, so a check that
    reads only literal segments passes `%2e%2e` straight through. Every decoding
    pass is checked, so the raw and the decoded forms both have to be confined.
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

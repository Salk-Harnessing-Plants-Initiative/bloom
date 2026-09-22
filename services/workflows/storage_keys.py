"""Whether an object key stays inside its bucket.

`object_path` is written by the desktop apps and by any signed-in role, and both
renderers hand it to the storage client as the workflows app user. A key that
resolves outside its bucket therefore reads other paths on the internal gateway
with this service's privileges, and the bytes come back inside a video the
caller can download.

Reading the text cannot answer this. The client parses the key as a URL, and
that both decodes `%2e%2e` into `..` and strips every tab, newline and carriage
return — so `.<tab>./x` resolves to `../x` while no literal `..` ever appears in
the string. A check written against the characters is wrong in both directions:
it misses those, and it refuses a leading slash the client simply strips.

So the key is resolved the way the client resolves it, and the result is what is
checked.
"""

from storage3._sync.file_api import relative_path_to_parts
from yarl import URL

# Where a key lands depends only on the shape of the path, so resolving against
# a stand-in base keeps this a pure function: the real host and bucket cannot
# change whether a key climbs out of its own prefix.
_BASE = URL("http://resolve.invalid/")
_BUCKET = "bucket"
_INSIDE = f"/object/{_BUCKET}/"


def leaves_the_bucket(path: str) -> bool:
    """Whether a key resolves outside its bucket, as the storage client sees it."""
    try:
        resolved = _BASE.joinpath("object", _BUCKET, *relative_path_to_parts(path))
    except Exception:
        # joinpath refuses a part that starts with "/", and anything else that
        # cannot be resolved is not a key this service should send either.
        return True
    return not resolved.path.startswith(_INSIDE)

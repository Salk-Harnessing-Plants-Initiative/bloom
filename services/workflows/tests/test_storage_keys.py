"""A key that leaves its bucket must be refused before anything downloads it.

`object_path` is written by the desktop apps and by any signed-in role, and the
renderers fetch it as the workflows app user — so an unconfined key reads other
paths on the internal gateway with this service's privileges, and the bytes come
back inside a video the caller can download.
"""

import pytest
from storage3._sync.file_api import relative_path_to_parts
from yarl import URL

from storage_keys import leaves_the_bucket

# What the service really builds, so the property below is checked against the
# deployed shape and not only against the stand-in inside storage_keys.
REAL_BASE = URL("http://kong:8000/storage/v1")
REAL_BUCKET = "graviscan-images"

STAYS_INSIDE = [
    "12/wave-1/P7_40.tif",
    "demo/plate-a2-cycle1.jpg",
    "cyl-videos/5.mp4",
    "gravi-images/Root Study 2026_wave1_cy3_A01.tif",
    "gravi-images/expérience_wave2_cy10_B02.tif",
    # Dots inside a segment are not a traversal.
    "gravi-images/a..b_cy1.tif",
    # A literal percent is not an escape sequence.
    "gravi-images/50%_growth_cy1.tif",
    # The client strips a leading slash, so this lands in the bucket. Refusing
    # it drops frames from scans that render fine today.
    "/cyl-images/frame_1.png",
    "//demo/x.jpg",
    # A scheme or host is discarded rather than followed.
    "http://evil/x",
    "//evil/x",
    # Backslashes stay inside one segment with this client.
    "..\\videos\\1.mp4",
    "..%5cvideos/1.mp4",
    # Encoded twice, the client decodes once: an odd filename, not a climb.
    "%252e%252e/videos/1.mp4",
    # Names nothing, but names nothing *inside* the bucket: the download then
    # fails the way a missing object does, which is the honest answer.
    "",
    "   ",
]

LEAVES = [
    "../videos/1.mp4",
    "a/../../object/videos/1.mp4",
    # Percent-encoded: the client decodes before it resolves.
    "%2e%2e/videos/1.mp4",
    "%2E%2E/videos/1.mp4",
    ".%2e/videos/1.mp4",
    "%2e./videos/1.mp4",
    "a/%2e%2e/%2e%2e/object/videos/1.mp4",
    "..%2fvideos/1.mp4",
    " ..%2fvideos/1.mp4",
    # Whitespace and control characters: the client strips these entirely, so
    # `..` reappears in the URL while no literal `..` is ever in the string.
    ".\t./videos/1.mp4",
    ".\n./videos/1.mp4",
    ".\r./videos/1.mp4",
    "\t../videos/1.mp4",
    "..\t/videos/1.mp4",
    " ../videos/1.mp4",
    "\x00../videos/1.mp4",
    "\x0b../videos/1.mp4",
    "\t%2e%2e/videos/1.mp4",
    # Climbing further reaches other services behind the same gateway.
    ".\t./.\t./rest/v1/gravi_scans",
    ".\n./.\n./.\n./.\n./auth/v1/admin/users",
]


def _client_leaves_the_bucket(key: str) -> bool:
    """Where the storage client would actually send this key."""
    try:
        url = REAL_BASE.joinpath("object", REAL_BUCKET, *relative_path_to_parts(key))
    except Exception:
        return True
    return not url.path.startswith(f"/storage/v1/object/{REAL_BUCKET}/")


@pytest.mark.parametrize("path", STAYS_INSIDE)
def test_a_key_that_stays_in_its_bucket_is_allowed(path):
    assert leaves_the_bucket(path) is False


@pytest.mark.parametrize("path", LEAVES)
def test_a_key_that_climbs_out_is_refused(path):
    assert leaves_the_bucket(path) is True


@pytest.mark.parametrize("path", STAYS_INSIDE + LEAVES)
def test_the_answer_matches_what_the_client_would_do(path):
    """The property the whole check rests on. Reading the characters cannot
    give this answer, and a storage3 or yarl change that alters how a key
    resolves has to fail here rather than quietly widen or narrow the guard."""
    assert leaves_the_bucket(path) is _client_leaves_the_bucket(path)


def test_a_key_the_client_cannot_resolve_is_refused():
    """`joinpath` refuses a part that starts with a slash, so this key can never
    be fetched; refusing it says so instead of raising from inside the client."""
    assert leaves_the_bucket("demo/%2Fweird.tif") is True

"""A key that leaves its bucket must be refused before anything downloads it.

`object_path` is written by the desktop apps and by any signed-in role, and the
renderers fetch it as the workflows app user — so an unconfined key reads other
paths on the internal gateway with this service's privileges, and the bytes come
back inside a video the caller can download.
"""

import pytest

from storage_keys import MAX_KEY_DECODES, leaves_the_bucket


@pytest.mark.parametrize(
    "path",
    [
        "12/wave-1/P7_40.tif",
        "gravi-images/Root Study 2026_wave1_cy3_A01.tif",
        "gravi-images/expérience_wave2_cy10_B02.tif",
        # Dots inside a segment are not a traversal.
        "gravi-images/a..b_cy1.tif",
        # A literal percent is not an escape sequence.
        "gravi-images/50%_growth_cy1.tif",
        "cyl-videos/5.mp4",
    ],
)
def test_a_real_key_stays_in_its_bucket(path):
    assert leaves_the_bucket(path) is False


@pytest.mark.parametrize(
    "path",
    [
        "../videos/1.mp4",
        "a/../../object/videos/1.mp4",
        "/storage/v1/object/videos/1.mp4",
    ],
)
def test_a_literal_traversal_is_refused(path):
    assert leaves_the_bucket(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "%2e%2e/videos/1.mp4",
        "%2E%2E/videos/1.mp4",
        ".%2e/videos/1.mp4",
        "%2e./videos/1.mp4",
        "a/%2e%2e/%2e%2e/object/videos/1.mp4",
        "%2Fstorage/v1/object/videos/1.mp4",
    ],
)
def test_an_encoded_traversal_is_refused(path):
    """The storage client decodes before it resolves `..`, so a check that reads
    only literal segments lets every one of these through."""
    assert leaves_the_bucket(path) is True


def test_an_escape_hidden_under_another_escape_is_refused():
    assert leaves_the_bucket("%252e%252e/videos/1.mp4") is True


def test_a_key_that_never_stops_decoding_is_refused():
    """Nothing legitimate nests escapes; a key still decoding after the limit is
    only ever hiding a traversal."""
    nested = "%" + "25" * (MAX_KEY_DECODES + 2) + "2e"

    assert leaves_the_bucket(nested + nested + "/videos/1.mp4") is True

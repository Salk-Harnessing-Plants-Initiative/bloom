"""StorageFileReader: Storage errors become FileNotFound or FileReadError, never raw."""

from __future__ import annotations

import pytest
from storage3.exceptions import StorageApiError

from bloom_mcp.data_access import file_reader as fr
from bloom_mcp.data_access.file_ports import FileNotFound, FileReadError


class _Bucket:
    def __init__(self, error=None, info=None):
        self.error, self._info, self.seen = error, info, []

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def info(self, path):
        self._maybe_fail()
        return self._info

    def download(self, path, options=None):
        self.seen.append(options)
        self._maybe_fail()
        return b"bytes"

    def create_signed_url(self, path, seconds):
        self._maybe_fail()
        return {"signedURL": "http://kong:8000/storage/v1/object/sign/images/a.png?t=1"}


@pytest.fixture
def bucket(monkeypatch):
    holder = {"bucket": _Bucket()}

    def client(name, **kwargs):
        holder["accept"] = kwargs.get("accept")
        return holder["bucket"]

    monkeypatch.setattr(fr, "get_bucket_client", client)
    return holder


@pytest.mark.parametrize(
    "error",
    [
        StorageApiError("Object not found", "not_found", 404),
        StorageApiError("Object not found", "Not Found", "400"),
        StorageApiError("The resource was not found", "NoSuchKey", 400),
    ],
)
def test_a_missing_object_is_file_not_found(bucket, error):
    bucket["bucket"] = _Bucket(error)
    with pytest.raises(FileNotFound):
        fr.StorageFileReader().size("images", "a.png")


@pytest.mark.parametrize(
    "error", [StorageApiError("denied", "Unauthorized", 403), TimeoutError("slow")]
)
def test_other_failures_are_file_read_errors(bucket, error):
    bucket["bucket"] = _Bucket(error)
    with pytest.raises(FileReadError) as err:
        fr.StorageFileReader().download("images", "a.png")
    assert not isinstance(err.value, FileNotFound)


def test_size_reads_the_object_info(bucket):
    bucket["bucket"] = _Bucket(info={"metadata": {"size": 42}})
    assert fr.StorageFileReader().size("images", "a.png") == 42


def test_a_missing_size_is_a_read_error(bucket):
    bucket["bucket"] = _Bucket(info={"metadata": {}})
    with pytest.raises(FileReadError):
        fr.StorageFileReader().size("images", "a.png")


def test_images_are_asked_for_as_webp(bucket):
    """Plate scans are TIFF: the image service converts only when WebP is accepted."""
    fr.StorageFileReader().download_image("graviscan-images", "a.tif", 1000)
    assert "image/webp" in bucket["accept"]
    fr.StorageFileReader().download("scrna", "a.json")
    assert bucket["accept"] is None


def test_images_are_shrunk_by_the_image_service(bucket):
    fr.StorageFileReader().download_image("images", "a.png", 1000)
    assert bucket["bucket"].seen == [
        {"transform": {"width": 1000, "resize": "contain"}}
    ]


def test_links_use_the_public_address(bucket, monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://kong:8000")
    monkeypatch.setenv("BLOOM_PUBLIC_SUPABASE_URL", "https://bloom.example")
    url = fr.StorageFileReader().signed_url("images", "a.png", 3600)
    assert url.startswith("https://bloom.example/storage/v1/object/sign/images/a.png")

"""Upload surface: receive_upload / signed_input_upload core + supabase_client
write helpers, against a monkeypatched storage boundary (no live Supabase)."""

from __future__ import annotations

import dataclasses
import io

import pandas as pd
import pytest

from bloom_mcp import input_formats, supabase_client, uploads


def _csv_bytes(rows: int = 3) -> bytes:
    df = pd.DataFrame({"genotype": ["g"] * rows, "trait_x": list(range(rows))})
    return df.to_csv(index=False).encode()


class _FakeStorage:
    """Records uploads and returns a canned signed-upload response."""

    def __init__(self):
        self.uploaded: list[tuple[str, bytes]] = []
        self.signed: list[str] = []

    def upload(self, *, path, file, file_options):
        self.uploaded.append((path, file))

    def create_signed_upload_url(self, path):
        self.signed.append(path)
        return {"signed_url": f"https://storage/signed/{path}", "token": "tok", "path": path}


@pytest.fixture
def storage(monkeypatch):
    fake = _FakeStorage()
    monkeypatch.setattr(supabase_client, "get_storage_client", lambda: fake)
    return fake


# ─── receive_upload (small-file path) ─────────────────────────────────────────


def test_receive_upload_stores_and_returns_reference(storage):
    result = uploads.receive_upload("accessions.csv", _csv_bytes(3))
    assert result["input_ref"] == "accessions.csv"
    assert result["format"] == "csv"
    assert result["columns"] == ["genotype", "trait_x"]
    # landed under the input prefix, flat
    assert storage.uploaded == [("bloommcp_input/accessions.csv", _csv_bytes(3))]


def test_receive_upload_strips_directory_to_basename(storage):
    uploads.receive_upload("sub/dir/exp.csv", _csv_bytes(2))
    assert storage.uploaded[0][0] == "bloommcp_input/exp.csv"


def test_receive_upload_rejects_unregistered_format(storage):
    with pytest.raises(input_formats.UnsupportedFormatError):
        uploads.receive_upload("model.pkl", b"anything")
    assert storage.uploaded == []


def test_receive_upload_rejects_oversize(monkeypatch, storage):
    tiny = dataclasses.replace(input_formats.get_format("csv"), max_size=5)
    monkeypatch.setitem(input_formats._BY_EXT, ".csv", tiny)
    with pytest.raises(input_formats.FileTooLargeError):
        uploads.receive_upload("big.csv", _csv_bytes(50))
    assert storage.uploaded == []


def test_receive_upload_rejects_invalid_bytes(storage):
    with pytest.raises(input_formats.InvalidFormatError):
        uploads.receive_upload("corrupt.parquet", b"not a parquet file")
    assert storage.uploaded == []


# ─── signed_input_upload (large-file path) ────────────────────────────────────


def test_signed_input_upload_mints_scoped_url(storage):
    result = uploads.signed_input_upload("counts.parquet")
    assert result["input_ref"] == "counts.parquet"
    assert result["format"] == "parquet"
    assert result["upload"]["path"] == "bloommcp_input/counts.parquet"
    assert storage.signed == ["bloommcp_input/counts.parquet"]


def test_signed_input_upload_rejects_unregistered(storage):
    with pytest.raises(input_formats.UnsupportedFormatError):
        uploads.signed_input_upload("weights.pkl")
    assert storage.signed == []


# ─── Content-Length guard (reject before buffering) ──────────────────────────


def test_buffered_limit_exceeded_flags_oversize():
    over = input_formats.MAX_BUFFERED_UPLOAD_SIZE + 1
    assert uploads.buffered_limit_exceeded(str(over)) == over


def test_buffered_limit_allows_within_cap():
    assert uploads.buffered_limit_exceeded(str(input_formats.MAX_BUFFERED_UPLOAD_SIZE)) is None
    assert uploads.buffered_limit_exceeded("1024") is None


def test_buffered_limit_ignores_absent_or_unparseable_header():
    assert uploads.buffered_limit_exceeded(None) is None
    assert uploads.buffered_limit_exceeded("not-a-number") is None


# ─── supabase_client write helpers ────────────────────────────────────────────


def test_write_input_uses_input_prefix(storage):
    ref = supabase_client.write_input("exp.csv", b"a,b\n1,2\n")
    assert ref == "exp.csv"
    assert storage.uploaded[0][0] == "bloommcp_input/exp.csv"


def test_write_input_rejects_slash():
    with pytest.raises(ValueError):
        supabase_client.write_input("a/b.csv", b"x")


def test_create_signed_upload_url_scopes_to_key(storage):
    out = supabase_client.create_signed_upload_url("counts.parquet")
    assert out["path"] == "bloommcp_input/counts.parquet"
    assert storage.signed == ["bloommcp_input/counts.parquet"]


def test_create_signed_upload_url_rejects_slash():
    with pytest.raises(ValueError):
        supabase_client.create_signed_upload_url("a/b.parquet")

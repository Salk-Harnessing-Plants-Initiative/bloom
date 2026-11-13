"""File-read port for the read_file tool; adapters read Storage as bloom_agent."""

from __future__ import annotations

from typing import Protocol


class FileReadError(Exception):
    """A file read failed; the message never carries hosts, keys or internals."""


class FileNotFound(FileReadError):
    """No file at this path in the bucket."""


class FileReader(Protocol):
    def size(self, bucket: str, path: str) -> int:
        """The file's size in bytes; raises FileNotFound."""
        ...

    def download(self, bucket: str, path: str) -> bytes: ...

    def signed_url(self, bucket: str, path: str, seconds: int) -> str:
        """A download link on Bloom's public address, valid for `seconds`."""
        ...

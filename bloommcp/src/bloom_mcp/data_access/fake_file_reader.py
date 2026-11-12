"""In-memory FileReader for tests."""

from __future__ import annotations

from .file_ports import FileNotFound, FileReadError


class FakeFileReader:
    """Files keyed by (bucket, path); `resized` stands in for Storage's image service."""

    def __init__(
        self,
        files: dict[tuple[str, str], bytes] | None = None,
        resized: dict[tuple[str, str], bytes] | None = None,
        broken: set[tuple[str, str]] | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.resized = dict(resized or {})
        self.broken = set(broken or ())
        self.calls: list[tuple[str, str, str]] = []

    def _get(self, bucket: str, path: str) -> bytes:
        if (bucket, path) in self.broken:
            raise FileReadError(path)
        if (bucket, path) not in self.files:
            raise FileNotFound(path)
        return self.files[(bucket, path)]

    def size(self, bucket: str, path: str) -> int:
        self.calls.append(("size", bucket, path))
        return len(self._get(bucket, path))

    def download(self, bucket: str, path: str) -> bytes:
        self.calls.append(("download", bucket, path))
        return self._get(bucket, path)

    def download_image(self, bucket: str, path: str, width: int) -> bytes:
        self.calls.append(("download_image", bucket, path))
        original = self._get(bucket, path)
        return self.resized.get((bucket, path), original)

    def signed_url(self, bucket: str, path: str, seconds: int) -> str:
        self.calls.append(("signed_url", bucket, path))
        self._get(bucket, path)
        return (
            f"https://bloom.example/storage/v1/object/sign/{bucket}/{path}?t={seconds}"
        )

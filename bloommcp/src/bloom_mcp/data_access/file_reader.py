"""Reads Bloom's stored files through Supabase Storage as bloom_agent."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from storage3.exceptions import StorageApiError

from bloom_mcp.storage_backend import (
    _extract_object_size,
    _extract_signed_url,
    _to_public_url,
)
from bloom_mcp.supabase_client import get_bucket_client

from .file_ports import FileNotFound, FileReadError

logger = logging.getLogger(__name__)

STORAGE_TIMEOUT_SECONDS = 30
_NOT_FOUND = frozenset({"404", "not_found", "notfound", "nosuchkey"})

_T = TypeVar("_T")


def _not_found(exc: StorageApiError) -> bool:
    status = str(getattr(exc, "status", "")).lower()
    code = str(getattr(exc, "code", "")).lower().replace(" ", "")
    message = str(getattr(exc, "message", "")).lower()
    return status in _NOT_FOUND or code in _NOT_FOUND or "not found" in message


def _call(bucket: str, path: str, action: Callable[[Any], _T]) -> _T:
    try:
        return action(
            get_bucket_client(bucket, timeout_seconds=STORAGE_TIMEOUT_SECONDS)
        )
    except StorageApiError as exc:
        if _not_found(exc):
            raise FileNotFound(path) from None
        logger.warning("file read failed in %s: %s", bucket, type(exc).__name__)
        raise FileReadError(path) from None
    except Exception as exc:  # noqa: BLE001 — network or client failures, logged by type
        logger.warning("file read failed in %s: %s", bucket, type(exc).__name__)
        raise FileReadError(path) from None


class StorageFileReader:
    def size(self, bucket: str, path: str) -> int:
        size = _extract_object_size(_call(bucket, path, lambda b: b.info(path)))
        if size is None:
            raise FileReadError(path)
        return size

    def download(self, bucket: str, path: str) -> bytes:
        return _call(bucket, path, lambda b: b.download(path))

    def download_image(self, bucket: str, path: str, width: int) -> bytes:
        options = {"transform": {"width": width, "resize": "contain"}}
        return _call(bucket, path, lambda b: b.download(path, options))

    def signed_url(self, bucket: str, path: str, seconds: int) -> str:
        url = _extract_signed_url(
            _call(bucket, path, lambda b: b.create_signed_url(path, seconds))
        )
        if not url:
            raise FileReadError(path)
        return _to_public_url(url)

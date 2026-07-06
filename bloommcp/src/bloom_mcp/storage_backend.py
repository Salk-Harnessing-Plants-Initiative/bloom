"""Object-storage backend selection for bloommcp.

The five object-storage helpers in :mod:`bloom_mcp.supabase_client`
(``upload_file`` / ``download_file`` / ``write_json`` / ``read_json`` /
``list_prefix``) delegate to the *active* backend selected here. Two backends
exist:

* :class:`SupabaseStorageBackend` — the deployed default (Supabase Storage in the
  ``bloommcp-data`` bucket). Its method bodies are the pre-backend
  ``supabase_client`` helpers verbatim, so the default path is byte-for-byte
  unchanged.
* :class:`LocalStorageBackend` — opt-in; writes/reads real files under a root
  dir, mapping each ``/``-separated storage key to ``<root>/<key>``. It preserves
  the object store's implicit guarantees on a POSIX filesystem: atomic writes
  (temp file on the root's filesystem + ``os.replace``), upsert/overwrite,
  verbatim bytes (so recorded ``output_sha256`` equals the file on disk), and
  redacted (no host-path) not-found and permission/OS errors.

Selection is driven by ``BLOOM_STORAGE_BACKEND`` (default ``supabase``; ``local``
opts in). Resolution is **lazy** — this module reads no environment variable and
touches no filesystem at import, so ``import bloom_mcp`` stays side-effect-free.
:func:`validate_storage_backend` is called at server boot (via
``experiment_utils.validate_env``) so a misconfigured value or an unusable local
root fails fast at boot rather than mid-run.

Out of scope: PostgREST/table access (``get_postgrest_client``) and
``read_input_csv``, which rides that client — neither is one of the five swapped
helpers, so both are unaffected by the selected backend.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

VALID_BACKENDS = ("supabase", "local")
_DEFAULT_BACKEND = "supabase"

# Prefix for the in-flight atomic-write temp file. Shared by the writer (which
# creates `<dir>/.tmp-*`) and list_prefix (which filters it out for cross-backend
# parity), so the two never drift.
_TMP_PREFIX = ".tmp-"


@runtime_checkable
class StorageBackend(Protocol):
    """The five object-storage operations bloommcp's write/read paths use."""

    def upload_file(self, key: str, local_path: Path) -> None: ...

    def download_file(self, key: str, local_path: Path) -> None: ...

    def write_json(self, key: str, payload: dict) -> None: ...

    def read_json(self, key: str) -> dict: ...

    def list_prefix(self, prefix: str) -> list[str]: ...


def _json_bytes(payload: dict) -> bytes:
    """Canonical JSON serialization shared by both backends.

    ``sort_keys`` + ``indent=2`` make the manifest bytes deterministic and
    backend-invariant, so the serialized ``manifest.json`` is byte-identical
    across the Supabase and local backends for the same payload.
    """
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


class StorageKeyNotFound(FileNotFoundError):
    """A key has no backing object. The message is redacted — no host path.

    Subclasses ``FileNotFoundError`` so the broad ``except Exception`` gates in
    the read path keep working, while carrying only the logical key (never an
    absolute filesystem path) into any agent-facing message.
    """


class StorageBackendError(OSError):
    """A local-backend filesystem failure (permission/OS error), redacted.

    Subclasses ``OSError`` so the read path's broad ``except`` gates keep working,
    while the agent-facing message names only the logical storage key — never an
    absolute host path (which would reveal the server's local root layout). The
    raw error (errno + path) is logged server-side only. See
    :func:`_redacted_io_error`.
    """


def _redacted_io_error(key: str, exc: OSError) -> StorageBackendError:
    """Log the raw OSError server-side and return a path-free error for the caller.

    The spec requires local-backend permission/OS errors to surface to agents
    without leaking an absolute host path, with the detail available only in
    server logs — mirroring the redaction :class:`StorageKeyNotFound` already
    gives the not-found case.
    """
    logger.warning(
        "local storage I/O error: key=%s errno=%s",
        key,
        getattr(exc, "errno", None),
        exc_info=True,
    )
    kind = "permission denied" if isinstance(exc, PermissionError) else "I/O error"
    return StorageBackendError(f"storage {kind} for key: {key}")


# ─── Supabase backend (deployed default) ──────────────────────────────────────


class SupabaseStorageBackend:
    """Supabase Storage in the ``bloommcp-data`` bucket — the deployed default.

    Method bodies are the pre-backend ``supabase_client`` helpers verbatim
    (they re-use ``get_storage_client`` / ``_guess_content_type`` from that
    module), so selecting ``supabase`` is byte-for-byte the prior behavior.
    Stateless — each call builds a fresh client via ``get_storage_client``.
    """

    def upload_file(self, key: str, local_path: Path) -> None:
        from bloom_mcp.supabase_client import _guess_content_type, get_storage_client

        client = get_storage_client()
        body = Path(local_path).read_bytes()
        client.upload(
            path=key,
            file=body,
            file_options={
                "content-type": _guess_content_type(Path(local_path)),
                "upsert": "true",
            },
        )

    def download_file(self, key: str, local_path: Path) -> None:
        from bloom_mcp.supabase_client import get_storage_client

        client = get_storage_client()
        payload = client.download(key)
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(payload)

    def write_json(self, key: str, payload: dict) -> None:
        from bloom_mcp.supabase_client import get_storage_client

        client = get_storage_client()
        client.upload(
            path=key,
            file=_json_bytes(payload),
            file_options={"content-type": "application/json", "upsert": "true"},
        )

    def read_json(self, key: str) -> dict:
        from bloom_mcp.supabase_client import get_storage_client

        client = get_storage_client()
        payload = client.download(key)
        return json.loads(payload.decode("utf-8"))

    def list_prefix(self, prefix: str) -> list[str]:
        from bloom_mcp.supabase_client import get_storage_client

        client = get_storage_client()
        items = client.list(prefix)
        return [item["name"] for item in items]


# ─── Local filesystem backend (opt-in) ────────────────────────────────────────


class LocalStorageBackend:
    """Writes/reads the bloommcp object store as real files under ``root``.

    Storage keys are ``/``-separated logical paths; each maps to ``<root>/<key>``.
    On **POSIX filesystems** writes are atomic (temp file on the root's
    filesystem, ``fsync``, then ``os.replace``) and overwrite in place; bytes are
    copied verbatim (binary, no newline/encoding translation) so a recorded
    ``output_sha256`` equals the file on disk. A resolved-path guard rejects any
    key that would escape the root. Windows/NTFS does **not** guarantee atomic
    replace-over-existing (and may raise if a reader holds the target open) — this
    is an opt-in dev backend; production stays on Supabase Storage.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()

    # key → path, with a resolved-path containment guard
    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key:
            raise ValueError(f"invalid storage key {key!r}")
        segments = key.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            raise ValueError(f"invalid storage key {key!r}")
        target = (self._root / Path(*segments)).resolve()
        if target != self._root and self._root not in target.parents:
            raise ValueError(f"storage key {key!r} escapes the local root")
        return target

    def _atomic_write(self, target: Path, data: bytes, *, key: str) -> None:
        """Write ``data`` to ``target`` atomically on POSIX.

        Writes a temp file in the target's directory (same filesystem), fsyncs
        it, then ``os.replace``s it into place, so a crash / kill / ENOSPC
        mid-write leaves either the whole prior file or the whole new file —
        never a truncated ``manifest.json``. The parent dir is fsynced
        best-effort for power-loss durability of the rename. NOTE: ``os.replace``
        is atomic over an existing file on POSIX only; on Windows/NTFS it is not
        guaranteed atomic (see the class docstring).

        ``OSError``\\ s (e.g. a permission-denied root, ENOSPC) are redacted to
        carry only ``key`` — never the absolute temp/target path.
        """
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(target.parent), prefix=_TMP_PREFIX, suffix=target.suffix
            )
        except OSError as exc:
            raise _redacted_io_error(key, exc) from None
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except OSError as exc:
            Path(tmp).unlink(missing_ok=True)
            raise _redacted_io_error(key, exc) from None
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        # Best-effort durability of the rename itself (POSIX dir fsync; a no-op
        # / OSError on platforms that can't open a directory fd, e.g. Windows).
        try:
            dir_fd = os.open(str(target.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass

    def upload_file(self, key: str, local_path: Path) -> None:
        self._atomic_write(self._resolve(key), Path(local_path).read_bytes(), key=key)

    def write_json(self, key: str, payload: dict) -> None:
        self._atomic_write(self._resolve(key), _json_bytes(payload), key=key)

    def download_file(self, key: str, local_path: Path) -> None:
        src = self._resolve(key)
        if not src.is_file():
            logger.debug("local storage miss: key=%s", key)
            raise StorageKeyNotFound(f"storage object not found: {key}")
        # Read the canonical file, redacting any permission/OS error to the key.
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise _redacted_io_error(key, exc) from None
        # Copy bytes to the caller-owned destination — never symlink or hand back
        # the canonical file under the root (the caller manages dest's lifetime).
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def read_json(self, key: str) -> dict:
        src = self._resolve(key)
        if not src.is_file():
            logger.debug("local storage miss: key=%s", key)
            raise StorageKeyNotFound(f"storage object not found: {key}")
        try:
            raw = src.read_bytes()
        except OSError as exc:
            raise _redacted_io_error(key, exc) from None
        return json.loads(raw.decode("utf-8"))

    def list_prefix(self, prefix: str) -> list[str]:
        rel = prefix.strip("/")
        directory = self._root if rel == "" else self._resolve(rel)
        try:
            names = [p.name for p in Path(directory).iterdir()]
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as exc:
            # e.g. a permission-denied directory — redact the absolute path.
            raise _redacted_io_error(prefix, exc) from None
        # Hide in-flight atomic-write temp files: a SIGKILL between mkstemp and
        # os.replace can orphan a `.tmp-*`, which never appears in the Supabase
        # backend — filtering keeps cross-backend list parity.
        return sorted(n for n in names if not n.startswith(_TMP_PREFIX))


# ─── Selection ────────────────────────────────────────────────────────────────

_active: Optional[StorageBackend] = None


def _selected_backend_name() -> str:
    """The lower-cased ``BLOOM_STORAGE_BACKEND`` value, defaulting to ``supabase``."""
    return (
        os.environ.get("BLOOM_STORAGE_BACKEND") or _DEFAULT_BACKEND
    ).strip().lower() or _DEFAULT_BACKEND


def selected_backend_name() -> str:
    """Public accessor for the selected backend name.

    The composition root (``server.main``) and the reader-selection in
    ``tools._ports`` need to know which backend is active without reaching into
    the private ``_selected_backend_name``. Resolved lazily (reads env on call),
    so it preserves the side-effect-free-import contract.
    """
    return _selected_backend_name()


def is_local_backend() -> bool:
    """Whether fully-local mode is selected (``BLOOM_STORAGE_BACKEND=local``).

    A single switch means "local input AND output": the input-side ``LocalReader``
    is wired only when this is true, so the reader and object-storage backends
    stay coupled (no local-raw / Supabase-cleaned split lineage).
    """
    return _selected_backend_name() == "local"


def _resolve_local_root() -> Path:
    """The local root for the ``local`` backend.

    ``BLOOM_STORAGE_LOCAL_ROOT`` when set. Otherwise falls back to
    ``BLOOM_OUTPUT_DIR`` — a **bridge-only, deprecated** default that reuses the
    already-mounted dev dir so ``BLOOM_STORAGE_BACKEND=local`` needs no second var
    in dev. Prefer setting ``BLOOM_STORAGE_LOCAL_ROOT`` explicitly; the fallback is
    logged (not silent) so a fourth overlapping use of ``BLOOM_OUTPUT_DIR`` stays
    observable.
    """
    explicit = os.environ.get("BLOOM_STORAGE_LOCAL_ROOT")
    if explicit:
        return Path(explicit)
    fallback = os.environ.get("BLOOM_OUTPUT_DIR", "")
    if fallback:
        logger.warning(
            "BLOOM_STORAGE_BACKEND=local is using BLOOM_OUTPUT_DIR as the local "
            "storage root because BLOOM_STORAGE_LOCAL_ROOT is unset; this fallback "
            "is a deprecated dev bridge — set BLOOM_STORAGE_LOCAL_ROOT explicitly."
        )
    return Path(fallback)


def _unrecognized_backend_error(name: str) -> RuntimeError:
    """The single 'unrecognized BLOOM_STORAGE_BACKEND' error.

    Shared by lazy selection (:func:`_build_backend`) and boot validation
    (:func:`validate_storage_backend`) so the message — the offending value + the
    accepted set — never drifts between the two raise sites.
    """
    return RuntimeError(
        f"BLOOM_STORAGE_BACKEND={name!r} is not recognized; "
        f"valid values: {', '.join(VALID_BACKENDS)}."
    )


def _build_backend() -> StorageBackend:
    name = _selected_backend_name()
    if name == "supabase":
        return SupabaseStorageBackend()
    if name == "local":
        return LocalStorageBackend(_resolve_local_root())
    raise _unrecognized_backend_error(name)


def active_backend() -> StorageBackend:
    """The process's active object-storage backend (memoized, resolved on first use).

    Intentionally lock-free. Under a concurrent first call two threads could each
    run :func:`_build_backend` and race on the assignment below; that is safe —
    both backends are cheap and stateless/idempotent to construct
    (``SupabaseStorageBackend`` holds nothing; ``LocalStorageBackend`` just stores a
    resolved root), so the two instances are interchangeable and last-write-wins
    leaves an equivalent object. A lock would only add contention on the hot
    storage path to prevent a harmless, transient double-build.
    """
    global _active
    if _active is None:
        _active = _build_backend()
    return _active


def reset_backend_for_tests() -> None:
    """Clear the memoized backend so tests can re-select from a changed env."""
    global _active
    _active = None


def validate_storage_backend() -> None:
    """Fail fast at boot on an invalid backend value or an unusable local root.

    Called from ``experiment_utils.validate_env`` (which ``server.main()`` runs
    before binding the port). Raising here names the offending value / root, so a
    misconfigured deploy fails at boot rather than on the first storage call.
    """
    name = _selected_backend_name()
    if name not in VALID_BACKENDS:
        raise _unrecognized_backend_error(name)
    if name == "local":
        root = _resolve_local_root()
        if str(root) in ("", "."):
            raise RuntimeError(
                "BLOOM_STORAGE_BACKEND=local but neither BLOOM_STORAGE_LOCAL_ROOT "
                "nor BLOOM_OUTPUT_DIR is set."
            )
        if not root.exists() or not root.is_dir():
            raise RuntimeError(
                f"BLOOM_STORAGE_BACKEND=local root {root} does not exist or is not "
                f"a directory."
            )
        if not os.access(root, os.W_OK):
            raise RuntimeError(
                f"BLOOM_STORAGE_BACKEND=local root {root} is not writable."
            )

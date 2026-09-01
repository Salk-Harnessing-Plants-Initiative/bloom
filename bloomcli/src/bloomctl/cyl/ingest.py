"""`bloomctl cyl ingest-result`: read + validate an envelope, call the RPC.

Pure helpers (read/parse, validate, summarize, error-mapping) are separated from
the single Supabase I/O call so the contract is unit-testable without a live
server — mirroring ``download.py``. The command sends the *original* parsed JSON
to ``insert_cyl_result_envelope`` (never a model re-serialization) so the
producer's ``provenance.idempotency_key`` — the RPC's first-writer-wins identity —
is preserved exactly. The RPC's return object and RAISE EXCEPTION messages are
owned by the ``cyl-trait-writeback`` capability; this module only consumes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

import click
from pydantic import ValidationError
from sleap_roots_contracts import RUN_MANIFEST_FILENAME, RunManifest

from ..credentials import DEFAULT_PROFILE
from ._batch import BatchResult, ScanResult, format_json, format_summary

logger = logging.getLogger(__name__)


class EnvelopeError(Exception):
    """Envelope could not be read or parsed (bad path, non-JSON, empty input)."""


class EnvelopeValidationError(EnvelopeError):
    """Envelope did not conform to the sleap-roots-contracts ResultEnvelope."""


class BlobConstructionError(Exception):
    """A --predictions-dir blob could not be constructed, verified, or uploaded."""


@dataclass
class PendingBlob:
    """One BlobRef-in-progress: its RPC-bound fields (kind/root_type/scan_key/
    checksum/file_size/s3_location/box_link) plus the local .slp path needed to
    verify and upload it. ``s3_location``/``box_link`` start ``None`` and are
    filled in by the upload step."""

    blob: dict[str, Any]
    local_path: Path


def load_envelope(source: str, *, stdin: TextIO | None = None) -> dict[str, Any]:
    """Read and parse a ResultEnvelope from a path, or from stdin when ``source`` is ``-``."""
    if source == "-":
        stream = stdin if stdin is not None else sys.stdin
        text = stream.read()
        where = "stdin"
    else:
        where = repr(source)
        try:
            text = Path(source).read_text(encoding="utf-8")
        except OSError as exc:
            raise EnvelopeError(f"could not read envelope from {where}: {exc}") from exc

    if not text.strip():
        raise EnvelopeError(f"empty envelope input ({where})")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnvelopeError(f"envelope from {where} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise EnvelopeError(f"envelope from {where} must be a JSON object")
    return data


@dataclass
class DiscoveredEnvelopes:
    """Result of scoping envelope discovery to an optional ``run_manifest.json`` (bloom #678).

    ``paths``: in-scope ``*.result.json`` files to ingest, sorted. ``missing_scan_keys``:
    manifest-declared scan_keys with no matching file, sorted.
    """

    paths: list[Path]
    missing_scan_keys: list[str]


def discover_envelopes(envelopes_dir: str | Path) -> DiscoveredEnvelopes:
    """Non-recursive glob for ``*.result.json`` directly under ``envelopes_dir``, sorted,
    scoped to a ``run_manifest.json`` when one is present.

    Matches the flat layout ``trait_extractor.extractor.extract_batch``'s ``output_dir``
    produces (one ``{scan_key}.result.json`` per scan, no nesting). If
    ``envelopes_dir / RUN_MANIFEST_FILENAME`` exists, only files whose filename stem is in
    the manifest's ``scan_keys`` are returned, and any declared scan_key with no matching
    file is reported via ``DiscoveredEnvelopes.missing_scan_keys``. With no manifest,
    discovery is fully unscoped (identical to the pre-manifest behavior). Raises
    ``EnvelopeError`` if ``envelopes_dir`` doesn't exist or isn't a directory, or if a
    present manifest is unreadable or fails to parse; an empty-but-present directory with no
    manifest returns ``DiscoveredEnvelopes([], [])`` (the empty-batch no-op case, not an
    error).
    """
    path = Path(envelopes_dir)
    if not path.is_dir():
        raise EnvelopeError(f"envelopes directory does not exist or is not a directory: {path}")

    all_paths = sorted(path.glob("*.result.json"))

    manifest_path = path / RUN_MANIFEST_FILENAME
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return DiscoveredEnvelopes(paths=all_paths, missing_scan_keys=[])
    except OSError as exc:
        # Covers a directory (or other non-file entry) at manifest_path
        # (IsADirectoryError), a permission-denied stat/read (PermissionError), and any
        # other read failure — all "can't read this as a manifest," same as a parse
        # failure below. Deliberately not a pre-check via .exists()/.is_file(): those
        # only swallow ENOENT-class errors, not EACCES, so a permission-denied stat
        # would otherwise escape uncaught instead of failing loud with a readable error.
        raise EnvelopeError(
            f"{manifest_path} exists but is not a valid RunManifest: {exc}"
        ) from exc

    try:
        manifest = RunManifest.model_validate_json(manifest_text)
    except ValidationError as exc:
        raise EnvelopeError(
            f"{manifest_path} exists but is not a valid RunManifest: {exc}"
        ) from exc

    scoped_keys = set(manifest.scan_keys)
    in_scope: list[Path] = []
    excluded: list[str] = []
    seen_keys: set[str] = set()
    for p in all_paths:
        stem = p.name.removesuffix(".result.json")
        if stem in scoped_keys:
            in_scope.append(p)
            seen_keys.add(stem)
        else:
            excluded.append(stem)

    if excluded:
        logger.debug(
            "Excluded %d envelope(s) outside run_manifest.json scope: %s",
            len(excluded),
            sorted(excluded),
        )

    missing_scan_keys = sorted(scoped_keys - seen_keys)
    return DiscoveredEnvelopes(paths=in_scope, missing_scan_keys=missing_scan_keys)


def validate_envelope(data: dict[str, Any]) -> None:
    """Validate against sleap-roots-contracts as a fail-fast gate (raise on invalid).

    Heavy imports are deferred so ``bloomctl --help`` stays fast. Pydantic's
    ``ValidationError`` is reshaped into a concise :class:`EnvelopeValidationError`
    (never dumped raw) — note the model is *stricter* than the RPC (it requires
    provenance fields the RPC ignores), so this can reject envelopes the RPC would
    otherwise accept, before any network call.
    """
    from pydantic import ValidationError
    from sleap_roots_contracts import ResultEnvelope

    try:
        ResultEnvelope.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        preview = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}".strip(": ")
            for e in errors[:5]
        )
        more = "" if len(errors) <= 5 else f" (+{len(errors) - 5} more)"
        raise EnvelopeValidationError(
            f"envelope failed sleap-roots-contracts validation "
            f"({len(errors)} error(s)): {preview}{more}"
        ) from exc


def load_predictions_manifest(predictions_dir: str | Path, scan_key: str) -> Any:
    """Read ``<predictions_dir>/{scan_key}.predictions.json`` as a ``PredictionManifest``.

    Heavy import deferred so ``bloomctl --help`` stays fast (matches
    ``validate_envelope``'s existing convention).
    """
    from pydantic import ValidationError
    from sleap_roots_contracts import PredictionManifest

    path = Path(predictions_dir) / f"{scan_key}.predictions.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise BlobConstructionError(f"predictions manifest not found: {path}") from exc
    try:
        return PredictionManifest.model_validate_json(text)
    except ValidationError as exc:
        raise BlobConstructionError(
            f"predictions manifest at {path} failed validation: {exc}"
        ) from exc
    except ValueError as exc:  # malformed JSON (pydantic wraps json.JSONDecodeError)
        raise BlobConstructionError(f"predictions manifest at {path} is not valid JSON: {exc}") from exc


def build_pending_blobs(
    manifest: Any, predictions_dir: str | Path, existing_blobs: list[dict[str, Any]]
) -> list[PendingBlob]:
    """Build one ``PendingBlob`` per manifest artifact.

    Raises :class:`BlobConstructionError` if ``existing_blobs`` already has an
    entry for a ``(root_type, scan_key)`` a constructed blob would also occupy
    — silently overwriting or duplicating it would hide a real data-integrity
    question (spec: "Conflicting pre-existing blob entry").
    """
    existing_keys = {(b.get("root_type"), b.get("scan_key")) for b in existing_blobs}
    predictions_dir = Path(predictions_dir)
    pending: list[PendingBlob] = []
    for artifact in manifest.artifacts:
        key = (artifact.root_type, manifest.scan_key)
        if key in existing_keys:
            raise BlobConstructionError(
                f"envelope already has a blobs entry for root_type={artifact.root_type!r} "
                f"scan_key={manifest.scan_key!r}, which --predictions-dir would also construct"
            )
        local_path = predictions_dir / artifact.slp_path
        # Defense-in-depth: predict-produced manifests are trusted pipeline
        # output today, but slp_path is still externally-produced structured
        # data flowing into a shared, multi-reader bucket. Refuse anything
        # that would resolve outside predictions_dir (e.g. a corrupted or
        # tampered manifest with a traversal path) before ever reading it.
        resolved_dir = predictions_dir.resolve()
        if not local_path.resolve().is_relative_to(resolved_dir):
            raise BlobConstructionError(
                f"artifact slp_path {artifact.slp_path!r} resolves outside "
                f"predictions_dir ({predictions_dir}) — refusing to read it"
            )
        blob = {
            "kind": artifact.kind,
            "root_type": artifact.root_type,
            "scan_key": manifest.scan_key,
            "checksum": artifact.checksum,
            "file_size": artifact.file_size,
            "s3_location": None,
            "box_link": None,
        }
        pending.append(PendingBlob(blob=blob, local_path=local_path))
    return pending


def verify_blob_checksum(path: str | Path, expected: str) -> None:
    """Recompute ``path``'s sha256 and compare to ``expected``.

    Raises :class:`BlobConstructionError` if the file is missing, or if the
    checksums disagree (naming the path and both checksums) — predict's
    manifest is an on-disk artifact that could in principle drift from the
    bytes it describes (partial write, manual edit, disk corruption).
    """
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise BlobConstructionError(f"blob file not found: {path}") from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise BlobConstructionError(
            f"checksum mismatch for {path}: expected {expected}, got {actual}"
        )


def blob_object_path(scan_key: str, idempotency_key: str, kind: str, root_type: str) -> str:
    """Deterministic object-storage key for a blob.

    An object-storage key, not a filesystem path — built with a plain string
    join, never ``pathlib.Path`` (which would silently emit backslashes on a
    Windows dev machine and produce a key that doesn't match what a Linux
    CI/prod run would derive for the same inputs). Keyed on ``idempotency_key``
    rather than ``source_id`` (cyl_scan_intermediates's own uniqueness anchor),
    because ``source_id`` is assigned by the RPC and unknown until it responds.
    """
    for name, value in (("scan_key", scan_key), ("idempotency_key", idempotency_key)):
        if "/" in value or "\\" in value or ".." in value:
            raise BlobConstructionError(
                f"{name} {value!r} contains a path separator or '..' — refusing to build "
                "an object-storage key from it"
            )
    return "/".join([scan_key, idempotency_key, f"{kind}.{root_type}.slp"])


def upload_blob(
    client: Any, local_path: str | Path, object_path: str, expected_checksum: str
) -> tuple[str, bool]:
    """Upload ``local_path``'s bytes to the ``cyl-intermediates`` bucket at
    ``object_path``, unless an object already exists there.

    Returns ``(location, skipped)``. If an object already exists at
    ``object_path`` with a matching checksum, the upload is skipped
    (idempotent no-op) and ``skipped`` is ``True``. If an object exists there
    with a *different* checksum, raises :class:`BlobConstructionError` rather
    than overwriting it (a path collision between two different runs' bytes).
    """
    from storage3.exceptions import StorageApiError

    bucket = client.storage.from_("cyl-intermediates")
    try:
        existing = bucket.download(object_path)
    except StorageApiError as exc:
        # Only a genuine "not found" means no upload has happened yet. Any
        # other status (permission denied, timeout, 5xx) must propagate --
        # silently treating it as "doesn't exist" would mask a real
        # infra/permission problem as an ordinary first upload.
        if str(getattr(exc, "status", "")) != "404":
            raise
        existing = None

    if existing is not None:
        existing_checksum = hashlib.sha256(existing).hexdigest()
        if existing_checksum == expected_checksum:
            return object_path, True
        raise BlobConstructionError(
            f"object already exists at {object_path} with a different checksum "
            f"(existing={existing_checksum}, new={expected_checksum}) — refusing to overwrite"
        )

    data = Path(local_path).read_bytes()
    bucket.upload(object_path, data)
    return object_path, False


@dataclass
class BlobUploadOutcome:
    """Outcome of constructing+uploading one blob."""

    root_type: str
    ok: bool
    skipped: bool = False
    location: str = ""
    error: str = ""


@dataclass
class BlobUploadReport:
    """Aggregate outcome of an `upload_pending_blobs` run."""

    outcomes: list[BlobUploadOutcome]

    @property
    def all_ok(self) -> bool:
        return all(o.ok for o in self.outcomes)

    @property
    def failed(self) -> list[BlobUploadOutcome]:
        return [o for o in self.outcomes if not o.ok]


def upload_pending_blobs(
    client: Any, pending: list[PendingBlob], *, scan_key: str, idempotency_key: str
) -> BlobUploadReport:
    """Verify + upload every pending blob, filling in ``s3_location`` on success.

    Each blob is independent: a failure (checksum mismatch, missing file,
    path collision, storage error) is recorded, not raised, so one bad blob
    can't abort the batch — mirroring ``download_images``'s per-frame
    discipline. Whether the *command* proceeds to call the RPC is gated by
    the caller on ``report.all_ok`` (a single-shot RPC call must never see a
    partially-populated ``blobs`` array).
    """
    outcomes: list[BlobUploadOutcome] = []
    for p in pending:
        # root_type is read inside the try (not before it) so a malformed
        # blob dict is recorded as a per-blob failure like everything else,
        # never an uncaught KeyError that kills the whole batch.
        root_type = "<unknown>"
        try:
            root_type = p.blob["root_type"]
            verify_blob_checksum(p.local_path, p.blob["checksum"])
            object_path = blob_object_path(scan_key, idempotency_key, p.blob["kind"], root_type)
            location, skipped = upload_blob(client, p.local_path, object_path, p.blob["checksum"])
            p.blob["s3_location"] = location
            outcomes.append(
                BlobUploadOutcome(root_type=root_type, ok=True, skipped=skipped, location=location)
            )
        except Exception as exc:  # per-blob: record and continue
            outcomes.append(BlobUploadOutcome(root_type=root_type, ok=False, error=str(exc)))
    return BlobUploadReport(outcomes)


def summarize_result(result: dict[str, Any]) -> str:
    """Human-readable one-liner for the RPC's return summary.

    A ``was_noop`` re-delivery is reported as a benign success (the RPC returns a
    null ``scan_id`` on that path, so it is not referenced here).
    """
    source_id = result.get("source_id")
    if result.get("was_noop"):
        return f"Already ingested (no-op): source_id={source_id} — nothing to do."
    return (
        f"Ingested: source_id={source_id} scan_id={result.get('scan_id')} "
        f"traits={result.get('trait_count', 0)} blobs={result.get('blob_count', 0)}."
    )


def map_rpc_error(message: str | None, *, profile: str | None = None) -> str:
    """Map an RPC RAISE EXCEPTION message to actionable CLI text.

    Known validations get a hint appended; unrecognized messages are returned
    verbatim (never swallowed). The RAISE strings are owned by the RPC migration
    (``cyl-trait-writeback``) — keep this match table in sync with it.
    """
    msg = (message or "").strip()
    if not msg:
        return "the write-back RPC failed (no message returned)."

    where = f" (profile {profile!r})" if profile else ""

    scan_markers = (
        "no image_ids",
        "unresolvable image_ids",
        "image_ids resolve to",
        "non-numeric image_id",
    )
    if any(m in msg for m in scan_markers):
        return (
            f"{msg}\n"
            f"No matching Bloom scan for this envelope's inputs.image_ids{where}. "
            "The scan's images must already exist in cyl_images on this server — check you are "
            "pointed at the right Bloom and that the images were uploaded."
        )
    if "contract_version mismatch" in msg:
        return (
            f"{msg}\n"
            "This server rejected the envelope's contract_version; re-pin the producer or the "
            "server to a shared contract version."
        )
    if "empty or absent idempotency_key" in msg:
        return (
            f"{msg}\nThe envelope's provenance.idempotency_key is empty — the producer must set it."
        )
    if "disagrees with provenance.scan_key" in msg:
        return (
            f"{msg}\n"
            "The envelope's scan_key is inconsistent — provenance.scan_key must match every "
            "trait/blob scan_key."
        )
    if "missing provenance.scan_key" in msg:
        return f"{msg}\nThe envelope is missing provenance.scan_key."
    if "permission denied" in msg:
        return (
            f"{msg}\n"
            "The authenticated profile lacks EXECUTE on insert_cyl_result_envelope — log in with "
            "a bloom_writer / bloom_admin account."
        )
    # Structural / trait / blob validations and anything unrecognized: verbatim.
    return msg


# --- supabase / storage I/O -------------------------------------------------


def resolve_argo_workflow_name() -> str | None:
    """`ARGO_WORKFLOW_NAME` when set and non-empty (Argo sets it inside the
    write-back container — see sleap-roots-write-back-template.yaml), else
    None for the existing manual/ad-hoc invocation shape."""
    return os.environ.get("ARGO_WORKFLOW_NAME") or None


def call_insert_envelope(
    client: Any, envelope: dict[str, Any], *, argo_workflow_name: str | None = None
) -> dict[str, Any]:
    """Call the SECURITY DEFINER RPC with the original envelope; return its jsonb summary.

    `argo_workflow_name`, when given, links the write-back to the matching
    `cyl_pipeline_run_scans` row (fix-cyl-pipeline-run-scan-status) — omitted
    from the payload entirely when None, relying on the RPC's own
    `DEFAULT NULL` rather than sending an explicit null, matching the
    existing manual-invocation call shape exactly.

    Lets ``postgrest.APIError`` propagate so the command can map it to a message.
    """
    payload: dict[str, Any] = {"envelope": envelope}
    if argo_workflow_name is not None:
        payload["p_argo_workflow_name"] = argo_workflow_name
    return client.rpc("insert_cyl_result_envelope", payload).execute().data


def reconcile_unresolved_scans(client: Any, argo_workflow_name: str) -> int:
    """Close out, as `'failed'`, any scan dispatched under `argo_workflow_name`
    that write-back never resolved either way — a prediction failure before
    write-back was ever attempted, or an envelope otherwise never produced
    (including the "manifest-declared scan_key with no matching file" case).
    Called once, at the end of a batch, only when `ARGO_WORKFLOW_NAME` is set.
    Returns the number of scans marked failed."""
    result = (
        client.rpc(
            "fail_cyl_pipeline_run_scans_without_result",
            {
                "p_argo_workflow_name": argo_workflow_name,
                "p_error_message": "no result produced for this scan by write-back",
            },
        )
        .execute()
        .data
    )
    return result or 0


# --- batch: non-raising per-envelope core ------------------------------------


def ingest_one_envelope(
    client: Any,
    envelope_path: str | Path,
    *,
    predictions_dir: str | Path | None = None,
    profile: str | None = None,
) -> ScanResult:
    """Ingest one envelope file, isolating any failure into a `ScanResult` instead of raising.

    Sequences the same steps `ingest_result` (the single-envelope command) does — read/parse via
    the existing `load_envelope` (so an unreadable or malformed file is isolated the same way a
    bad path/stdin input already is for the single command), validate, optionally construct +
    upload blobs, call the RPC — but never raises. When `predictions_dir` is given, it is expected
    to be predict's own nested batch output root; this looks up
    `predictions_dir/{scan_key}/{scan_key}.predictions.json` per envelope (reusing
    `load_predictions_manifest`/`build_pending_blobs`/`upload_pending_blobs` unchanged).
    """
    scan_key = envelope_path if isinstance(envelope_path, str) else envelope_path.name
    scan_key = Path(scan_key).name.removesuffix(".result.json")

    try:
        data = load_envelope(str(envelope_path))
    except EnvelopeError as exc:
        return ScanResult(scan_key, "failed", str(exc))

    # Prefer the envelope's own provenance.scan_key once it's readable, so a failure after this
    # point is reported under the scan's real key rather than the filename stem.
    scan_key = (data.get("provenance") or {}).get("scan_key") or scan_key

    try:
        validate_envelope(data)
    except EnvelopeValidationError as exc:
        return ScanResult(scan_key, "failed", str(exc))

    try:
        pending: list[PendingBlob] = []
        idempotency_key = ""
        if predictions_dir is not None:
            idempotency_key = data["provenance"].get("idempotency_key") or ""
            if not idempotency_key:
                return ScanResult(
                    scan_key,
                    "failed",
                    "envelope's provenance.idempotency_key is empty or absent — required to "
                    "derive the cyl-intermediates object path; the producer must set it before "
                    "blobs can be uploaded",
                )
            # scan_key is producer-supplied JSON content (no path-safety constraint from
            # sleap-roots-contracts) and is about to become a directory *segment*, not just a
            # string embedded in a filename — reject the same unsafe characters
            # blob_object_path already rejects for the object-storage key, before any local
            # filesystem access (review finding: this was a path-traversal gap).
            if "/" in scan_key or "\\" in scan_key or ".." in scan_key:
                return ScanResult(
                    scan_key,
                    "failed",
                    f"provenance.scan_key {scan_key!r} contains a path separator or '..' — "
                    "refusing to build a local predictions-dir path from it",
                )
            scan_predictions_dir = Path(predictions_dir) / scan_key
            try:
                manifest = load_predictions_manifest(scan_predictions_dir, scan_key)
                pending = build_pending_blobs(
                    manifest, scan_predictions_dir, data.get("blobs") or []
                )
            except BlobConstructionError as exc:
                return ScanResult(scan_key, "failed", str(exc))

            report = upload_pending_blobs(
                client, pending, scan_key=scan_key, idempotency_key=idempotency_key
            )
            if not report.all_ok:
                details = "; ".join(f"{o.root_type}: {o.error}" for o in report.failed)
                return ScanResult(
                    scan_key,
                    "failed",
                    f"blob upload failed for {len(report.failed)} of {len(report.outcomes)} "
                    f"blob(s): {details}",
                )
            data["blobs"] = [*(data.get("blobs") or []), *(p.blob for p in pending)]

        from postgrest import APIError

        try:
            result = call_insert_envelope(
                client, data, argo_workflow_name=resolve_argo_workflow_name()
            )
        except APIError as exc:
            return ScanResult(
                scan_key,
                "failed",
                map_rpc_error(getattr(exc, "message", None), profile=profile),
            )

        if not isinstance(result, dict):
            return ScanResult(scan_key, "failed", f"unexpected RPC response shape: {result!r}")

        if result.get("was_noop"):
            return ScanResult(scan_key, "skipped")
        return ScanResult(scan_key, "ok")
    except Exception as exc:  # batch isolation: a transient network/auth error on one
        # envelope must never abort the rest of the batch (review finding: this was
        # previously uncaught for anything other than postgrest.APIError/BlobConstructionError).
        return ScanResult(scan_key, "failed", str(exc))


# --- command ----------------------------------------------------------------


@click.command(name="ingest-result")
@click.argument("envelope")
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use (must have write access to the RPC).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the RPC result object as JSON on stdout (e.g. to capture source_id).",
)
@click.option(
    "--predictions-dir",
    "predictions_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Directory containing predict's {scan_key}.predictions.json + .slp files. "
        "When given, constructs BlobRef entries from the manifest, uploads the .slp "
        "bytes to the cyl-intermediates bucket, and merges them into the envelope's "
        "blobs before ingesting. Omit to forward blobs unchanged (no upload)."
    ),
)
def ingest_result(
    envelope: str, profile: str, as_json: bool, predictions_dir: Path | None
) -> None:
    """Ingest a per-scan ResultEnvelope (a path, or - for stdin) into Bloom.

    Validates the envelope against sleap-roots-contracts, then calls the
    insert_cyl_result_envelope RPC. Re-ingesting an already-ingested envelope is a
    benign no-op (first-writer-wins), reported distinctly from a real error.
    """
    from ..cli import _authed_client

    # Read + validate before any network call: the model gate catches shape and
    # missing-provenance errors up front (fails fast without authenticating). Some
    # value-level checks (e.g. an empty idempotency_key) are enforced only by the
    # RPC and surface below.
    try:
        data = load_envelope(envelope)
        validate_envelope(data)
    except EnvelopeError as exc:
        raise click.ClickException(str(exc)) from exc

    # Blob construction is pure (no I/O beyond local files) so it stays before
    # authentication, matching the envelope gate's "fail fast before any
    # network call" discipline. Upload needs the authed client, so it happens
    # after — but before the RPC call, since a single-shot RPC must never see
    # a partially-populated blobs array.
    pending: list[PendingBlob] = []
    if predictions_dir is not None:
        # scan_key has no contract-level default (Provenance requires it), so
        # validate_envelope already guarantees it's present; idempotency_key
        # DOES default to "" in the contract (a producer convenience -- the
        # model derives it from other fields when blank), so a validated
        # envelope's raw JSON can genuinely omit it. The RPC already rejects
        # that case cleanly ("empty or absent idempotency_key"); check it here
        # too, before spending time uploading blobs for a scheme (Decision 5)
        # that requires a real key to be correct.
        scan_key = data["provenance"]["scan_key"]
        idempotency_key = data["provenance"].get("idempotency_key") or ""
        if not idempotency_key:
            raise click.ClickException(
                "envelope's provenance.idempotency_key is empty or absent — required to "
                "derive the cyl-intermediates object path (see design.md Decision 5); the "
                "producer must set it before blobs can be uploaded"
            )
        try:
            manifest = load_predictions_manifest(predictions_dir, scan_key)
            pending = build_pending_blobs(manifest, predictions_dir, data.get("blobs") or [])
        except BlobConstructionError as exc:
            raise click.ClickException(str(exc)) from exc

    client = _authed_client(profile)

    if predictions_dir is not None:
        report = upload_pending_blobs(
            client, pending, scan_key=scan_key, idempotency_key=idempotency_key
        )
        if not report.all_ok:
            details = "; ".join(f"{o.root_type}: {o.error}" for o in report.failed)
            raise click.ClickException(
                f"blob upload failed for {len(report.failed)} of {len(report.outcomes)} "
                f"blob(s): {details}"
            )
        data["blobs"] = [*(data.get("blobs") or []), *(p.blob for p in pending)]

    from postgrest import APIError

    try:
        result = call_insert_envelope(
            client, data, argo_workflow_name=resolve_argo_workflow_name()
        )
    except APIError as exc:
        raise click.ClickException(
            map_rpc_error(getattr(exc, "message", None), profile=profile)
        ) from exc

    # The RPC RETURNS jsonb (an object) on every path; guard so any future
    # shape change surfaces as a clean error, not a bare AttributeError.
    if not isinstance(result, dict):
        raise click.ClickException(f"unexpected RPC response shape: {result!r}")

    if as_json:
        click.echo(json.dumps(result))
    else:
        click.echo(summarize_result(result))


# --- batch: command -----------------------------------------------------------


@click.command(name="batch-ingest-result")
@click.argument("envelopes_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option(
    "-p",
    "--profile",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Credentials profile to use (must have write access to the RPC).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the batch result as a JSON array on stdout.",
)
@click.option(
    "--predictions-dir",
    "predictions_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Predict's own nested batch output root, containing "
        "{scan_key}/{scan_key}.predictions.json + .slp files per scan. When given, constructs + "
        "uploads blobs for each envelope from its own scan_key's subdirectory. Omit to forward "
        "blobs unchanged (no upload)."
    ),
)
@click.pass_context
def batch_ingest_result(
    ctx: click.Context,
    envelopes_dir: Path,
    profile: str,
    as_json: bool,
    predictions_dir: Path | None,
) -> None:
    """Ingest every {scan_key}.result.json file directly under ENVELOPES_DIR — the batch
    sibling of `ingest-result`. If ENVELOPES_DIR contains a run_manifest.json, only the files
    it lists are ingested and a declared scan_key with no matching file is reported as a
    failure — unless a differently-named file's own content actually reports that scan_key
    (a filename/body mismatch), in which case the real outcome wins and the failure is
    dropped; with no manifest, every file is ingested (unchanged). Isolates per-envelope
    failures (one bad envelope doesn't abort the batch); exits non-zero if any envelope
    failed."""
    try:
        discovered = discover_envelopes(envelopes_dir)
    except EnvelopeError as exc:
        raise click.ClickException(str(exc)) from exc

    missing_results = [
        ScanResult(
            key,
            "failed",
            f"run_manifest.json lists scan_key {key!r} but no {key}.result.json was found "
            f"in {envelopes_dir}",
        )
        for key in discovered.missing_scan_keys
    ]

    argo_workflow_name = resolve_argo_workflow_name()

    if not discovered.paths and not missing_results:
        # Still reconcile when ARGO_WORKFLOW_NAME is set — even an empty batch
        # (every scan's prediction failed before producing any file at all)
        # must close out this workflow's scans as 'failed', not leave them
        # 'queued' forever. Unset, this is the pre-existing manual/local
        # no-envelopes-no-manifest shape: no client, no RPC call, unchanged.
        if argo_workflow_name:
            from ..cli import _authed_client

            client = _authed_client(profile)
            reconcile_unresolved_scans(client, argo_workflow_name)
        click.echo("No envelope files found; nothing to ingest.")
        return

    if discovered.paths:
        from ..cli import _authed_client

        client = _authed_client(profile)
        ingest_results = [
            ingest_one_envelope(client, path, predictions_dir=predictions_dir, profile=profile)
            for path in discovered.paths
        ]
        # ingest_one_envelope relabels its result by the envelope body's own scan_key once
        # read, which can differ from the filename discover_envelopes matched against the
        # manifest. Drop a "missing" entry for any key that was, in fact, actually ingested —
        # otherwise the same scan_key could appear twice with contradictory ok/failed statuses.
        ingested_scan_keys = {r.scan_key for r in ingest_results}
        collided_keys = sorted(
            r.scan_key for r in missing_results if r.scan_key in ingested_scan_keys
        )
        if collided_keys:
            logger.debug(
                "Dropped %d manifest-declared-missing entry/entries superseded by an "
                "actual ingest result under the same scan_key (filename/body scan_key "
                "mismatch): %s",
                len(collided_keys),
                collided_keys,
            )
        missing_results = [r for r in missing_results if r.scan_key not in ingested_scan_keys]
        scan_results = ingest_results + missing_results
    else:
        # Only manifest-declared-missing entries, no files at all. The
        # pre-existing "never authenticate" behavior is preserved when
        # ARGO_WORKFLOW_NAME is unset; when it IS set, a client is needed
        # purely to make the one reconciliation call below.
        if argo_workflow_name:
            from ..cli import _authed_client

            client = _authed_client(profile)
        scan_results = missing_results

    if argo_workflow_name:
        reconcile_unresolved_scans(client, argo_workflow_name)

    batch_result = BatchResult(scan_results)

    if as_json:
        click.echo(format_json(batch_result))
    else:
        click.echo(
            format_summary(
                batch_result, verb="Ingested", noun="envelope", destination=str(envelopes_dir)
            )
        )

    if not batch_result.ok:
        ctx.exit(1)

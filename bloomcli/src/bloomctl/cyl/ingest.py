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

import json
import sys
from pathlib import Path
from typing import Any, TextIO

import click

from ..credentials import DEFAULT_PROFILE


class EnvelopeError(Exception):
    """Envelope could not be read or parsed (bad path, non-JSON, empty input)."""


class EnvelopeValidationError(EnvelopeError):
    """Envelope did not conform to the sleap-roots-contracts ResultEnvelope."""


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


def call_insert_envelope(client: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """Call the SECURITY DEFINER RPC with the original envelope; return its jsonb summary.

    Lets ``postgrest.APIError`` propagate so the command can map it to a message.
    """
    return client.rpc("insert_cyl_result_envelope", {"envelope": envelope}).execute().data


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
def ingest_result(envelope: str, profile: str, as_json: bool) -> None:
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

    client = _authed_client(profile)

    from postgrest import APIError

    try:
        result = call_insert_envelope(client, data)
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

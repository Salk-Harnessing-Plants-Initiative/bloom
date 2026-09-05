"""Shared ephemeral CSV-parsing helper for a tool's inline-content input path (#582).

Parses a caller-supplied CSV string directly into an in-memory :class:`ExperimentFrame`
— never written to Storage, never registered, never persisted. Resolves column roles
and trait columns through the same :func:`resolve_columns` unit every
:class:`ExperimentReader` adapter uses, so an inline frame is indistinguishable in shape
from an adapter-sourced one.

``resolve_inline_or_experiment`` is the entry point every tool uses. It owns the
exactly-one-of rule, the rejection of parameters that only mean something against a
registered experiment, the parse, and the ``input_sha256`` — in one place, with one
message vocabulary, so ten tools cannot drift on what "exactly one is required"
says. Everything genuinely per-tool (``require_clean``, version pinning, read-error
mapping) stays in the tool and reaches this module as ``reader_call``.

Routing every tool through it is also what makes the size, row-count and
column-count guards below unbypassable: no tool calls ``pandas.read_csv`` on
caller-supplied content itself.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

import pandas as pd

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import ExperimentFrame
from bloom_mcp.data_access.columns import resolve_columns

# By the time this string reaches us it is already fully materialized in memory
# (the MCP transport/JSON layer allocated it) — this check does not prevent that
# initial allocation. What it bounds is everything downstream: pandas' in-memory
# representation of a parsed frame is a multiple of the raw text size, so this
# caps that multiplication before `pandas.read_csv` runs, in a shared container
# with no upload step to rate-limit a caller-controlled payload first. See
# design.md for the rationale behind this specific number.
MAX_INLINE_CSV_BYTES = 5 * 1024 * 1024

# A byte cap alone does NOT bound CPU cost: a pathologically wide-but-short CSV
# (many narrow columns) can sit comfortably under MAX_INLINE_CSV_BYTES while
# still costing seconds of CPU in pandas' per-column overhead (dtype inference,
# Python object overhead for labels) — measured directly: ~480,000 columns in a
# single row, 4.69 MB (under the byte cap), took ~7.7s of CPU in
# `pandas.read_csv` alone, with bloommcp having no rate limiting in front of
# this path (FastMCP ships a RateLimitingMiddleware but it is not wired into
# server.py) and no persistence step to create natural backpressure — a real,
# reproducible DoS vector for the shared container. A post-parse check on
# `df.shape[1]` cannot prevent this: the expensive parse has already run by the
# time it fires. `_estimate_header_columns` below is the actual guard — a cheap
# pre-parse estimate that rejects before `pandas.read_csv` is ever called;
# `df.shape[1] > MAX_INLINE_CSV_COLUMNS` after parsing is kept only as an exact
# backstop, not the primary guard.
MAX_INLINE_CSV_COLUMNS = 2000

# Caps how much of csv_content `_estimate_header_columns` will scan looking for
# the header row's closing boundary. A row-aware scan (see below) must read an
# unterminated quoted field until it finds the closing quote or gives up — an
# attacker who never closes the quote could otherwise force a scan of the
# entire payload, defeating the point of a "cheap" pre-parse check. No real
# header row (even at MAX_INLINE_CSV_COLUMNS columns with generous name
# lengths) comes close to this.
_MAX_HEADER_SCAN_BYTES = 256 * 1024

# Neither cap above bounds a *super-linear* tool. Measured through this very
# parser: a 5,242,866-byte payload (14 bytes under MAX_INLINE_CSV_BYTES) is
# accepted in ~0.03s and yields 313,171 rows. `clustering(method="hierarchical")`
# is cleanly O(n^2) in time and resident memory (n=6,000 -> 1.70s/+809 MiB;
# n=12,000 -> 7.24s/+2.38 GiB), so that row count implies a condensed distance
# matrix of hundreds of gibibytes; and `cross_experiment_correlations` runs an
# all-pairs loop at ~326us per trait pair. Nothing throttles this path: no rate
# limiting is wired into server.py, the proxy sets no request-body cap, tools are
# registered without a timeout, and no compose service declares a memory limit —
# so an OOM is resolved by the *host* killer, which may select the database
# rather than bloommcp.
#
# 20,000 is roughly a hundred times the largest real experiment fixture in this
# repo (turface_19: 187 rows; cylinder: 129) and ~15x below what the byte cap
# alone admits. Tools whose cost is worse than linear in the row count add their
# own, stricter inline caps on top of this one.
MAX_INLINE_CSV_ROWS = 20_000

# Kill switch (#582). bloommcp has no feature flags, and the deploy pipeline's
# automatic rollback fires only when the deploy *job* fails — a successfully
# deployed but misbehaving build is otherwise reverted only by a new commit
# through a full multi-image rebuild. This change turns on the inline path for
# ten tools at once, so one variable and a container restart is a proportionate
# off switch. Read per call, not at import, so a restart is enough.
_KILL_SWITCH_ENV = "BLOOMMCP_INLINE_CSV_ENABLED"
_FALSEY = {"0", "false", "no", "off"}

_BOM = "﻿"


def _bounded_lines(text: str):
    """Yield ``text``'s lines like iterating ``io.StringIO(text)``, but raise
    ``BloomMCPError`` if more than `_MAX_HEADER_SCAN_BYTES` is consumed without
    the caller stopping — the guard against an unterminated quote forcing
    `_scan_leading_row_widths` to scan the whole payload (see its docstring).

    This bound is load-bearing beyond the unterminated-quote case: a single
    legitimately-terminated but enormous row (the wide-data-row DoS shape) also
    exhausts it, so an oversized leading row is refused here in milliseconds
    rather than measured and then refused.
    """
    consumed = 0
    for line in io.StringIO(text):
        consumed += len(line)
        if consumed > _MAX_HEADER_SCAN_BYTES:
            raise BloomMCPError(
                code="invalid_input",
                message=(
                    f"csv_content's header and first data row could not be "
                    f"read within the first {_MAX_HEADER_SCAN_BYTES} bytes — "
                    f"a leading row is either malformed or unusably wide."
                ),
                remedy=(
                    "Ensure the first rows are well-formed (every quote closed) "
                    "and not unusually large, or register the data as an "
                    "experiment instead of passing it inline."
                ),
            )
        yield line


def _scan_leading_row_widths(csv_content: str) -> tuple[int, Optional[int]]:
    """Cheap field-count scan of the header **and the first data row**.

    Returns ``(header_fields, first_data_fields)``; the second is ``None`` when
    the content has no data row. Does not parse the body.

    Feeds `csv.reader` a bounded line iterator (`_bounded_lines`), not a naive
    ``csv_content.split("\\n", 1)[0]``. The naive split cuts a row short the
    moment any field contains a literal newline inside quotes (valid CSV) —
    reproduced directly: a crafted header whose first cell is a quoted value
    containing one embedded newline made the naive split's estimate say "1
    column" for a real ~480,000-column row, letting the expensive
    `pandas.read_csv` call run anyway (~5-9s of CPU) before the post-parse
    backstop caught it — exactly the cost this guard exists to avoid.
    `csv.reader` fed a genuine line iterator instead handles this correctly:
    it keeps consuming lines from the iterator until the quoted field's
    closing quote is found and the row is complete, the same way iterating a
    real file handles a multi-line quoted CSV field. `_bounded_lines` caps how
    far it will do that (an unterminated quote would otherwise force scanning
    the entire payload), rejecting outright rather than guessing when a row's
    true extent can't be found cheaply.

    **Why the first data row and not just the header.** Measuring the header
    alone left the column cap fully bypassable, reproduced on this machine: a
    3-field header paired with data rows of 480,000 fields (1.92 MB — nowhere
    near any cap) was *accepted* after 16s in `pandas.read_csv`. `read_csv` does
    not require data rows to match the header's width; when they are
    consistently wider it silently absorbs the surplus into an implicit index
    rather than raising, which is both expensive and invisible — the resulting
    frame had 3 columns, so even the post-parse `df.shape[1]` backstop passed.
    Parse cost tracks the *widest row's* field count, not the header's, so the
    guard has to see a data row.

    Sampling one data row is sufficient rather than merely convenient: the
    silent-and-slow path requires the divergence to be *consistent*. A single
    wide row among narrow ones is inconsistent, and `read_csv` rejects that with
    a `ParserError` in ~0.00s (measured), which is already mapped to
    ``invalid_input``. So a wide row hiding beyond the scan window is either
    consistent with row 1 — and caught here — or inconsistent, and caught
    cheaply by the parser itself.
    """
    reader = csv.reader(_bounded_lines(csv_content))
    try:
        header = next(reader)
    except StopIteration:
        return 0, None
    try:
        data = next(reader)
    except StopIteration:
        return len(header), None
    return len(header), len(data)


def parse_inline_csv_frame(csv_content: str) -> ExperimentFrame:
    """Parse ``csv_content`` into an in-memory :class:`ExperimentFrame`.

    Raises :class:`BloomMCPError` (``invalid_input``) for an oversized payload,
    too many columns, too many rows, unparseable content, zero data rows, zero
    columns, or an encode/decode failure — never a raw ``pandas``/``Unicode``
    exception.
    """
    # Strip every leading BOM, not just one — a double-encoded or re-saved file
    # can carry more than one, and any left in place mangles the first column
    # name (e.g. "﻿Barcode"), silently breaking role detection for it.
    csv_content = csv_content.lstrip(_BOM)

    # O(1) short-circuit before anything touches the string: UTF-8 encodes each
    # character to at least one byte, so a character count over the cap is
    # already over the byte cap. Rejecting here avoids materializing a second
    # full copy of a grossly oversized payload just to measure it (the encode
    # below doubles peak memory for the duration of the check). Content under
    # this bound still gets the exact byte check further down — multi-byte
    # characters mean fewer characters can still be more bytes.
    if len(csv_content) > MAX_INLINE_CSV_BYTES:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content is at least {len(csv_content)} bytes, exceeding "
                f"the {MAX_INLINE_CSV_BYTES}-byte limit for inline content."
            ),
            remedy=(
                "Reduce the CSV content size, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )

    try:
        encoded = csv_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A lone UTF-16 surrogate (possible via a lossy upstream decode) raises
        # here, not in pandas — must be mapped explicitly or it becomes an
        # opaque internal_error, contradicting this module's "never a raw
        # Unicode exception" guarantee.
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be encoded as UTF-8: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text (no unpaired "
            "surrogates) and retry.",
        ) from None

    byte_length = len(encoded)
    if byte_length > MAX_INLINE_CSV_BYTES:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content is {byte_length} bytes, exceeding the "
                f"{MAX_INLINE_CSV_BYTES}-byte limit for inline content."
            ),
            remedy=(
                "Reduce the CSV content size, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )

    # Width guards — what actually prevent the wide-CSV CPU DoS (see
    # MAX_INLINE_CSV_COLUMNS above). They run after the size checks and before
    # `pandas.read_csv`: after, so a payload that is simply too large is reported
    # as too large rather than as an unreadable leading row (the scan bound would
    # otherwise fire first on a single oversized row and blame the wrong thing);
    # before the parse, because the post-parse backstop cannot help once the
    # parse has been paid, and in the divergence case below it never fires at all.
    # Encoding a payload already known to be within the cap is bounded work, so
    # nothing is lost by checking size first.
    header_columns, data_columns = _scan_leading_row_widths(csv_content)
    if header_columns > MAX_INLINE_CSV_COLUMNS:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content's header implies approximately {header_columns} "
                f"columns, exceeding the {MAX_INLINE_CSV_COLUMNS}-column limit "
                f"for inline content."
            ),
            remedy=(
                "Reduce the number of columns, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )
    if data_columns is not None and data_columns > MAX_INLINE_CSV_COLUMNS:
        # The header-only check left this fully bypassable — see
        # `_scan_leading_row_widths` for the reproduced 1.92 MB / 16s case.
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content's first data row has {data_columns} fields, "
                f"exceeding the {MAX_INLINE_CSV_COLUMNS}-column limit for "
                f"inline content."
            ),
            remedy=(
                "Reduce the number of fields per row, or register the data as "
                "an experiment instead of passing it inline."
            ),
        )
    if data_columns is not None and data_columns != header_columns:
        # Rejecting divergence outright, rather than letting pandas resolve it,
        # is a correctness fix as much as a cost one. Measured on a 3-name
        # header against 4-field rows: the default read silently promotes the
        # first field to the index, so every remaining value lands under the
        # WRONG column name (the barcodes became the index and the genotypes
        # became "Barcode"); `index_col=False` instead silently drops the last
        # field. For a tool whose whole point is traceable, contract-valid trait
        # data, either outcome is worse than a refusal — a misaligned frame
        # cleans and analyzes without complaint and reports confident nonsense.
        #
        # This does not reject the common "saved with the index" round trip:
        # `to_csv(index=True)` emits an empty first header name, so the field
        # counts still match (verified: 4 and 4).
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content's header has {header_columns} fields but its "
                f"first data row has {data_columns}. A row wider than the "
                f"header would silently shift values into the wrong columns."
            ),
            remedy=(
                "Give every row the same number of fields as the header "
                "(quote any field that contains a comma), then retry."
            ),
        )

    try:
        df = pd.read_csv(io.StringIO(csv_content))
    except pd.errors.EmptyDataError:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no data rows.",
            remedy="Supply CSV content with a header row and at least one data row.",
        ) from None
    except pd.errors.ParserError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be parsed as CSV: {exc}",
            remedy="Fix the CSV formatting (consistent field counts per row) and retry.",
        ) from None
    except UnicodeDecodeError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be decoded: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text and retry.",
        ) from None

    if df.shape[1] == 0:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no columns.",
            remedy="Supply CSV content with a header row naming at least one column.",
        )
    if df.shape[1] > MAX_INLINE_CSV_COLUMNS:
        # Exact backstop, not the primary guard: _estimate_header_columns
        # above already uses the same csv.reader-based, multi-line-aware
        # tokenization pandas itself effectively performs, so this should not
        # fire in practice — kept as defense-in-depth against any residual
        # divergence between the two, at the cost of the parse already having
        # run.
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content has {df.shape[1]} columns, exceeding the "
                f"{MAX_INLINE_CSV_COLUMNS}-column limit for inline content."
            ),
            remedy=(
                "Reduce the number of columns, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )
    if df.shape[0] == 0:
        raise BloomMCPError(
            code="invalid_input",
            message="csv_content has no data rows.",
            remedy="Supply CSV content with a header row and at least one data row.",
        )
    if df.shape[0] > MAX_INLINE_CSV_ROWS:
        # Post-parse is the right place for this one, unlike the column guard:
        # parsing is linear and cheap (~0.03s even at the byte cap), so the cost
        # this bounds is everything *downstream* — a tool's own super-linear work
        # — not the parse itself. See MAX_INLINE_CSV_ROWS above.
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"csv_content has {df.shape[0]} rows, exceeding the "
                f"{MAX_INLINE_CSV_ROWS}-row limit for inline content."
            ),
            remedy=(
                "Reduce the number of rows, or register the data as an "
                "experiment instead of passing it inline."
            ),
        )

    resolved = resolve_columns(df)
    return ExperimentFrame(
        df=df,
        trait_cols=resolved.trait_cols,
        metadata_cols=resolved.metadata_cols,
        genotype_col=resolved.genotype,
        replicate_col=resolved.replicate,
        sample_id_col=resolved.sample_id,
        source="inline",
    )


def compute_input_sha256(csv_content: str) -> str:
    """SHA-256 hex digest over the exact UTF-8-encoded bytes of ``csv_content``.

    Computed over the original string (before any BOM-stripping), so it reflects
    exactly what the caller sent. Independent of any manifest-/``Provenance``-level
    hash — this value exists solely for the caller's own record-keeping, since
    nothing is stored server-side to check it against later.

    Raises :class:`BloomMCPError` (``invalid_input``) rather than a raw
    ``UnicodeEncodeError`` if ``csv_content`` cannot be encoded (e.g. a lone
    surrogate) — this function is a public entry point in its own right, not
    guaranteed to run only after ``parse_inline_csv_frame`` has already
    validated the same string.
    """
    try:
        encoded = csv_content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BloomMCPError(
            code="invalid_input",
            message=f"csv_content could not be encoded as UTF-8: {exc}",
            remedy="Ensure csv_content is valid UTF-8 text (no unpaired "
            "surrogates) and retry.",
        ) from None
    return hashlib.sha256(encoded).hexdigest()


def serialize_table_csv(
    df: pd.DataFrame,
    *,
    field: str = "csv",
    verify_trait_cols: Optional[Iterable[str]] = None,
) -> str:
    """Serialize *df* to CSV text for an opt-in inline table return (#582).

    Used by the two producer tools (``qc_clean``'s ``return_cleaned_csv`` and
    ``remove_outliers``' ``return_trimmed_csv``) to hand the produced table back
    in the response, so a caller can chain client-side — pass the text as the
    next tool's ``csv_content``. This is **not** persistence: the text goes into
    the response and nowhere else, and the server records no lineage between the
    two calls.

    ``lineterminator="\n"`` is explicit, not incidental: pandas defaults it to
    ``os.linesep``, which would make the returned text — and therefore the
    digest the caller records — depend on the platform bloommcp happens to run
    on. (``.gitattributes`` already forces LF on this repo's CSVs for the same
    class of bug.)

    Raises :class:`BloomMCPError` (``invalid_input``) rather than returning a
    multi-megabyte string through the MCP transport when the result exceeds
    ``MAX_INLINE_CSV_BYTES``. Reusing the *input* cap avoids inventing a second
    number and is conservative: cleaning and trimming only ever remove rows and
    columns, so a result over the cap means the input was already near it.

    ``verify_trait_cols`` makes the handoff **structural instead of coincidental**.
    A producer certifies a specific set of trait columns; the consumer that
    receives this text re-derives its own trait set by running
    :func:`resolve_columns` over the *re-parsed* frame. That those two agree is
    the whole basis for chaining, and today it holds only because upstream's
    removal criteria and ``resolve_columns``' detection heuristic happen to
    coincide — two independently-evolving pieces of logic. Passing the certified
    set here re-parses the serialized text and checks the agreement for real,
    raising rather than handing back a table that would fail (or, worse, silently
    analyze the wrong columns) in the next call. The round trip is the right
    place to check because the property is about the *text*: a dtype that shifts
    on re-parse changes what ``resolve_columns`` detects, which a check against
    the in-memory frame would miss entirely.

    The extra parse is paid only on the opt-in table-return path, where the
    caller has explicitly asked for bytes they intend to hand to another tool,
    and is bounded by ``MAX_INLINE_CSV_BYTES``.
    """
    text = df.to_csv(index=False, lineterminator="\n")
    size = len(text.encode("utf-8"))
    if size > MAX_INLINE_CSV_BYTES:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"The table requested via {field} serializes to {size} bytes, "
                f"exceeding the {MAX_INLINE_CSV_BYTES}-byte limit for inline "
                f"content."
            ),
            remedy=(
                f"Omit {field} and use the summary, or register the data as an "
                "experiment so the table is persisted as a downloadable artifact."
            ),
        )

    if verify_trait_cols is not None:
        expected = set(verify_trait_cols)
        round_tripped = resolve_columns(pd.read_csv(io.StringIO(text)))
        actual = set(round_tripped.trait_cols)
        if actual != expected:
            raise BloomMCPError(
                code="assumption_violated",
                message=(
                    f"The table returned via {field} does not re-resolve to the "
                    f"trait columns it was certified with: "
                    f"{sorted(expected - actual)} would be lost and "
                    f"{sorted(actual - expected)} would be picked up. Returning "
                    f"it would hand the next call a different analysis than the "
                    f"one just reported."
                ),
                remedy=(
                    f"Omit {field} and use the summary, or register the data as "
                    "an experiment so the next tool resolves a committed cleaned "
                    "version instead of re-detecting roles from text."
                ),
            )
    return text


def inline_enabled() -> bool:
    """Whether the inline ``csv_content`` path is enabled (default: yes).

    Read per call rather than cached at import so flipping
    ``BLOOMMCP_INLINE_CSV_ENABLED`` takes effect on a container restart instead
    of a rebuild — see ``_KILL_SWITCH_ENV`` above.
    """
    raw = os.getenv(_KILL_SWITCH_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


@dataclass(frozen=True)
class InlineInput:
    """The frame a tool will analyze, plus how it was obtained.

    ``label`` is what a tool interpolates wherever it would otherwise name the
    experiment — the experiment identifier on the registered path, the literal
    ``"csv_content"`` on the inline path. Tools use it instead of
    ``params.experiment`` so no error message ever renders ``'None'`` as an
    identifier, including messages raised deep inside a tool (``remove_outliers``'
    fit-quality gate is the awkward one).
    """

    frame: Any
    is_inline: bool
    label: str
    input_sha256: Optional[str] = None


_INLINE_LABEL = "csv_content"

# Why each registered-only parameter cannot apply, single-sourced so ten tools
# quote one wording. A generic "only applies to a registered experiment's stored
# versions and sources" is true of the source/version pins but plainly wrong for
# `user_label` (which is about writing, not reading) and for the plot flags — and
# an inaccurate rejection message is exactly the kind of drift this module exists
# to prevent. Each clause completes "<name> cannot be used with <csv_content>: it
# <clause>."
_REGISTERED_ONLY_REASONS: dict[str, str] = {
    "source_id": (
        "pins which stored raw source to read, and {inline} is read directly "
        "rather than from a registered experiment's sources"
    ),
    "run_id": (
        "pins which stored raw source to read by its pipeline run, and {inline} "
        "is read directly rather than from a registered experiment's sources"
    ),
    "version": (
        "pins which committed version to read, and {inline} is read directly "
        "rather than from a registered experiment's version history"
    ),
    "version_1": (
        "pins which committed version to read for side 1, which is supplied "
        "inline rather than as a registered experiment"
    ),
    "version_2": (
        "pins which committed version to read for side 2, which is supplied "
        "inline rather than as a registered experiment"
    ),
    "user_label": (
        "names the version directory a committed run is written into, and no run "
        "is created for {inline}"
    ),
}

_PLOT_PARAM_REASON = (
    "configures figures that are persisted as run artifacts, and no run is "
    "created for {inline}"
)


def _registered_only_reason(name: str, inline_field: str) -> str:
    """The clause explaining why *name* cannot apply, or a safe generic fallback.

    Plot-companion parameters share one reason and are matched by prefix so a new
    ``plot_*`` knob on any tool inherits correct wording instead of silently
    falling through to the generic clause.
    """
    template = _REGISTERED_ONLY_REASONS.get(name)
    if template is None and (name == "include_plots" or name.startswith("plot")):
        template = _PLOT_PARAM_REASON
    if template is None:
        template = "only applies to a registered experiment, which {inline} is not"
    return template.format(inline=inline_field)


def reject_registered_only_params(
    registered_only: Mapping[str, Any],
    *,
    csv_content_field: str = "csv_content",
) -> None:
    """Reject parameters that only mean something against a registered experiment.

    **Reject, never silently ignore.** A caller who supplied a pin and got a
    successful result must not be left believing the pin took effect. Callers
    pass only the parameters they actually mean — a ``None`` entry is a no-op, so
    a tool can hand over its whole roster without filtering first, but a
    default-valued flag (``include_plots=False``) must be filtered by the caller
    rather than relied on being falsy here: ``version="latest"`` is falsy-looking
    but is a real pin request on ``remove_outliers``, so this function tests for
    ``None`` and nothing else.

    Every offender is named, not just the first — a caller who passed two bad
    parameters should fix both in one round trip.
    """
    offenders = sorted(k for k, v in registered_only.items() if v is not None)
    if not offenders:
        return
    listed = ", ".join(offenders)
    clauses = "; ".join(
        f"{name} {_registered_only_reason(name, csv_content_field)}"
        for name in offenders
    )
    raise BloomMCPError(
        code="invalid_input",
        message=f"{listed} cannot be used with {csv_content_field}: {clauses}.",
        remedy=(
            f"Omit {listed} when using {csv_content_field}, or supply a "
            f"registered experiment instead of {csv_content_field}."
        ),
    )


def resolve_inline_or_experiment(
    *,
    experiment: Optional[str],
    csv_content: Optional[str],
    reader_call: Optional[Callable[[], Any]] = None,
    registered_only: Optional[Mapping[str, Any]] = None,
    registered_field: str = "experiment",
    csv_content_field: str = "csv_content",
) -> InlineInput:
    """Resolve a tool's frame from exactly one of ``experiment`` / ``csv_content``.

    The single entry point every inline-capable tool uses, so the exactly-one-of
    rule, the registered-only rejection, the parse, and the ``input_sha256`` have
    one implementation and one message vocabulary across the whole tool roster.

    ``registered_field`` / ``csv_content_field`` name the caller's own parameters:
    ``load_experiment_data`` pairs ``csv_content`` with ``filename``, and
    ``cross_experiment_correlations`` resolves each side independently
    (``experiment_2`` / ``csv_content_2``). Messages are therefore identical
    across tools *modulo those names*, which is the strongest equality that is
    actually true.

    ``reader_call`` is the tool's own read, invoked only on the registered path.
    Keeping it a callable is what leaves ``require_clean``, version pinning and
    read-error mapping in the tool where they belong — this module never learns
    what "cleaned" means.

    **Check order is specified, not incidental.** The exactly-one-of check runs
    first, so a call that is wrong in two ways reports the input conflict rather
    than a parameter conflict that is moot; without that, a per-tool assertion
    like "the error names version_2 only" would depend on order and flake.
    """
    has_experiment = experiment is not None
    has_inline = csv_content is not None

    if has_experiment == has_inline:
        raise BloomMCPError(
            code="invalid_input",
            message=(
                f"Exactly one of {registered_field} or {csv_content_field} must "
                f"be provided (both or neither is not a valid call)."
            ),
            remedy=(
                f"Supply exactly one of {registered_field} (a registered "
                f"experiment identifier) or {csv_content_field} (raw CSV text "
                f"for a one-off analysis)."
            ),
        )

    if not has_inline:
        if reader_call is None:
            # A programming error in the calling tool, not a caller error: fail
            # loudly here rather than returning a frameless result that explodes
            # somewhere less obvious.
            raise ValueError(
                "resolve_inline_or_experiment requires reader_call on the "
                f"{registered_field} path"
            )
        return InlineInput(
            frame=reader_call(), is_inline=False, label=experiment, input_sha256=None
        )

    if not inline_enabled():
        raise BloomMCPError(
            code="invalid_input",
            message=(f"Inline {csv_content_field} input is disabled on this server."),
            remedy=(
                f"Register the data as an experiment and supply "
                f"{registered_field} instead, or ask an administrator to "
                f"re-enable inline input."
            ),
        )

    if registered_only:
        reject_registered_only_params(
            registered_only, csv_content_field=csv_content_field
        )

    return InlineInput(
        frame=parse_inline_csv_frame(csv_content),
        is_inline=True,
        label=_INLINE_LABEL,
        input_sha256=compute_input_sha256(csv_content),
    )


__all__ = [
    "MAX_INLINE_CSV_BYTES",
    "MAX_INLINE_CSV_COLUMNS",
    "MAX_INLINE_CSV_ROWS",
    "InlineInput",
    "compute_input_sha256",
    "inline_enabled",
    "parse_inline_csv_frame",
    "reject_registered_only_params",
    "resolve_inline_or_experiment",
    "serialize_table_csv",
]

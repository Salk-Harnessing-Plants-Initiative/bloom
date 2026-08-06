"""get_download_links — re-sign fresh download links for an already-committed run.

Not a ``sleap-roots-analyze`` wrapper — reads through the injected
``ResultStore`` port, exactly like ``list_existing_analyses``. Unlike
``get_run``/``list_runs``/``list_existing_analyses``, which always leave
``output_links`` empty (bloom#581 Decision 1), this tool is the deliberate,
caller-opted-in exception: a caller who already knows a specific
``(experiment, tool_class, run_ref)`` — e.g. from a prior `list_existing_analyses`
call, or from a tool response in a now-expired chat session — can get a fresh
signed URL, hash, and size for each of that run's outputs (bloom#599).

Not registered in ``ALWAYS_INCLUDE_MCP_TOOLS`` — this is a targeted,
on-demand retrieval tool, not a session-bootstrap discovery tool (see
``openspec/changes/add-bloommcp-get-download-links/design.md`` Decision 5).
"""

import json

from bloom_mcp.experiment_utils import safe_error_text
from bloom_mcp.tools import _ports


def get_download_links(
    experiment: str, tool_class: str, run_ref: str = "latest"
) -> str:
    """Re-sign fresh download links for an already-committed run's outputs.

    Use this to get a working download link for a run that was committed in
    a prior tool call or chat session (its own signed URLs have since
    expired, or the session ended before the link was used). For a run whose
    outputs were just committed *in this same tool call* (e.g. `pca_analysis`,
    `qc_clean`), that tool's own response already carries fresh
    `output_links` — you do not need to call this tool for it.

    `size_bytes` is always resolved live, on every call — it is never cached
    or persisted, so this makes one extra network round-trip per output.
    A single output's lookup failure fails the whole call (no
    partially-populated `output_links` is ever returned). A legacy run
    recorded before per-artifact keys existed (a v2 manifest entry) has
    nothing to sign — its `output_links` comes back empty, not an error.
    Always verify what you download against the returned `sha256` — it comes
    from the immutable manifest record, independent of the live-refreshed
    `url`/`size_bytes`.

    Also returns `manifest_url` — a fresh signed/served link for the run's
    own `manifest.json` (bloom#600), independent of whether `output_links`
    is empty: a run's manifest always exists once committed, so this is
    never skipped for a legacy run the way `output_links` is. Unlike each
    `output_links` entry, `manifest_url` has no `sha256`/`size_bytes`
    counterpart — it is a bare link; fetch and read the manifest itself if
    you need its contents. That fetched content includes
    `ExperimentBlock.source_path` (an absolute host path, not a credential —
    see `docs/storage-backends.md`), returned exactly as stored, with no
    redaction or filtering.

    Args:
        experiment: experiment identifier, e.g. "alfalfa_gwas_wave2.csv"
        tool_class: the tool's storage class, e.g. "qc", "pca", "clustering"
            (see `list_existing_analyses`'s response for the exact classes
            recorded for a given experiment — including retired-but-historical
            ones, which remain resolvable here)
        run_ref: a specific version id (e.g. "v3"), or "latest" (default) for
            the most recent run
    """
    known = {exp.filename for exp in _ports.reader().list_experiments()}
    if known and experiment not in known:
        return json.dumps(
            {
                "error": f"Experiment '{experiment}' not found",
                "available_experiments": ", ".join(sorted(known)),
            },
            indent=2,
        )

    try:
        stored = _ports.store().get_download_links(experiment, tool_class, run_ref)
    except Exception as exc:  # noqa: BLE001 - see module docstring / spec:
        # RunNotFoundError/ManifestReadError/ManifestIncompatibleError/
        # CorruptRunLinksError are the expected ResultStore-level cases, but
        # the live create_signed_url/get_object_size calls this makes can
        # raise whatever the active StorageBackend's underlying client
        # raises (a closed list would be structurally incomplete for the
        # Supabase backend) — never a raw traceback to the caller either way.
        # safe_error_text bounds the length and strips anything that looks
        # like a credential/token fragment before it reaches the caller,
        # exactly as list_existing_analyses.py's trim_staleness path already
        # does for its own storage-adjacent failure (PR #611 review finding —
        # an earlier version of this handler returned str(exc) unredacted).
        return json.dumps({"error": safe_error_text(exc)}, indent=2)

    response = {
        "experiment": stored.experiment,
        "tool_class": stored.tool_class,
        "run_ref": stored.run_ref,
        "version_dir": stored.version_dir,
        "outputs": stored.outputs,
        "output_links": {
            name: link.model_dump(mode="json")
            for name, link in stored.output_links.items()
        },
        "manifest_url": stored.manifest_url,
    }
    return json.dumps(response, indent=2)

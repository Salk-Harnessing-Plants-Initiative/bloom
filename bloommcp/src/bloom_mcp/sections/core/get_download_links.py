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
        return json.dumps({"error": str(exc)}, indent=2)

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
    }
    return json.dumps(response, indent=2)

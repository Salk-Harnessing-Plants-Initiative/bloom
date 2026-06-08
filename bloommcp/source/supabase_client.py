"""Single point of Supabase access for bloommcp.

Every read or write that bloommcp performs against Supabase Storage or
PostgREST goes through this module. Keeping the URL / role / bucket /
prefix decisions in one place means future tools cannot accidentally
upload to the wrong bucket, hit Supabase as service_role, or skip the
required input/output prefix.

Public surface (exactly three functions):

    get_postgrest_client()           → supabase.Client authenticated as
                                       bloom_agent. Use for table reads
                                       via PostgREST. Construct fresh per
                                       call; do not cache.

    read_input_csv(name)             → pd.DataFrame loaded from object
                                       `bloommcp_input/{name}` in the
                                       `bloommcp-data` bucket.

For tool outputs, use `AnalysisWriter` instead — it routes through the
versioned `bloommcp_output/<tool_class>_<stem>/v<N>_<date>_<slug>/`
prefix and updates `manifest.json`. The generic storage helpers below
(`upload_file`, `write_json`, etc.) take a fully-qualified `key` and are
called by `AnalysisWriter.commit()`; they are not meant for direct use
by tools.

`name` for `read_input_csv` is always a basename (no slashes). The
helper prepends the input prefix. Passing a key that contains `/` raises
ValueError so a tool cannot accidentally cross prefixes or escape the
bucket.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pandas as pd
import supabase

BUCKET = "bloommcp-data"
INPUT_PREFIX = "bloommcp_input/"
SUPABASE_URL = os.environ.get("SUPABASE_URL")
BLOOM_AGENT_KEY = os.environ.get("BLOOM_AGENT_KEY")
# checks for keys
if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is required for bloommcp.source.supabase_client. "
        "Set it in the bloommcp service env block of docker-compose.prod.yml."
    )
if not BLOOM_AGENT_KEY:
    raise RuntimeError(
        "BLOOM_AGENT_KEY is required for bloommcp.source.supabase_client. "
        "It is the JWT signed with role=bloom_agent that bloommcp uses to "
        "call PostgREST and Supabase Storage. Set it in the bloommcp service "
        "env block of docker-compose.prod.yml (already injected from the "
        "PROD_/STAGING_BLOOM_AGENT_KEY GitHub Actions secret at deploy time)."
    )


def _validate_name(name: str) -> None:
    """Reject keys that would escape the prefix or cross the input/output
    convention. The prefix is added by this module — callers pass a bare
    basename like `accessions.csv`."""
    if not name:
        raise ValueError("name must be a non-empty basename, got empty string")
    if "/" in name:
        raise ValueError(
            f"name must be a basename without slashes; got {name!r}. The "
            f"input/output prefix is added by this helper."
        )


def get_postgrest_client() -> supabase.Client:
    """Return a fresh Supabase client authenticated as bloom_agent.

    PostgREST and Storage access flow through the same client. The
    bloom_agent role's existing `agent_read_*` policies on the public
    schema cover the table reads bloommcp needs; the
    `agent_insert_bloommcp_data` / `agent_update_bloommcp_data` policies
    introduced by 20260605000000 cover storage writes.

    A new client is constructed per call so the JWT does not live as
    module-level state and rotation requires no in-process reload.
    """
    return supabase.create_client(SUPABASE_URL, BLOOM_AGENT_KEY)


def read_input_csv(name: str) -> pd.DataFrame:
    """Load `bloommcp_input/{name}` from the `bloommcp-data` bucket as a
    DataFrame.

    Args:
        name: basename of the CSV (e.g. `accessions.csv`). Must not
            contain a slash.

    Raises:
        ValueError: if `name` is empty or contains a slash.
        Exception: any error surfaced by the Supabase storage download
            (e.g. object not found, network failure, RLS denial). Caller
            decides how to surface those.
    """
    _validate_name(name)
    client = get_postgrest_client()
    payload = client.storage.from_(BUCKET).download(f"{INPUT_PREFIX}{name}")
    return pd.read_csv(io.BytesIO(payload))


# ─── Generic storage helpers ──────────────────────────────────────────────────
#
# These six helpers are the storage primitives AnalysisWriter uses to store
# the versioned-output catalog. They take an object `key` that
# includes any prefix structure (e.g. `bloommcp_output/qc_my_exp/v1_.../_cleaned.csv`)


_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _guess_content_type(path: Path) -> str:
    """Map common extensions to content types."""
    return _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")


def get_storage_client():
    """Return a fresh Supabase storage client with access to `bloommcp-data`.
    """
    return supabase.create_client(SUPABASE_URL, BLOOM_AGENT_KEY).storage.from_(BUCKET)


def list_prefix(prefix: str) -> list[str]:
    """List basenames of objects directly under `prefix` in `bloommcp-data`.
        Lists file inside the folder.
        list_prefix("") is shows at the root"
        list_prefix("bloommcp_output/") shows file under the prefix folder
    """
    client = get_storage_client()
    items = client.list(prefix)
    return [item["name"] for item in items]


def read_json(key: str) -> dict:
    """Download `key` from `bloommcp-data` and parse as JSON.

    Raises a Supabase storage exception if the key does not exist;
    callers that treat absence as a normal state should check with
    `list_prefix()` first (this is what `AnalysisDir.read_manifest`
    does).
    """
    client = get_storage_client()
    payload = client.download(key)
    return json.loads(payload.decode("utf-8"))


def write_json(key: str, payload: dict) -> None:
    """Save `payload` as a JSON file at `key`. Overwrites if it exists."""
    body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    client = get_storage_client()
    client.upload(
        path=key,
        file=body,
        file_options={"content-type": "application/json", "upsert": "true"},
    )


def upload_file(key: str, local_path: Path) -> None:
    """Upload bytes from `local_path` to `key` in `bloommcp-data`.

    Content-type is inferred from the file extension; CSV, JSON, and PNG
    are the cases AnalysisWriter writes today. Unknown extensions fall
    back to `application/octet-stream`. Same upsert semantics as
    `write_json`.
    """
    client = get_storage_client()
    body = local_path.read_bytes()
    client.upload(
        path=key,
        file=body,
        file_options={"content-type": _guess_content_type(local_path), "upsert": "true"},
    )


def download_file(key: str, local_path: Path) -> None:
    """Download `key` from `bloommcp-data` into `local_path`.

    Creates parent directories if needed. Raises on missing key.
    """
    client = get_storage_client()
    payload = client.download(key)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(payload)

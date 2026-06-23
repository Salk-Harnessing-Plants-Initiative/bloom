#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "supabase>=2.0",
#     "torch>=2.0",
#     "tqdm>=4.66",
# ]
# ///

"""Upload protein embeddings from a PyTorch .pt file into bloom's
protein_embeddings_esm2 table on staging or prod.

Auths as a real user via email/password. The returned JWT is attached
to every PostgREST call so postgres sees the user's role from their
JWT claims; the RLS policies on proteins + protein_embeddings_esm2
allow writes ONLY for bloom_admin. If your account does not map to
bloom_admin, the upserts will silently affect 0 rows.

USAGE
-----

Staging:

    # Get ANON_KEY once: it ships in the public frontend bundle.
    # Either grep the homepage HTML, SSH and read .env.staging, or
    # copy from a Supabase API call in browser dev-tools.
    export ANON_KEY="eyJhbGc..."

    BLOOM_URL="https://staging.bloom.salk.edu:8443/api" \\
    BLOOM_PASSWORD='...' \\
      uv run scripts/uploaders/upload_protein_embeddings.py \\
        --email you@salk.edu \\
        --embedding-file ~/Box/bloom-embeddings/arabidopsis_embeddings.pt \\
        --species arabidopsis \\
        --dry-run

Prod: swap BLOOM_URL to https://bloom.salk.edu/api and use the prod ANON_KEY.

Password can be passed inline with --password '...' but reading from
BLOOM_PASSWORD avoids leaking it into shell history / ps output.

INPUT SHAPE (.pt)
-----------------
Two shapes are supported. Both produce a {uid: 1280-float-vector}
mapping after load:

  Shape A:  {uid_str: torch.Tensor(1280)}            # dict-of-tensors
  Shape B:  {"ids": [uid, ...], "embeddings": Tensor(N, 1280)}

If your file has a different shape, edit `load_pt_file()` — that's
the single place to adapt for new formats.

IDEMPOTENCY
-----------
Both upserts target their PK (uid) with `on_conflict="uid"` so re-runs
overwrite cleanly. Safe to re-run after a partial load.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, Iterator

import torch
from supabase import Client, create_client
from tqdm import tqdm

EXPECTED_DIM = 1280
BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# .pt loader
# ---------------------------------------------------------------------------

def load_pt_file(path: Path) -> dict[str, list[float]]:
    """Read a .pt file and return {uid: list[float] of length EXPECTED_DIM}.

    Accepts two common shapes; see module docstring. Raises on any other
    structure rather than guessing.
    """
    obj = torch.load(path, map_location="cpu", weights_only=False)

    if isinstance(obj, dict) and "ids" in obj and "embeddings" in obj:
        ids = obj["ids"]
        embs = obj["embeddings"]
        if len(ids) != len(embs):
            raise ValueError(
                f"shape mismatch: ids={len(ids)} embeddings={len(embs)}"
            )
        out = {str(ids[i]): embs[i].tolist() for i in range(len(ids))}
    elif isinstance(obj, dict):
        # Assume {uid: tensor(1280)}
        out = {str(k): (v.tolist() if hasattr(v, "tolist") else list(v)) for k, v in obj.items()}
    else:
        raise ValueError(
            f"Unsupported .pt shape: {type(obj).__name__}. "
            "Expected dict[uid -> tensor] OR {'ids': [...], 'embeddings': tensor}. "
            "Inspect the file with `torch.load(path)` and adapt load_pt_file()."
        )

    # Validate dim on a single sample — full validation happens at insert via
    # the vector(1280) column type (postgres rejects wrong-dim vectors).
    first_uid, first_vec = next(iter(out.items()))
    if len(first_vec) != EXPECTED_DIM:
        raise ValueError(
            f"vector for {first_uid!r} has dim {len(first_vec)}, expected {EXPECTED_DIM}. "
            "Are these ESM-2 (650M) embeddings?"
        )

    return out


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def preflight_check(embeddings: dict[str, list[float]]) -> None:
    """Validate before upload: no NaN/Inf, and log the norm distribution.

    NaN/Inf in a vector(1280) column would either fail at INSERT (good) or
    silently propagate through cosine distance calculations (bad). We catch
    it here, name the offending UIDs, and fail loudly. The norm stats are
    a quick sanity check: if mean is ~0 or stdev is huge, something's off
    with the inference run.
    """
    import math

    bad: list[str] = []
    norms: list[float] = []
    for uid, vec in embeddings.items():
        sq = 0.0
        for x in vec:
            if math.isnan(x) or math.isinf(x):
                bad.append(uid)
                break
            sq += x * x
        else:
            norms.append(math.sqrt(sq))

    if bad:
        sample = ", ".join(bad[:5])
        more = f" (+ {len(bad) - 5} more)" if len(bad) > 5 else ""
        raise ValueError(
            f"{len(bad)} vector(s) contain NaN/Inf — refusing to upload. "
            f"Offending UIDs: {sample}{more}"
        )

    n = len(norms)
    mean = sum(norms) / n
    var = sum((x - mean) ** 2 for x in norms) / n
    stdev = math.sqrt(var)
    print(
        f"  preflight: {n} vectors clean | "
        f"norms min={min(norms):.3f} max={max(norms):.3f} "
        f"mean={mean:.3f} stdev={stdev:.3f}"
    )


# ---------------------------------------------------------------------------
# pgvector serialization
# ---------------------------------------------------------------------------

def vec_to_pgvector(vec: list[float]) -> str:
    """Format a 1280-dim list as pgvector text literal '[v1,v2,...]'.

    PostgREST stores this as text; postgres implicit-casts to vector(1280)
    at INSERT time because of the column's declared type.
    """
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

def make_authed_client(
    bloom_url: str, anon_key: str, email: str, password: str,
) -> Client:
    """Create a Supabase client and sign in via email/password.

    The session JWT is attached to subsequent PostgREST calls. Writes are
    governed by RLS — the signed-in user must have INSERT permission on
    proteins + protein_embeddings_esm2 (via a writer role + policy, or by
    being granted bloom_admin). Service-role auth is intentionally NOT
    supported by this script; this is a write-user ingest path only.
    """
    client = create_client(bloom_url, anon_key)
    res = client.auth.sign_in_with_password({"email": email, "password": password})
    if not res.session:
        raise RuntimeError("sign-in failed — check email/password")
    return client


def resolve_species_id(client: Client, common_name: str) -> int:
    """Look up species.id by common_name, creating the row if absent.

    The proteins.species_id FK requires a valid species row. We look up
    by common_name first; if no match, insert and return the new id.
    Lookups are case-sensitive — `arabidopsis` ≠ `Arabidopsis`.
    """
    found = (
        client.table("species")
        .select("id")
        .eq("common_name", common_name)
        .limit(1)
        .execute()
    )
    if found.data:
        return int(found.data[0]["id"])

    created = (
        client.table("species")
        .insert({"common_name": common_name})
        .execute()
    )
    if not created.data:
        raise RuntimeError(
            f"failed to create species row for common_name={common_name!r}"
        )
    return int(created.data[0]["id"])


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def batched(iterable: Iterable, n: int) -> Iterator[list]:
    batch: list = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------

def make_uid(species: str, gene_id: str, namespace: bool) -> str:
    """Construct the proteins.uid PK from the species + gene_id.

    Default: '<species>:<gene_id>' (e.g. 'arabidopsis:AT5G16970') so
    same-named genes across species cannot collide. With --no-namespace,
    uid = gene_id directly (caller takes responsibility for global
    uniqueness across all species in the table).
    """
    return f"{species}:{gene_id}" if namespace else gene_id


def upsert_proteins(
    client: Client, species: str, species_id: int, gene_ids: list[str],
    namespace: bool, dry_run: bool,
) -> int:
    """One proteins row per gene_id. uid is namespaced by species; both the
    text species field (back-compat) and the structured species_id FK are
    written so callers can migrate to JOINing public.species at their pace."""
    rows = [
        {
            "uid": make_uid(species, gid, namespace),
            "species": species,
            "species_id": species_id,
            "gene_id": gid,
            "raw_gene_id": gid,
        }
        for gid in gene_ids
    ]
    written = 0
    for batch in tqdm(list(batched(rows, BATCH_SIZE)), desc="proteins"):
        if dry_run:
            continue
        resp = client.table("proteins").upsert(batch, on_conflict="uid").execute()
        written += len(resp.data or [])
    return written


def upsert_embeddings(
    client: Client, species: str, embeddings: dict[str, list[float]],
    namespace: bool, dry_run: bool,
) -> int:
    """One protein_embeddings_esm2 row per gene_id with the 1280-dim vector.
    uid is namespaced by species so the FK to proteins.uid lines up."""
    rows = [
        {
            "uid": make_uid(species, gid, namespace),
            "embedding": vec_to_pgvector(vec),
        }
        for gid, vec in embeddings.items()
    ]
    written = 0
    for batch in tqdm(list(batched(rows, BATCH_SIZE)), desc="embeddings"):
        if dry_run:
            continue
        resp = (
            client.table("protein_embeddings_esm2")
            .upsert(batch, on_conflict="uid")
            .execute()
        )
        written += len(resp.data or [])
    return written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(
        description="Upload protein embeddings (.pt) into bloom's "
                    "protein_embeddings_esm2 via PostgREST.",
    )
    p.add_argument("--email", required=True,
                   help="Login email (account must have INSERT permission on "
                        "proteins + protein_embeddings_esm2 — RLS-governed).")
    p.add_argument("--password",
                   help="Login password. If omitted, read from BLOOM_PASSWORD env.")
    p.add_argument("--embedding-file", required=True, type=Path,
                   help="Path to .pt file with the embeddings")
    p.add_argument("--species", required=True,
                   help="Species label written to proteins.species")
    p.add_argument("--dry-run", action="store_true",
                   help="Load file + authenticate, then exit without writing")
    p.add_argument("--no-namespace", action="store_true",
                   help="Store uid = gene_id directly (default: '<species>:<gene_id>')")
    args = p.parse_args()
    namespace_uids = not args.no_namespace

    bloom_url = os.environ.get("BLOOM_URL")
    anon_key = os.environ.get("ANON_KEY")
    if not bloom_url or not anon_key:
        print("BLOOM_URL and ANON_KEY must be set in the environment.", file=sys.stderr)
        return 2

    password = args.password or os.environ.get("BLOOM_PASSWORD")
    if not password:
        print("Password missing: pass --password or set BLOOM_PASSWORD.", file=sys.stderr)
        return 2

    if not args.embedding_file.exists():
        print(f"embedding file not found: {args.embedding_file}", file=sys.stderr)
        return 2

    print(f"Loading {args.embedding_file} ...")
    embeddings = load_pt_file(args.embedding_file)
    print(f"  -> {len(embeddings)} proteins, dim={len(next(iter(embeddings.values())))}")

    preflight_check(embeddings)

    print(f"Authenticating as {args.email} against {bloom_url} ...")
    client = make_authed_client(bloom_url, anon_key, args.email, password)
    print("  -> authenticated")

    species_id = resolve_species_id(client, args.species)
    print(f"  -> species_id={species_id} for common_name={args.species!r}")

    if args.dry_run:
        sample_gene_ids = list(embeddings.keys())[:5]
        print("DRY RUN — skipping inserts. Sample UIDs that would be written:")
        for gid in sample_gene_ids:
            print(f"  gene_id={gid!r} -> uid={make_uid(args.species, gid, namespace_uids)!r}")
        return 0

    proteins_written = upsert_proteins(
        client, args.species, species_id, list(embeddings.keys()),
        namespace=namespace_uids, dry_run=False,
    )
    print(f"proteins:   {proteins_written} rows written")

    embs_written = upsert_embeddings(
        client, args.species, embeddings,
        namespace=namespace_uids, dry_run=False,
    )
    print(f"embeddings: {embs_written} rows written")

    if embs_written == 0:
        print(
            "WARNING: 0 embedding rows written. Likely your login user does "
            "not satisfy the bloom_admin RLS policy. Verify your account's "
            "postgres role and try again.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

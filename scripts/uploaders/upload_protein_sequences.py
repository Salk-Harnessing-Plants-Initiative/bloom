#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "supabase>=2.0",
#     "tqdm>=4.66",
# ]
# ///

"""Upload protein amino-acid sequences from a FASTA file into bloom's
protein_sequences table on staging or prod.

Companion to upload_protein_embeddings.py. Both build the same namespaced
proteins.uid ('<species>:<gene_id>') so a protein's metadata, embedding, and
sequence line up by uid across the three tables. Run order does not matter —
this script upserts the proteins row too, so it is safe to run before or
after the embeddings uploader.

Auths as a real user via email/password. The returned JWT is attached to
every PostgREST call so postgres sees the user's role from their JWT claims;
the RLS policies on proteins + protein_sequences allow writes for bloom_writer
(or bloom_admin). If your account maps to neither, the upserts will silently
affect 0 rows.

USAGE
-----

Staging:

    export ANON_KEY="eyJhbGc..."

    BLOOM_URL="https://staging.bloom.salk.edu:8443/api" \\
    BLOOM_PASSWORD='...' \\
      uv run scripts/uploaders/upload_protein_sequences.py \\
        --email you@salk.edu \\
        --fasta ~/Box/bloom-embeddings/arabidopsis.faa \\
        --species arabidopsis \\
        --source phytozome \\
        --dry-run

Prod: swap BLOOM_URL to https://bloom.salk.edu/api and use the prod ANON_KEY.

INPUT (.fasta / .faa)
---------------------
Standard FASTA. The gene_id is the first whitespace-delimited token of each
header (after '>'); everything after the first space is ignored. Sequence
lines are concatenated until the next header.

    >AT5G16970.1 some description here
    MASTACVRRL...
    ...

If your headers encode the gene_id differently, edit `load_fasta_file()` —
that's the single place to adapt for new header formats.

ALPHABET
--------
protein_sequences.sequence has a CHECK constraint of '^[A-Za-z*]+$' (amino-acid
letters + stop). Preflight validates the same alphabet and names offending
sequences/characters BEFORE upload, so a gap ('-'), dot, digit, or stray
whitespace fails loudly here instead of as an opaque CHECK violation at INSERT.
Case is preserved as-is (the CHECK accepts both); pass --uppercase to normalise.

IDEMPOTENCY
-----------
Both upserts target their PK (uid) with `on_conflict="uid"` so re-runs overwrite
cleanly. Safe to re-run after a partial load.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable, Iterator

from supabase import Client, create_client
from tqdm import tqdm

BATCH_SIZE = 100
# Mirrors the protein_sequences.sequence CHECK constraint.
VALID_SEQ_RE = re.compile(r"^[A-Za-z*]+$")


# ---------------------------------------------------------------------------
# FASTA loader
# ---------------------------------------------------------------------------

def load_fasta_file(path: Path) -> dict[str, str]:
    """Read a FASTA file and return {gene_id: sequence}.

    gene_id is the first whitespace-delimited token of each '>' header.
    Duplicate gene_ids raise rather than silently keeping the last one.
    """
    sequences: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None

    with path.open() as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            if not line:
                continue
            if line.startswith(">"):
                gene_id = line[1:].split()[0] if len(line) > 1 else ""
                if not gene_id:
                    raise ValueError(f"empty FASTA header at line {lineno}")
                if gene_id in sequences:
                    raise ValueError(
                        f"duplicate gene_id {gene_id!r} at line {lineno} — "
                        "FASTA must have one record per gene_id"
                    )
                sequences[gene_id] = []
                order.append(gene_id)
                current = gene_id
            else:
                if current is None:
                    raise ValueError(
                        f"sequence data before any header at line {lineno}"
                    )
                sequences[current].append(line.strip())

    if not order:
        raise ValueError(f"no FASTA records found in {path}")

    out = {gid: "".join(parts) for gid, parts in sequences.items()}
    empty = [gid for gid, seq in out.items() if not seq]
    if empty:
        sample = ", ".join(empty[:5])
        raise ValueError(f"{len(empty)} record(s) have empty sequence: {sample}")
    return out


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def preflight_check(sequences: dict[str, str]) -> None:
    """Validate the alphabet and log a length distribution before upload.

    Any sequence with characters outside [A-Za-z*] is named (with the offending
    characters) and the run fails — this is the same alphabet the DB CHECK
    enforces, caught here with a clearer message than a CHECK violation.
    """
    bad: list[tuple[str, str]] = []
    lengths: list[int] = []
    for gid, seq in sequences.items():
        if not VALID_SEQ_RE.match(seq):
            offenders = "".join(sorted(set(re.sub(r"[A-Za-z*]", "", seq))))
            bad.append((gid, offenders))
        else:
            lengths.append(len(seq))

    if bad:
        sample = ", ".join(f"{gid} (bad chars: {chars!r})" for gid, chars in bad[:5])
        more = f" (+ {len(bad) - 5} more)" if len(bad) > 5 else ""
        raise ValueError(
            f"{len(bad)} sequence(s) contain characters outside [A-Za-z*] — "
            f"refusing to upload. Offenders: {sample}{more}. "
            "Gaps ('-'), dots, digits, or whitespace are rejected by the CHECK; "
            "clean the FASTA or widen the constraint."
        )

    n = len(lengths)
    mean = sum(lengths) / n
    print(
        f"  preflight: {n} sequences clean | "
        f"length min={min(lengths)} max={max(lengths)} mean={mean:.0f}"
    )


# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

def make_authed_client(
    bloom_url: str, anon_key: str, email: str, password: str,
) -> Client:
    """Create a Supabase client and sign in via email/password.

    The session JWT is attached to subsequent PostgREST calls. Writes are
    governed by RLS — the signed-in user must have INSERT permission on
    proteins + protein_sequences (via bloom_writer, or bloom_admin).
    """
    client = create_client(bloom_url, anon_key)
    res = client.auth.sign_in_with_password({"email": email, "password": password})
    if not res.session:
        raise RuntimeError("sign-in failed — check email/password")
    return client


def resolve_species_id(client: Client, common_name: str) -> int:
    """Look up species.id by common_name, creating the row if absent.

    The proteins.species_id FK requires a valid species row. Lookups are
    case-sensitive — `arabidopsis` ≠ `Arabidopsis`.
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

    created = client.table("species").insert({"common_name": common_name}).execute()
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


def make_uid(species: str, gene_id: str, namespace: bool) -> str:
    """Construct the proteins.uid PK from species + gene_id.

    Default '<species>:<gene_id>' matches upload_protein_embeddings.py so a
    protein's sequence, embedding, and metadata share one uid. --no-namespace
    stores uid = gene_id directly.
    """
    return f"{species}:{gene_id}" if namespace else gene_id


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------

def upsert_proteins(
    client: Client, species: str, species_id: int, gene_ids: list[str],
    namespace: bool, dry_run: bool,
) -> int:
    """One proteins row per gene_id so the protein_sequences FK resolves.
    Mirrors upload_protein_embeddings.py so both ingest paths converge on the
    same proteins row."""
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


def upsert_sequences(
    client: Client, species: str, sequences: dict[str, str], source: str | None,
    namespace: bool, dry_run: bool,
) -> int:
    """One protein_sequences row per gene_id. seq_length and seq_md5 are
    generated columns — postgres computes them, so we only send uid + sequence
    (+ optional source)."""
    rows = [
        {
            "uid": make_uid(species, gid, namespace),
            "sequence": seq,
            "source": source,
        }
        for gid, seq in sequences.items()
    ]
    written = 0
    for batch in tqdm(list(batched(rows, BATCH_SIZE)), desc="sequences"):
        if dry_run:
            continue
        resp = (
            client.table("protein_sequences")
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
        description="Upload protein sequences (FASTA) into bloom's "
                    "protein_sequences via PostgREST.",
    )
    p.add_argument("--email", required=True,
                   help="Login email (account must have INSERT permission on "
                        "proteins + protein_sequences — RLS-governed).")
    p.add_argument("--password",
                   help="Login password. If omitted, read from BLOOM_PASSWORD env.")
    p.add_argument("--fasta", required=True, type=Path,
                   help="Path to FASTA file (.fasta / .faa) with the sequences")
    p.add_argument("--species", required=True,
                   help="Species label written to proteins.species")
    p.add_argument("--source",
                   help="Provenance written to protein_sequences.source "
                        "(e.g. uniprot, phytozome, or the filename)")
    p.add_argument("--uppercase", action="store_true",
                   help="Uppercase sequences before upload (default: preserve case)")
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

    if not args.fasta.exists():
        print(f"FASTA file not found: {args.fasta}", file=sys.stderr)
        return 2

    print(f"Loading {args.fasta} ...")
    sequences = load_fasta_file(args.fasta)
    if args.uppercase:
        sequences = {gid: seq.upper() for gid, seq in sequences.items()}
    print(f"  -> {len(sequences)} sequences")

    preflight_check(sequences)

    print(f"Authenticating as {args.email} against {bloom_url} ...")
    client = make_authed_client(bloom_url, anon_key, args.email, password)
    print("  -> authenticated")

    species_id = resolve_species_id(client, args.species)
    print(f"  -> species_id={species_id} for common_name={args.species!r}")

    if args.dry_run:
        sample_gene_ids = list(sequences.keys())[:5]
        print("DRY RUN — skipping inserts. Sample UIDs that would be written:")
        for gid in sample_gene_ids:
            uid = make_uid(args.species, gid, namespace_uids)
            print(f"  gene_id={gid!r} -> uid={uid!r} (len={len(sequences[gid])})")
        return 0

    proteins_written = upsert_proteins(
        client, args.species, species_id, list(sequences.keys()),
        namespace=namespace_uids, dry_run=False,
    )
    print(f"proteins:  {proteins_written} rows written")

    seqs_written = upsert_sequences(
        client, args.species, sequences, args.source,
        namespace=namespace_uids, dry_run=False,
    )
    print(f"sequences: {seqs_written} rows written")

    if seqs_written == 0:
        print(
            "WARNING: 0 sequence rows written. Likely your login user does not "
            "satisfy the bloom_writer/bloom_admin RLS policy on protein_sequences. "
            "Verify your account's postgres role and try again.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

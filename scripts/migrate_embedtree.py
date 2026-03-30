"""
Migrate ESM-2 protein embeddings into the Supabase proteins table.

Supports two formats:
  - .npy: pickled dict {gene_id: 1280-dim numpy array}
  - .npz: compressed numpy archive with 'embeddings' array + .fasta for gene IDs

The species name is derived from the filename or can be overridden with --species.

Usage:
    # .npy format (original)
    python scripts/migrate_embedtree.py \
        --supabase-url http://localhost:8000 \
        --supabase-key your-service-role-key \
        ~/Downloads/arabidopsis_embeddings.npy

    # .npz format (new) — requires matching .fasta file
    python scripts/migrate_embedtree.py \
        --supabase-url http://localhost:8000 \
        --supabase-key your-service-role-key \
        --species eh23a \
        --fasta ~/Downloads/EH23a.v2.proteins.fasta \
        ~/Downloads/EH23a.v2.proteins_embeddings.npz
"""

import argparse
import os
import sys
import time

import numpy as np
from supabase import create_client


def parse_fasta_ids(fasta_path: str) -> list[str]:
    """Extract sequence IDs from a FASTA file (the part after '>' before first space)."""
    ids = []
    with open(fasta_path) as f:
        for line in f:
            if line.startswith(">"):
                gene_id = line[1:].strip().split()[0]
                ids.append(gene_id)
    return ids


def load_npy(path: str) -> dict[str, np.ndarray]:
    """Load .npy format: pickled dict {gene_id: embedding}."""
    data = np.load(path, allow_pickle=True).item()
    if not isinstance(data, dict):
        print(f"ERROR: expected a dict, got {type(data)}")
        sys.exit(1)
    return data


def load_npz(path: str, fasta_path: str | None = None) -> dict[str, np.ndarray]:
    """Load .npz format: compressed archive with embedding arrays.

    If fasta_path is provided, gene IDs come from fasta headers.
    Otherwise, uses array keys or numeric indices.
    """
    archive = np.load(path)

    # Check what's in the archive
    keys = list(archive.keys())
    print(f"  NPZ keys: {keys[:5]}{'...' if len(keys) > 5 else ''} ({len(keys)} total)")

    if len(keys) == 1:
        # Single array — rows are genes, need fasta for IDs
        embeddings = archive[keys[0]]
        print(f"  Embeddings shape: {embeddings.shape}")

        if fasta_path:
            gene_ids = parse_fasta_ids(fasta_path)
            if len(gene_ids) != embeddings.shape[0]:
                print(f"ERROR: fasta has {len(gene_ids)} sequences but embeddings has {embeddings.shape[0]} rows")
                sys.exit(1)
            return {gid: embeddings[i] for i, gid in enumerate(gene_ids)}
        else:
            # Use numeric indices
            return {str(i): embeddings[i] for i in range(embeddings.shape[0])}
    else:
        # Multiple keys — each key is a gene ID
        return {key: archive[key] for key in keys}


def migrate(file_path: str, supabase_url: str, supabase_key: str,
            species: str | None = None, fasta_path: str | None = None,
            batch_size: int = 1000):
    filename = os.path.basename(file_path)

    # Determine species name
    if species:
        species_name = species
    else:
        species_name = filename.replace("_embeddings.npy", "").replace("_embeddings.npz", "").replace("_embeddings.pt", "")
        # Clean up version strings like "EH23a.v2.proteins" → "eh23a"
        species_name = species_name.split(".")[0].lower()

    # Load data based on format
    print(f"Loading {file_path}...")
    if file_path.endswith(".npz"):
        data = load_npz(file_path, fasta_path)
    else:
        data = load_npy(file_path)

    # Validate embedding dimensions
    first_val = next(iter(data.values()))
    dim = first_val.shape[0] if len(first_val.shape) == 1 else first_val.shape[-1]
    if dim != 1280:
        print(f"ERROR: expected 1280-dim vectors, got {dim}-dim")
        sys.exit(1)

    total = len(data)
    print(f"Species: {species_name}")
    print(f"Genes: {total}")
    print(f"Embedding dim: {dim}")

    # Connect and insert
    client = create_client(supabase_url, supabase_key)
    genes = list(data.items())
    inserted = 0
    start_time = time.time()

    print(f"Inserting in batches of {batch_size}...")

    for i in range(0, total, batch_size):
        batch = genes[i:i + batch_size]
        rows = [
            {
                "uid": f"{species_name}:{gene_id}",
                "species": species_name,
                "gene_id": gene_id,
                "embedding": embedding.tolist(),
            }
            for gene_id, embedding in batch
        ]

        client.table("proteins").insert(rows).execute()
        inserted += len(rows)

        elapsed = time.time() - start_time
        rate = inserted / elapsed if elapsed > 0 else 0
        print(f"  {inserted}/{total} ({inserted * 100 // total}%) - {rate:.0f} rows/sec")

    elapsed = time.time() - start_time
    print(f"\nDone. {inserted} rows inserted in {elapsed:.1f}s ({inserted / elapsed:.0f} rows/sec)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate ESM-2 embeddings into Supabase proteins table")
    parser.add_argument("file", help=".npy or .npz embedding file")
    parser.add_argument("--supabase-url", required=True, help="Supabase project URL")
    parser.add_argument("--supabase-key", required=True, help="Supabase service role key")
    parser.add_argument("--species", help="Override species name (default: derived from filename)")
    parser.add_argument("--fasta", help=".fasta file for gene ID mapping (required for npz with single array)")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows per batch (default: 1000)")
    args = parser.parse_args()

    migrate(args.file, args.supabase_url, args.supabase_key, args.species, args.fasta, args.batch_size)

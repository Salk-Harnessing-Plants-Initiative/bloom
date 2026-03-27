"""
Migrate ESM-2 protein embeddings into the Supabase proteins table.

Each .npy file is a pickled dict: {gene_id: 1280-dim numpy array}.
The species name is derived from the filename (e.g. arabidopsis_embeddings.npy -> arabidopsis).

Usage:
    python scripts/migrate_embedtree.py \
        --supabase-url http://localhost:8000 \
        --supabase-key your-service-role-key \
        ~/Downloads/arabidopsis_embeddings.npy
"""

import argparse
import os
import sys
import time

import numpy as np
from supabase import create_client


def migrate(npy_path: str, supabase_url: str, supabase_key: str, batch_size: int = 1000):
    # Parse species from filename
    filename = os.path.basename(npy_path)
    species = filename.replace("_embeddings.npy", "").replace("_embeddings.pt", "")

    # Load data
    print(f"Loading {npy_path}...")
    data = np.load(npy_path, allow_pickle=True).item()
    if not isinstance(data, dict):
        print(f"ERROR: expected a dict, got {type(data)}")
        sys.exit(1)

    first_val = next(iter(data.values()))
    if first_val.shape != (1280,):
        print(f"ERROR: expected 1280-dim vectors, got {first_val.shape}")
        sys.exit(1)

    total = len(data)
    print(f"Species: {species}")
    print(f"Genes: {total}")

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
                "uid": f"{species}:{gene_id}",
                "species": species,
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
    parser.add_argument("file", help=".npy embedding file (e.g. arabidopsis_embeddings.npy)")
    parser.add_argument("--supabase-url", required=True, help="Supabase project URL")
    parser.add_argument("--supabase-key", required=True, help="Supabase service role key")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows per batch (default: 1000)")
    args = parser.parse_args()

    migrate(args.file, args.supabase_url, args.supabase_key, args.batch_size)

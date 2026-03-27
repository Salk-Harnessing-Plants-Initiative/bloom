"""
Import OrthoFinder orthogroup mappings from searchkeys.tsv into Supabase.

Parses the HPI OrthoBrowser searchkeys.tsv file and inserts gene-to-orthogroup
mappings into the orthogroups table. Normalizes gene IDs to match the format
used in the proteins table.

Species mapping (orthobrowser prefix -> embedding species name):
  - at*g* (araport)     -> arabidopsis
  - tarv.*              -> pennycress
  - sbic.*              -> sorghum
  - gmax.*              -> soybean

Usage:
    python scripts/import_orthogroups.py \
        --supabase-url http://localhost:8000 \
        --supabase-key your-service-role-key \
        ~/Downloads/searchkeys.tsv
"""

import argparse
import json
import re
import sys
import time

from supabase import create_client

# Map orthobrowser gene prefixes to embedding species names
SPECIES_MAP = {
    "arabidopsis": "arabidopsis",
    "tarv": "pennycress",
    "sbic": "sorghum",
    "gmax": "soybean",
}


def normalize_gene_id(raw_gene_id: str, species: str) -> str | None:
    """Normalize orthobrowser gene ID to match embedding gene ID format."""
    if species == "arabidopsis":
        # at5g06290.1.araport11.447 -> AT5G06290
        match = re.match(r"(at\dg\d+)", raw_gene_id, re.IGNORECASE)
        return match.group(1).upper() if match else None

    if species == "pennycress":
        # tarv.1014.hpi3.1.g000010.t1 -> Tarv.1014.HPI3.1.g000010.t1
        parts = raw_gene_id.split(".")
        if len(parts) >= 6 and parts[0].lower() == "tarv":
            return f"Tarv.{parts[1]}.HPI3.{'.'.join(parts[3:])}"
        return None

    if species == "sorghum":
        # Sbic.BT623.HPI3.Chr01.g000010.t1 -> Sbic.BT623.HPI3.Chr01.g000010
        if raw_gene_id.lower().startswith("sbic."):
            parts = raw_gene_id.split(".")
            if len(parts) >= 5:
                return ".".join(parts[:5])
        return None

    if species == "soybean":
        # gmax.w82a5.hpi3.chr01.g000010.t1 -> normalize similarly
        if raw_gene_id.lower().startswith("gmax."):
            parts = raw_gene_id.split(".")
            if len(parts) >= 5:
                return ".".join(parts[:5])
        return None

    return None


def detect_species(raw_gene_id: str) -> str | None:
    """Detect which species a gene belongs to based on its ID pattern."""
    lower = raw_gene_id.lower()
    if re.match(r"at\dg\d+", lower) and "araport" in lower:
        return "arabidopsis"
    if lower.startswith("tarv."):
        return "pennycress"
    if lower.startswith("sbic."):
        return "sorghum"
    if lower.startswith("gmax."):
        return "soybean"
    return None


def import_orthogroups(tsv_path: str, supabase_url: str, supabase_key: str, batch_size: int = 1000):
    client = create_client(supabase_url, supabase_key)

    print(f"Parsing {tsv_path}...")
    rows = []
    skipped = 0
    species_counts: dict[str, int] = {}

    with open(tsv_path) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) < 2:
                continue

            data = json.loads(parts[1])
            if len(data) < 2:
                continue

            orthogroup = data[0]
            raw_gene_id = data[1]

            species = detect_species(raw_gene_id)
            if species is None:
                skipped += 1
                continue

            gene_id = normalize_gene_id(raw_gene_id, species)
            if gene_id is None:
                skipped += 1
                continue

            rows.append({
                "gene_id": gene_id,
                "species": species,
                "orthogroup": orthogroup,
                "raw_gene_id": raw_gene_id,
            })

            species_counts[species] = species_counts.get(species, 0) + 1

    print(f"Parsed {len(rows)} mappings, skipped {skipped} (other species)")
    for sp, count in sorted(species_counts.items()):
        print(f"  {sp}: {count}")

    # Batch insert
    total = len(rows)
    inserted = 0
    start_time = time.time()

    print(f"\nInserting {total} rows in batches of {batch_size}...")

    for i in range(0, total, batch_size):
        batch = rows[i:i + batch_size]
        client.table("orthogroups").insert(batch).execute()
        inserted += len(batch)

        elapsed = time.time() - start_time
        rate = inserted / elapsed if elapsed > 0 else 0
        print(f"  {inserted}/{total} ({inserted * 100 // total}%) - {rate:.0f} rows/sec")

    elapsed = time.time() - start_time
    print(f"\nDone. {inserted} rows inserted in {elapsed:.1f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import OrthoFinder orthogroup mappings into Supabase")
    parser.add_argument("file", help="Path to searchkeys.tsv")
    parser.add_argument("--supabase-url", required=True, help="Supabase project URL")
    parser.add_argument("--supabase-key", required=True, help="Supabase service role key")
    parser.add_argument("--batch-size", type=int, default=1000, help="Rows per batch (default: 1000)")
    args = parser.parse_args()

    import_orthogroups(args.file, args.supabase_url, args.supabase_key, args.batch_size)

#!/usr/bin/env python3
"""
Load test data from CSV files into the development database.
This script loads data in the correct order to respect foreign key constraints.
"""

import os
import sys
import json
import pandas as pd
from supabase import create_client, Client
from pathlib import Path

SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8000")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA")
TEST_DATA_DIR = Path(__file__).parent.parent / "test_data"

# Load tables in order due to foreign key constraints
TABLE_FILES = [
    # Base tables (no foreign keys)
    ("species", "species.csv"),
    ("phenotypers", "phenotypers.csv"),
    ("cyl_scanners", "cyl_scanners.csv"),
    ("people", "people.csv"),
    ("assemblies", "assemblies.csv"),
    ("cyl_trait_sources", "cyl_trait_sources.csv"),
    
    # Tables with foreign keys
    ("cyl_experiments", "cyl_experiments.csv"),
    ("cyl_waves", "cyl_waves.csv"),
    ("accessions", "accessions.csv"),
    ("cyl_plants", "cyl_plants.csv"),
    ("cyl_scientists", "cyl_scientists.csv"),  # Must be before cyl_scans
    ("cyl_camera_settings", "cyl_camera_settings.csv"),
    ("cyl_scans", "cyl_scans.csv"),
    ("cyl_images", "cyl_images.csv"),

    # Skip - Schema issues
    # ("genes", "genes.csv"),
    # ("gene_candidates", "gene_candidates.csv"),
    # ("gene_candidate_scientists", "gene_candidate_scientists.csv"),
    # ("gene_candidate_support", "gene_candidate_support.csv"),
]


def load_csv_to_table(supabase: Client, table_name: str, csv_file: Path, batch_size: int = 100):
    """Load data from CSV file into Supabase table."""
    if not csv_file.exists():
        print(f"[SKIP] {table_name}: {csv_file.name} not found")
        return None
    
    try:
        df = pd.read_csv(csv_file)
        
        records = df.where(pd.notnull(df), None).to_dict('records')
        
        if not records:
            print(f"[SKIP] {table_name}: no data in {csv_file.name}")
            return None
        
        print(f"[LOAD] {table_name} ({len(records)} records from {csv_file.name})...")
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            try:
                response = supabase.table(table_name).upsert(batch).execute()
                
            except Exception as e:
                print(f"[ERROR] Batch {i//batch_size + 1} into {table_name}: {str(e)[:100]}")
                return False
        
        print(f"[SUCCESS] {table_name}: {len(records)} records loaded")
        return True
        
    except Exception as e:
        print(f"[ERROR] Loading {table_name} from {csv_file.name}: {str(e)[:100]}")
        return False


def main():
    print("=" * 60)
    print("BLOOM Test Data Loader")
    print("=" * 60)
    print(f"Supabase URL: {SUPABASE_URL}")
    print(f"Test data directory: {TEST_DATA_DIR}")
    print("=" * 60)
    
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[OK] Connected to Supabase")
    except Exception as e:
        print(f"[FAIL] Failed to connect to Supabase: {e}")
        sys.exit(1)
    
    print()
    
    success_count = 0
    skip_count = 0
    fail_count = 0
    
    for table_name, csv_filename in TABLE_FILES:
        csv_path = TEST_DATA_DIR / csv_filename
        result = load_csv_to_table(supabase, table_name, csv_path)
        
        if result is True:
            success_count += 1
        elif result is False:
            fail_count += 1
        else: 
            skip_count += 1
        
        print()
    
    print("=" * 60)
    print(f"Loaded: {success_count} tables")
    print(f"Skipped: {skip_count} tables")
    print(f"Failed: {fail_count} tables")
    print("=" * 60)
    
    if fail_count > 0:
        print("Some tables failed to load. Check the errors above.")
        sys.exit(1)
    
    print("\nTest data loading complete!")
    sys.exit(0)


if __name__ == "__main__":
    main()

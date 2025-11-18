#!/usr/bin/env python3
"""
Upload sample scan images to MinIO storage.
This uploads the test_data/sample_cyl_scan images to the 'images' bucket in MinIO.
"""

import os
import sys
from pathlib import Path
from supabase import create_client, Client

# Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8000")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA")
TEST_DATA_DIR = Path(__file__).parent.parent / "test_data" / "sample_cyl_scan"
BUCKET_NAME = "images"
STORAGE_PATH_PREFIX = "sample_cyl_scan"


def upload_images(supabase: Client):
    """Upload all PNG images from test_data/sample_cyl_scan to MinIO."""
    if not TEST_DATA_DIR.exists():
        print(f"[ERROR] Test data directory not found: {TEST_DATA_DIR}")
        return False
    
    # Get all PNG files
    image_files = sorted(TEST_DATA_DIR.glob("*.png"))
    
    if not image_files:
        print(f"[SKIP] No PNG files found in {TEST_DATA_DIR}")
        return False
    
    print(f"Found {len(image_files)} images to upload")
    print()
    
    success_count = 0
    fail_count = 0
    
    for image_file in image_files:
        storage_path = f"{STORAGE_PATH_PREFIX}/{image_file.name}"
        
        try:
            # Read image file
            with open(image_file, 'rb') as f:
                file_data = f.read()
            
            # Upload to storage
            print(f"[UPLOAD] {image_file.name} -> {BUCKET_NAME}/{storage_path}")
            
            response = supabase.storage.from_(BUCKET_NAME).upload(
                path=storage_path,
                file=file_data,
                file_options={"content-type": "image/png", "upsert": "true"}
            )
            
            success_count += 1
            print(f"[SUCCESS] {image_file.name}")
            
        except Exception as e:
            fail_count += 1
            print(f"[ERROR] Failed to upload {image_file.name}: {str(e)[:100]}")
    
    print()
    print("=" * 60)
    print(f"Uploaded: {success_count} images")
    print(f"Failed: {fail_count} images")
    print("=" * 60)
    
    return fail_count == 0


def main():
    print("=" * 60)
    print("BLOOM Image Uploader")
    print("=" * 60)
    print(f"Supabase URL: {SUPABASE_URL}")
    print(f"Bucket: {BUCKET_NAME}")
    print(f"Source directory: {TEST_DATA_DIR}")
    print("=" * 60)
    print()
    
    # Create Supabase client
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("[OK] Connected to Supabase")
    except Exception as e:
        print(f"[FAIL] Failed to connect to Supabase: {e}")
        sys.exit(1)
    
    print()
    
    # Upload images
    success = upload_images(supabase)
    
    if success:
        print("\nImage upload complete!")
        sys.exit(0)
    else:
        print("\nImage upload completed with errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()

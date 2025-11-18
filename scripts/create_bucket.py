#!/usr/bin/env python3
"""
Create a new bucket in Supabase Storage.
This ensures the bucket is properly registered in both Supabase and MinIO.
"""

import os
import sys
from supabase import create_client, Client

SUPABASE_URL = os.getenv("SUPABASE_URL", "http://localhost:8000")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiaWF0IjoxNzYwNDA3NTYzLCJleHAiOjIwNzU5ODM1NjN9.MQtGFnfpIKzWTvUIDTH7IUyym8TXDW_kjcWcl-_LNgA")


def create_bucket(bucket_name: str, is_public: bool = False):
    """Create a new bucket via Supabase Storage API."""
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print(f"[CREATE] Creating bucket '{bucket_name}'...")
        
        response = supabase.storage.create_bucket(
            bucket_name,
            options={"public": is_public}
        )
        
        access_type = "PUBLIC" if is_public else "PRIVATE"
        print(f"[SUCCESS] Bucket '{bucket_name}' created as {access_type}")
        return True
        
    except Exception as e:
        error_msg = str(e)
        if "already exists" in error_msg.lower() or "duplicate" in error_msg.lower():
            print(f"[INFO] Bucket '{bucket_name}' already exists")
            return True
        else:
            print(f"[ERROR] Failed to create bucket: {error_msg}")
            return False


def list_buckets():
    """List all buckets in Supabase Storage."""
    try:
        supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("Supabase Storage Buckets:")
        print("-" * 50)
        
        buckets = supabase.storage.list_buckets()
        
        if not buckets:
            print("No buckets found")
            return True
        
        for bucket in buckets:
            bucket_name = bucket.get("name", bucket.get("id", "unknown"))
            is_public = bucket.get("public", False)
            access = "PUBLIC" if is_public else "PRIVATE"
            print(f"  • {bucket_name} ({access})")
        
        print(f"\nTotal: {len(buckets)} bucket(s)")
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to list buckets: {e}")
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python3 create_bucket.py create <bucket-name> [public]")
        print("  python3 create_bucket.py list")
        sys.exit(1)
    
    command = sys.argv[1].lower()
    
    if command == "list":
        success = list_buckets()
        sys.exit(0 if success else 1)
    
    elif command == "create":
        if len(sys.argv) < 3:
            print("[ERROR] Bucket name required")
            print("Usage: python3 create_bucket.py create <bucket-name> [public]")
            sys.exit(1)
        
        bucket_name = sys.argv[2]
        is_public = len(sys.argv) > 3 and sys.argv[3].lower() in ["public", "true", "yes"]
        
        success = create_bucket(bucket_name, is_public)
        sys.exit(0 if success else 1)
    
    else:
        print(f"[ERROR] Unknown command: {command}")
        print("Available commands: create, list")
        sys.exit(1)


if __name__ == "__main__":
    main()

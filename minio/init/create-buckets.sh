#!/bin/bash
set -e

echo " Waiting for MinIO to start..."
sleep 5

mc alias set local http://supabase-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

mc mb -p local/bloom-storage || true

mc mb -p local/bloom-images || true
mc mb -p local/bloom-plates || true

mc anonymous set public local/bloom-storage
mc anonymous set public local/bloom-images

echo " Buckets created and policies applied successfully."


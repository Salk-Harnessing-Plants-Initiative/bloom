#!/bin/bash
set -e

echo "Waiting for MinIO to start..."
sleep 5

echo "Configuring MinIO client..."
mc alias set local "${MINIO_ENDPOINT:-http://supabase-minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

# Bucket creation is idempotent: `mc ls` succeeds when the bucket already
# exists, so the `|| mc mb` branch only runs on a fresh volume. This keeps
# `set -e` happy on restarts (where every bucket already exists) while
# still failing loudly if `mc mb` rejects a NEW name for any reason —
# e.g. an S3-illegal character like the historical underscore.
create_bucket() {
  local name="$1"
  mc ls "local/${name}" >/dev/null 2>&1 || mc mb -p "local/${name}"
}

echo "Creating storage-api backend bucket..."
create_bucket bloom-storage

echo "Creating private buckets..."
create_bucket images
create_bucket species-illustrations
create_bucket tus-files
create_bucket videos
create_bucket scrna

echo "Creating public buckets..."
create_bucket experiment-log-images
create_bucket plates-images
create_bucket plate-blob-storage

echo "Setting public access policies..."
mc anonymous set download local/experiment-log-images
mc anonymous set download local/plates-images
mc anonymous set download local/plate-blob-storage

echo "Buckets created successfully:"
echo "  Private: images, species-illustrations, tus-files, videos, scrna"
echo "  Public: experiment-log-images, plates-images, plate-blob-storage"

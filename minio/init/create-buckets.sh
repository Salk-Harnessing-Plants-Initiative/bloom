#!/bin/bash
set -e

echo "Waiting for MinIO to start..."
sleep 5

echo "Configuring MinIO client..."
mc alias set local "${MINIO_ENDPOINT:-http://supabase-minio:9000}" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

echo "Creating storage-api backend bucket..."
mc mb -p local/bloom-storage

echo "Creating private buckets..."
mc mb -p local/images
mc mb -p local/species-illustrations
mc mb -p local/tus-files
mc mb -p local/videos
mc mb -p local/scrna

echo "Creating public buckets..."
mc mb -p local/experiment-log-images
mc mb -p local/plates-images
mc mb -p local/plate-blob-storage

echo "Setting public access policies..."
mc anonymous set download local/experiment-log-images
mc anonymous set download local/plates-images
mc anonymous set download local/plate-blob-storage

echo "Buckets created successfully:"
echo "  Private: images, species-illustrations, tus-files, videos, scrna"
echo "  Public: experiment-log-images, plates-images, plate-blob-storage"

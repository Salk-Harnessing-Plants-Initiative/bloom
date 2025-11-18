#!/bin/bash
set -e

echo "Waiting for MinIO to start..."
sleep 5

echo "Configuring MinIO client..."
mc alias set local http://supabase-minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

echo "Creating private buckets..."
mc mb -p local/images || true
mc mb -p local/species_illustrations || true
mc mb -p local/tus-files || true
mc mb -p local/video || true
mc mb -p local/scrna || true

echo "Creating public buckets..."
mc mb -p local/experiment-log-images || true
mc mb -p local/plates-images || true
mc mb -p local/plate-blob-storage || true

echo "Setting public access policies..."
mc anonymous set download local/experiment-log-images
mc anonymous set download local/plates-images
mc anonymous set download local/plate-blob-storage

echo "Buckets created successfully:"
echo "  Private: images, species_illustrations, tus-files, video, scrna"
echo "  Public: experiment-log-images, plates-images, plate-blob-storage"


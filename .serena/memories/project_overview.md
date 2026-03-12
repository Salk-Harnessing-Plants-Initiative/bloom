# Bloom Project Overview

## Purpose

Full-stack web application for biological/scientific data visualization and management with video generation capabilities for cylindrical scan data.

## Tech Stack

- **Frontend**: Next.js 16.0.0, React 19.2.0 (overridden to 18.2.0), TypeScript, Material-UI
- **Backend**: Flask (Python 3.11), Supabase (self-hosted)
- **Storage**: MinIO (S3-compatible), Supabase Storage
- **Database**: PostgreSQL (via Supabase)
- **Infrastructure**: Docker Compose, Nginx (prod only)
- **Build**: Turbo (monorepo), pnpm (specified but npm used in practice)

## Project Structure

```
/
├── web/              # Next.js frontend application
├── flask/            # Flask API for video generation and S3 access
├── packages/         # Shared packages (bloom-fs, bloom-js, bloom-nextjs-auth)
├── supabase/         # Supabase configuration and volumes
├── minio/            # MinIO initialization scripts
├── nginx/            # Nginx configuration (production)
├── volumes/          # Docker volume mounts
└── docker-compose.{dev,prod}.yml
```

## Key Services (Development)

- bloom-web: Next.js frontend (port 3000)
- flask-app: Flask API (port 5002)
- kong: API Gateway (port 8000)
- db-dev: PostgreSQL database (port 5432)
- supabase-minio: Object storage (ports 9100-9101)
- studio: Supabase Studio (port 55323)
- auth, rest, realtime, storage: Supabase services

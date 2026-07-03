# Bloom Workflows API

A small FastAPI service intended to host workflow-related HTTP endpoints.

Run locally

```bash
cd services/workflows
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 5100 --reload
```

Or via the dev stack (runs as the `workflows` service):

```bash
docker compose -f docker-compose.dev.yml --env-file .env.dev up -d --build workflows
```

Interactive docs (Swagger) are auto-generated at http://localhost:5100/docs
Behind Caddy it is served under `/workflows/*` (e.g. `<domain>/workflows/health`).

## Endpoints

| Method | Path                        | Purpose                                            |
| ------ | --------------------------- | -------------------------------------------------- |
| GET    | `/health`                 | Health check                                        |
| GET    | `/`                       | Basic test route                                    |
| POST   | `/experiments/{id}/video` | Generate the experiment's scan video, write to S3   |

### Video generation

`POST /experiments/{id}/video` runs the generation inline using the service's own
DB + S3 credentials (no per-user auth for now). It resolves
experiment → scan (via `cyl_scans_extended`) → images (`cyl_images`), downloads
each frame from S3, decimates, encodes H.264 with ffmpeg, uploads the MP4 to
`cyl-videos/{scan_id}.mp4`, and returns a presigned download URL. This mirrors
`services/video-worker`, but runs synchronously from the request instead of a
`pg_notify` job.

```bash
# generate the video for an experiment's first scan (or pass {"scan_id": N})
curl -X POST http://localhost:5100/experiments/123/video \
  -H "Content-Type: application/json" -d '{}'
# → {"experiment_id": 123, "scan_id": 456, "frames": 72, "download_url": "https://.../cyl-videos/456.mp4?..."}
```

> **No auth yet** — the route is currently unauthenticated and runs with a shared
> service identity. Gate it (API key / network-internal) before public exposure.
> Generation is synchronous; large scans can take a while.

## Configuration

| Env var                    | Default                   | Notes                                               |
| -------------------------- | ------------------------- | --------------------------------------------------- |
| `WORKFLOWS_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated browser origins allowed (frontend)  |
| `DATABASE_URL`           | –                        | Postgres connection (reads scan/image metadata)     |
| `S3_ENDPOINT`            | –                        | MinIO/S3 endpoint (reads frames, writes the video)  |
| `S3_BUCKET_NAME`         | `bloom-storage`         | Bucket for source images and output videos          |
| `AWS_ACCESS_KEY_ID`      | –                        | S3 access key                                        |
| `AWS_SECRET_ACCESS_KEY`  | –                        | S3 secret key                                        |
| `AWS_REGION`             | `us-east-1`             | S3 region                                            |

> `ffmpeg` must be present in the runtime image — the Dockerfile installs it.

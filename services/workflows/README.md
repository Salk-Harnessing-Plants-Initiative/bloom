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

| Method | Path                                          | Purpose                                          |
| ------ | --------------------------------------------- | ------------------------------------------------ |
| GET    | `/health`                                   | Health check                                      |
| GET    | `/`                                         | Basic test route                                  |
| POST   | `/experiments/{experiment_id}/scans/{scan_id}/video` | Generate a scan's video, write to S3      |

### Video generation

A video is **per scan** — one cylinder scan has many frames (~72 rotation
images), and that set of frames is one video. An experiment has *many* scans, so
the route takes **both** ids: `scan_id` identifies the video, and `experiment_id`
scopes it (the scan must belong to that experiment, else 404).

`POST /experiments/{experiment_id}/scans/{scan_id}/video` runs inline using the
service's own DB + S3 credentials (no per-user auth for now). It validates the
scan belongs to the experiment (`cyl_scans_extended`), reads the scan's images
(`cyl_images`), downloads each frame from S3, decimates, encodes H.264 with
ffmpeg, uploads the MP4 to `cyl-videos/{scan_id}.mp4`, and returns a presigned
download URL. Mirrors `services/video-worker`, but synchronously.

```bash
# Request: experiment 123, scan 456
curl -X POST http://localhost:5100/experiments/123/scans/456/video

# Response:
# {"experiment_id": 123, "scan_id": 456, "frames": 72, "download_url": "https://.../cyl-videos/456.mp4?..."}
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

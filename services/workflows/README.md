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

`POST /experiments/{experiment_id}/scans/{scan_id}/video` logs in as a dedicated
**least-privilege Supabase app user** (see below), then validates the scan
belongs to the experiment (`cyl_scans_extended`), reads the scan's images
(`cyl_images`, capped at 72), downloads each frame from the images bucket,
decimates, encodes H.264 with ffmpeg, uploads the MP4 to the videos bucket,
returns a signed download URL, and (if configured) inserts a record row.
Synchronous — mirrors `services/video-worker` but runs in the request.

```bash
# Request: experiment 123, scan 456
curl -X POST http://localhost:5100/experiments/123/scans/456/video

# Response:
# {"experiment_id": 123, "scan_id": 456, "frames": 72, "download_url": "https://.../456.mp4?..."}
```

### Auth / access model

The service holds **no privileged credentials** — it signs into Supabase as a
dedicated app user (`WORKFLOWS_SUPABASE_EMAIL` / `_PASSWORD`) and does all DB +
Storage work through that user's JWT, so **its grants and storage policies are
the security boundary**. Set that user up in Supabase with only:

- `SELECT` on `cyl_scans_extended`, `cyl_images`
- read on the images bucket, write on the videos bucket
- (optional) `INSERT` on the record table — **not** `video_jobs` (its trigger
  would re-run the async worker); use a separate plain table

## Configuration

| Env var                       | Default                   | Notes                                              |
| ----------------------------- | ------------------------- | -------------------------------------------------- |
| `WORKFLOWS_CORS_ORIGINS`    | `http://localhost:3000` | Comma-separated browser origins allowed (frontend)   |
| `SUPABASE_URL`              | –                        | Supabase gateway URL                                 |
| `SUPABASE_ANON_KEY`         | –                        | Supabase anon key (for the app-user login)           |
| `WORKFLOWS_SUPABASE_EMAIL`  | –                        | App user's email (least-privilege identity)          |
| `WORKFLOWS_SUPABASE_PASSWORD` | –                      | App user's password                                  |
| `WORKFLOWS_IMAGES_BUCKET`   | `images`                | Storage bucket to read frames from                   |
| `WORKFLOWS_VIDEOS_BUCKET`   | `videos`                | Storage bucket to write the MP4 to                   |
| `WORKFLOWS_VIDEO_TABLE`     | – (unset = skip)         | Plain table to record generated videos (not `video_jobs`) |

> `ffmpeg` must be present in the runtime image — the Dockerfile installs it.

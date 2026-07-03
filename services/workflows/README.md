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

| Method | Path                                          | Auth | Purpose                                   |
| ------ | --------------------------------------------- | ---- | ----------------------------------------- |
| GET    | `/health`                                   | none (internal-only) | Liveness — kept for the in-container probe; **not** exposed via the public proxy |
| POST   | `/experiments/{experiment_id}/scans/{scan_id}/video` | Supabase user JWT | Generate a scan's video, upload to Storage |

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
# Request: experiment 123, scan 456 — requires the caller's Supabase user JWT
curl -X POST http://localhost:5100/experiments/123/scans/456/video \
  -H "Authorization: Bearer <supabase-user-jwt>" \
  -H "apikey: <anon-key>"

# Response:
# {"experiment_id": 123, "scan_id": 456, "frames": 72, "path": "cyl-videos/456.mp4", "download_url": "https://.../456.mp4?..."}
```

### Auth model — two independent layers

**Layer 1 — caller auth (who may call):** application routes require the
caller's **Supabase user JWT** (`Authorization: Bearer`). The service validates
it by delegating to Supabase (`GET /auth/v1/user`), so it **never needs
`JWT_SECRET`**. Requests are rate-limited per user (`429` when exceeded).
`/health` is internal-only and not publicly exposed.

**Layer 2 — service identity (what the server may touch):** the service holds
**no privileged credential** — it signs into Supabase as a dedicated app user
(`WORKFLOWS_SUPABASE_EMAIL` / `_PASSWORD`) whose Postgres `role` is
`bloom_workflows`, so **its grants and storage policies are the boundary**. The
app user needs only:

- `SELECT` on `cyl_scans_extended`, `cyl_images`
- read on the images bucket, write on the videos bucket
- column-level `INSERT(scan_id, path)` / `UPDATE(path)` on `cyl_scan_videos`

All of the above is set up by the migration `…_create_workflows_role.sql`.

## Provisioning (per environment)

1. Create the Supabase auth user (Studio → Authentication, or the Auth Admin API) with an email + password.
2. Give it the scoped role: `UPDATE auth.users SET role = 'bloom_workflows' WHERE email = '…';` (so its login token is scoped to the migration's grants, not broad `authenticated`).
3. Set the deploy secrets `PROD_/STAGING_WORKFLOWS_SUPABASE_EMAIL` and `_PASSWORD`.

## Configuration

| Env var                          | Default                   | Notes                                              |
| -------------------------------- | ------------------------- | -------------------------------------------------- |
| `WORKFLOWS_CORS_ORIGINS`       | `http://localhost:3000` | Comma-separated browser origins allowed (frontend)   |
| `SUPABASE_URL`                 | –                        | Supabase gateway URL (login + caller-JWT validation) |
| `SUPABASE_ANON_KEY`            | –                        | Supabase anon key                                    |
| `WORKFLOWS_SUPABASE_EMAIL`     | –                        | App user's email (least-privilege identity)          |
| `WORKFLOWS_SUPABASE_PASSWORD`  | –                        | App user's password                                  |
| `WORKFLOWS_IMAGES_BUCKET`      | `images`                | Storage bucket to read frames from                   |
| `WORKFLOWS_VIDEOS_BUCKET`      | `videos`                | Storage bucket to write the MP4 to                   |
| `WORKFLOWS_VIDEO_TABLE`        | `cyl_scan_videos`       | Record table (`scan_id -> path`)                     |
| `WORKFLOWS_RATE_LIMIT`         | `5`                     | Max video requests per user per window (429 over)    |
| `WORKFLOWS_RATE_WINDOW_SECONDS`| `60`                    | Rate-limit window                                    |

> `ffmpeg` must be present in the runtime image — the Dockerfile installs it.
> Caller auth is delegated to Supabase (`/auth/v1/user`), so `JWT_SECRET` is **not** needed by this service.

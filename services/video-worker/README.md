# Video Generation Worker

A lightweight Python service that listens for PostgreSQL notifications and generates videos from cylindrical scan images.

## Architecture

```
Frontend                    PostgreSQL                 Video Worker
   │                            │                            │
   ├─ POST /rest/v1/video_jobs ►│                            │
   │  (via PostgREST)           │                            │
   │                            ├─ pg_notify ───────────────►│
   │                            │                            ├─ Process frames
   │◄─ Realtime subscription ───┤◄─ UPDATE progress ─────────┤
   │                            │                            │
   │◄─ Realtime subscription ───┤◄─ UPDATE complete ─────────┤
```

## Setup

### 1. Install dependencies

```bash
cd services/video-worker
pip install -r requirements.txt
```

### 2. Install FFmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg

# CentOS/RHEL
sudo yum install ffmpeg
```

### 3. Configure environment

```bash
export DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
export S3_ENDPOINT=http://localhost:9100
export S3_BUCKET_NAME=bloom-storage
export AWS_ACCESS_KEY_ID=supabase
export AWS_SECRET_ACCESS_KEY=supabase123
export AWS_REGION=us-east-1
```

### 4. Run the worker

```bash
python video_listener.py
```

## Production Deployment (systemd)

### 1. Copy service file

```bash
sudo cp video-worker.service /etc/systemd/system/
```

### 2. Edit the service file

Update paths and environment variables in `/etc/systemd/system/video-worker.service`:
- `WorkingDirectory` - path to this folder
- `ExecStart` - path to Python interpreter
- `DATABASE_URL` - your PostgreSQL connection string
- `S3_*` / `AWS_*` - your MinIO/S3 credentials

### 3. Enable and start

```bash
sudo systemctl daemon-reload
sudo systemctl enable video-worker
sudo systemctl start video-worker
```

### 4. Check status

```bash
sudo systemctl status video-worker
sudo journalctl -u video-worker -f  # Follow logs
```

## Usage

### Request video generation (via PostgREST)

```bash
curl "http://localhost:8000/rest/v1/video_jobs" \
  -H "apikey: YOUR_ANON_KEY" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=representation" \
  -d '{"scan_id": 123}'
```

Response:
```json
{
  "id": 1,
  "scan_id": 123,
  "status": "pending",
  "progress": 0,
  "created_at": "2024-01-26T10:00:00Z"
}
```

### Check job status

```bash
curl "http://localhost:8000/rest/v1/video_jobs?id=eq.1" \
  -H "apikey: YOUR_ANON_KEY"
```

### Subscribe to updates (Supabase Realtime)

```javascript
const channel = supabase
  .channel('video_jobs')
  .on('postgres_changes', {
    event: 'UPDATE',
    schema: 'public',
    table: 'video_jobs',
    filter: 'id=eq.1'
  }, (payload) => {
    console.log('Job update:', payload.new);
    // payload.new.progress, payload.new.status, payload.new.download_url
  })
  .subscribe();
```

## Job Statuses

| Status | Description |
|--------|-------------|
| `pending` | Job queued, waiting for worker |
| `processing` | Worker is generating video |
| `complete` | Video ready, `download_url` populated |
| `failed` | Error occurred, check `error_message` |

## Troubleshooting

### Worker not receiving notifications

1. Check the worker is running: `systemctl status video-worker`
2. Check PostgreSQL connection in logs
3. Verify the trigger exists: `SELECT * FROM pg_trigger WHERE tgname = 'trigger_video_job_notify';`

### Video generation fails

1. Check FFmpeg is installed: `ffmpeg -version`
2. Check S3/MinIO connectivity
3. Check worker logs: `journalctl -u video-worker -f`

### Jobs stuck in "pending"

The worker processes pending jobs on startup. Restart the worker:
```bash
sudo systemctl restart video-worker
```

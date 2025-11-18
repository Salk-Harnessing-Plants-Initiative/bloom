# Bloom Architecture & API Documentation

**Last Updated:** November 17, 2025

---

## Table of Contents

1. [Subdomain Architecture](#subdomain-architecture)
2. [API Endpoints](#api-endpoints)
3. [Configuration](#configuration)
4. [Development Guide](#development-guide)

---

## Subdomain Architecture

Bloom uses a multi-subdomain architecture where each service runs on its own subdomain for clean separation and easier CORS management.

### Service Subdomains

| Service | URL | Description |
|---------|-----|-------------|
| **🌐 Bloom Web** | `http://localhost` | Next.js web application - Main user interface |
| **⚡ Flask API** | `http://flask.localhost` | Flask backend - Video generation, presigned URLs, and fast API routes |
| **⛁ Supabase Studio** | `http://studio.localhost` | Database management dashboard - Tables, SQL editor, authentication |
| **🗄️ MinIO Console** | `http://minio.localhost` | Object storage management - File uploads, bucket management |

### Internal Network

Services communicate internally via Docker network:
- Flask → Kong: `http://kong:8000`
- Flask → MinIO: `http://supabase-minio:9000`
- Web → Kong: Internal Docker network

---


## API Endpoints

### Base URL

All Flask API endpoints are accessible at: `http://flask.localhost`

Interactive documentation available at: `http://flask.localhost/docs`

---

### 1. Documentation Page
**Endpoint:** `GET /`

**Description:** Serves the interactive API documentation page. This is the main landing page for the Flask API with comprehensive documentation of all available endpoints.

**Response:** Returns an HTML documentation page with all available endpoints, their parameters, and examples.

**Example:**
```bash
curl http://flask.localhost/
```

---

### 2. Documentation Page (Alternative)
**Endpoint:** `GET /docs`

**Description:** Alternative endpoint that serves the same API documentation page as `/`. Useful for explicit documentation access.

**Response:** Returns the same HTML documentation page.

**Example:**
```bash
curl http://flask.localhost/docs
```

---

### 3. Test Endpoint
**Endpoint:** `GET /test`

**Description:** Test endpoint that serves the documentation page. Can be used to verify the Flask API is running and accessible.

**Response:** Returns the HTML documentation page.

**Example:**
```bash
curl http://flask.localhost/test
```

---

### 4. Supabase Connection Test
**Endpoint:** `GET /supabaseconnection`

**Description:** Test Supabase database connectivity by fetching scanner data from the `cyl_scanners` table (limited to 5 records).

**Response:**
```json
[
  {
    "id": 1,
    "name": "FastScanner"
  },
  {
    "id": 2,
    "name": "SlowScanner"
  },
  {
    "id": 3,
    "name": "Unknown"
  },
  {
    "id": 4,
    "name": "PBIOBScanner"
  }
]
```

**Example:**
```bash
curl http://flask.localhost/supabaseconnection
```

---

### 5. List S3 Buckets
**Endpoint:** `GET /list_buckets`

**Description:** Test S3/MinIO connectivity by listing all available buckets.

**Response:**
```json
{
  "buckets": [
    "bloom-storage",
    "other-bucket"
  ]
}
```

**Example:**
```bash
curl http://flask.localhost/list_buckets
```
---

### 6. Generate Video
**Endpoint:** `POST /generate_video`

**Description:** Generate a video from images associated with a cylinder scan. Downloads images from S3, creates MP4 video, and uploads back to S3.

⚠️ **Long-running operation:** Can take several minutes depending on image count. Timeout set to 300 seconds.

**Request Body:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `scan_id` | integer | Required | ID of the cylinder scan from `cyl_scans` table |

**Request Example:**
```json
{
  "scan_id": 123
}
```

**Success Response:**
```json
{
  "message": "Video generated successfully",
  "scan_id": 123,
  "total_frames": 450,
  "download_url": "https://supabase-minio:9000/bloom-storage/cyl-videos/123.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=supabase%2F20251117%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20251117T174500Z&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Signature=abc123..."
}
```

**Error Response:**
```json
{
  "error": "Scan not found"
}
```

**Implementation Details:**
- Images are fetched from `storage-single-tenant/images/`
- Images are decimated by factor of 4 (every 4th pixel) to reduce size
- Video is saved to `cyl-videos/{scan_id}.mp4` in S3
- Presigned URL expires in 1 hour (3600 seconds)

**Example:**
```bash
curl -X POST http://flask.localhost/generate_video \
  -H "Content-Type: application/json" \
  -d '{"scan_id": 123}'
```

---

### 7. Get Presigned URLs
**Endpoint:** `POST /get_presigned_urls`

**Description:** Generate presigned URLs for multiple S3 objects. Requires JWT authentication.

**Authentication:** Required

Requires Bearer token in Authorization header. Token must be a valid JWT signed with `JWT_SECRET` and have audience "authenticated".

```
Authorization: Bearer YOUR_JWT_TOKEN
```

**Request Body:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `object_paths` | array[string] | Required | Array of S3 object paths (prefixes) |

**Request Example:**
```json
{
  "object_paths": [
    "images/scan1/image001.jpg",
    "images/scan1/image002.jpg",
    "images/scan2/missing.jpg"
  ]
}
```

**Success Response:**
```json
{
  "presigned_urls": [
    "https://supabase-minio:9000/bloom-storage/images/scan_123/img_001.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=...",
    "https://supabase-minio:9000/bloom-storage/images/scan_123/img_002.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=...",
    ""
  ],
  "invalid_urls": [false, false, true],
  "skipped_paths": ["images/scan_456/missing_image.jpg"]
}
```

**Response Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `presigned_urls` | array[string] | Generated presigned URLs (empty string if failed) |
| `invalid_urls` | array[boolean] | Flags indicating which URLs failed to generate |
| `skipped_paths` | array[string] | Object paths that couldn't be found or processed |

**Error Responses:**

Missing or invalid Authorization header:
```json
{
  "message": "Unauthorized--header missing or invalid"
}
```

JWT decoding failed:
```json
{
  "message": "Unauthorized--JWT decoding failed"
}
```

**Example:**
```bash
curl -X POST http://flask.localhost/get_presigned_urls \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -d '{
    "object_paths": [
      "images/scan1/image001.jpg",
      "images/scan1/image002.jpg"
    ]
  }'
```
---




### Accessing Services

| Service | Local URL | Credentials |
|---------|-----------|-------------|
| **Web App** | http://localhost | - |
| **Flask API** | http://flask.localhost | - |
| **Studio** | http://studio.localhost | Supabase credentials |
| **MinIO Console** | http://minio.localhost | 

### Docker Container Names

| Service | Container Name |
|---------|----------------|
| Flask API | `flask-app` |
| Nginx | `bloom-nginx` |
| Kong Gateway | `supabase-kong` |
| Supabase Studio | `studio` |
| MinIO | `supabase-minio` |
| Postgres | `db-prod` |

#### START UP GUIDES
- Always use `make prod-up` or include `--env-file .env.prod`
- Verify `.env.prod` exists and contains all required variables
- Rebuild services after changing environment variables

### Testing Endpoints
**Health Check:**
```bash
curl http://flask.localhost/
```

**Test CORS:**
```bash
curl -v -H "Origin: http://studio.localhost" http://flask.localhost/
```

**Test Supabase Connection:**
```bash
curl http://flask.localhost/supabaseconnection
```

**Test MinIO Connection:**
```bash
curl http://flask.localhost/list_buckets
```

---

## File Structure

```
bloom-v2/
├── flask/
│   ├── app.py                 # Main Flask application
│   ├── config.py              # Supabase & MinIO configuration
│   ├── videoWriter.py         # Video generation logic
│   ├── requirements.txt       # Python dependencies
│   ├── Dockerfile             # Flask container definition
│   └── static/
│       ├── docs.html          # Interactive API documentation
│       └── logo_small.png     # Bloom logo
├── nginx/
│   └── nginx.conf.template    # Nginx configuration with subdomains
├── docker-compose.prod.yml    # Production services orchestration
├── .env.prod                  # Production environment variables
├── Makefile                   # Convenience commands
├── README.md                  # Setup & installation guide
└── DOCUMENTATION.md           # This file
```

---

## Development Guide
### Starting Services
**Production Mode:**
```bash
# Start all services
make prod-up

# Stop all services
make prod-down

# View logs
make prod-logs
```
**Development Mode:**
```bash
# Start all services
make dev-up

# Stop all services
make dev-down

# View logs
make logs
```

## Additional Resources

- **Interactive Docs:** Visit `PROD_SETUP.html` & `WEB_SETUP.html` for web-based documentation
---

**Project:** Bloom  
**Repository:** https://github.com/Salk-Harnessing-Plants-Initiative/bloom    
**Last Updated:** November 17, 2025

# 🏭 Bloom Production Setup Guide

This guide covers deploying Bloom in production mode with optimized builds and proper security configurations.

---

## 📋 Prerequisites

Before starting, ensure you have:

- Docker and Docker Compose installed
- Python 3.8 or higher
- Root or sudo access
- Domain names configured (optional)


---

## 💾 STEP 1: MinIO Storage Setup

### Create Storage Directory

```bash
# Create directory
sudo mkdir -p /var/lib/bloom/minio

# Set ownership
sudo chown -R $USER:$USER /var/lib/bloom/minio

# Set permissions
chmod 755 /var/lib/bloom/minio
```

---

## ⚙️ STEP 2: Environment Configuration

### Step 1: Get Environment Template

Get the production environment template from:
**[Notion: Environment Configuration](https://www.notion.so/Plan-2734a67a766780e89373c1e1ec687a4d)**

Copy the `.env.prod` template content and create the file:

```bash
# Create .env.prod file
nano .env.prod
# Or use your preferred editor
```

### Step 2: Generate Secure Keys

Generate new JWT keys and secrets:

```bash
# Generate random strings for keys
openssl rand -base64 32  # For JWT_SECRET
openssl rand -base64 32  # For VAULT_ENC_KEY
openssl rand -hex 32     # For SUPAVISOR_ENC_KEY
```

### Step 3: Configure Critical Variables

Edit `.env.prod` and update these values:

**Domain Configuration:**
```bash
DOMAIN_MAIN=yourdomain.com
DOMAIN_STUDIO=studio.yourdomain.com
DOMAIN_MINIO=minio.yourdomain.com
DOMAIN_FLASK=flask.yourdomain.com
```

**Database Security:**
```bash
POSTGRES_PASSWORD=CHANGE_TO_SECURE_PASSWORD
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=CHANGE_TO_SECURE_PASSWORD
```

**MinIO Security:**
```bash
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=CHANGE_TO_SECURE_PASSWORD
MINIO_DATA_PATH=/var/lib/bloom/minio
```

**JWT Keys** (use generated values):
```bash
JWT_SECRET=your-generated-secret
ANON_KEY=your-generated-anon-key
SERVICE_ROLE_KEY=your-generated-service-role-key
VAULT_ENC_KEY=your-generated-vault-key
SUPAVISOR_ENC_KEY=your-generated-supavisor-key
```

### Step 4: Set Permissions

```bash
chmod 600 .env.prod
```

---

## 🚀 STEP 3: Starting Production Stack

### Start Services

```bash
make prod-up
```

This will:
- Build optimized Next.js production bundle
- Start all services in detached mode
- Initialize MinIO buckets

### Verify Services Running

```bash
docker ps
```

You should see: `db-prod`, `supabase-minio`, `supabase-storage`, `supabase-kong`, `supabase-auth`, `bloom-web-prod`, `flask-app`

### Check Logs

```bash
make prod-logs
```

---

## 🗄️ STEP 4: Database Migrations

### Verify Database Ready

```bash
docker exec db-prod pg_isready -U supabase_admin
```


### Apply Migrations

 `apply-migrations`

You should see tables like: `species`, `phenotypers`, `cyl_experiments`, `cyl_scans`, `cyl_images`, etc.

---

## 📊 STEP 5: Loading Initial Data

### Load Test Data

```bash
# Load CSV data into database
make load-test-data

# Upload test images to MinIO
make upload-images
```

### Load Production Data (Alternative)

Replace test data with your actual production data:

1. Place CSV files in `test_data/` directory
2. Run: `make load-test-data`
3. Upload production images to MinIO buckets

---

## 🌐 STEP 6: Service URLs and Ports

### Internal Services (Docker Network)

| Service | Internal URL | Port | Description |
|---------|-------------|------|-------------|
| PostgreSQL | db-prod:5432 | 5432 | Database |
| Kong Gateway | kong:8000 | 8000 | API Gateway |
| MinIO API | supabase-minio:9000 | 9000 | S3 API |
| Storage API | storage:5000 | 5000 | Supabase Storage |
| Auth API | auth:9999 | 9999 | Authentication |
| REST API | rest:3000 | 3000 | PostgREST |
| Flask API | flask-app:5002 | 5002 | Video Generation |

### External Access (via Nginx)

Production services are accessed through Nginx reverse proxy:

| Service | URL | Description |
|---------|-----|-------------|
| Main App | http://yourdomain.com | Frontend application |
| API | http://yourdomain.com/api | Supabase services |
| Studio | http://studio.yourdomain.com | Database management |
| MinIO | http://minio.yourdomain.com | Storage management |
| Flask | http://flask.yourdomain.com | Video generation API |








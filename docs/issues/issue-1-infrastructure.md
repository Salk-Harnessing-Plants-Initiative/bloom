# Issue 1: Infrastructure - Local Storage & RunAI Mount

**EPIC**: Automated Root Trait Extraction Pipeline Integration
**Priority**: P0 (Blocking)
**Dependencies**: None
**Blocks**: Issues #2, #3, #4, #5, #6

---

## Summary

Migrate Bloom from AWS to a local server with storage mounted to the RunAI GPU cluster, enabling direct data access for Argo pipeline workflows.

## Background

Bloom currently uses AWS S3 for image storage. To run pipelines on the RunAI GPU cluster, data must be accessible via hostPath volumes. Moving to local storage eliminates the need for data transfer between cloud and cluster, significantly improving pipeline performance and simplifying orchestration.

## Goals

1. Set up local database (Supabase) and object storage (MinIO)
2. Configure storage accessible to both Bloom web app and RunAI cluster
3. Create Argo service account with appropriate permissions
4. Migrate existing data from AWS
5. Verify end-to-end data access from Argo workflows

## Technical Design

### Storage Architecture

```
Local Server (e.g., 10.x.x.x)
├── Supabase (Docker)
│   ├── PostgreSQL (port 5432)
│   ├── PostgREST API (port 3000)
│   ├── GoTrue Auth (port 9999)
│   └── Storage API (port 5000)
│
├── MinIO (Docker)
│   ├── S3 API (port 9000)
│   ├── Console (port 9001)
│   └── Bucket: bloom-uploads
│
└── /hpi/hpi_dev/bloom/  (NFS/CIFS mounted to RunAI)
    ├── experiments/
    │   └── {experiment_id}/
    │       ├── images/
    │       │   └── {scan_id}/
    │       │       └── frame_{n}.png
    │       ├── pipeline_outputs/
    │       │   └── {pipeline_run_id}/
    │       │       ├── models_downloader_output/
    │       │       ├── predictions/
    │       │       └── traits/
    │       └── metadata.json
    │
    └── models/
        ├── primary/
        ├── lateral/
        └── crown/
```

### Data Flow

```
1. Image Upload (Bloom Web/Desktop)
   User uploads → MinIO (S3 API) → Sync to /hpi/hpi_dev/bloom/

2. Pipeline Execution (Argo)
   Argo pod mounts /hpi/hpi_dev/bloom/ via hostPath
   Reads images, writes outputs directly to filesystem

3. Results Access (Bloom)
   Bloom reads results from /hpi/hpi_dev/bloom/
   Ingests traits into Supabase
   Generates Box backup link
```

### MinIO to Filesystem Sync Options

**Option A: MinIO Gateway Mode (Recommended)**
MinIO can operate as a gateway to a local filesystem, making uploads appear directly in `/hpi/hpi_dev/bloom/`.

```bash
# Start MinIO with filesystem backend
minio server /hpi/hpi_dev/bloom/minio-data --console-address ":9001"
```

**Option B: Scheduled Rsync**
Periodically sync MinIO bucket to filesystem location.

```bash
# Cron job every 5 minutes
*/5 * * * * mc mirror minio/bloom-uploads /hpi/hpi_dev/bloom/experiments/
```

**Option C: MinIO Bucket Notifications**
Use MinIO webhooks to trigger sync on upload events.

### Argo Service Account Setup

```yaml
# bloom-pipeline-rbac.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: bloom-pipeline
  namespace: runai-tye-lab
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: bloom-pipeline-role
  namespace: runai-tye-lab
rules:
# Workflow management
- apiGroups: ["argoproj.io"]
  resources: ["workflows"]
  verbs: ["create", "get", "list", "watch", "delete", "patch"]
- apiGroups: ["argoproj.io"]
  resources: ["workflowtemplates"]
  verbs: ["get", "list"]
# Required for task completion reporting
- apiGroups: ["argoproj.io"]
  resources: ["workflowtaskresults"]
  verbs: ["create"]
# Pod access for logs
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: bloom-pipeline-binding
  namespace: runai-tye-lab
subjects:
- kind: ServiceAccount
  name: bloom-pipeline
  namespace: runai-tye-lab
roleRef:
  kind: Role
  name: bloom-pipeline-role
  apiGroup: rbac.authorization.k8s.io
```

**Apply and get token:**
```bash
# Apply RBAC
kubectl apply -f bloom-pipeline-rbac.yaml

# Create long-lived token (1 year)
kubectl create token bloom-pipeline -n runai-tye-lab --duration=8760h

# Store token securely in Bloom backend config
```

### Supabase Configuration

```yaml
# docker-compose.yml (Supabase self-hosted)
version: "3.8"
services:
  postgres:
    image: supabase/postgres:15.1.0.117
    ports:
      - "5432:5432"
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - ./volumes/db/data:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d

  rest:
    image: postgrest/postgrest:v11.2.0
    ports:
      - "3000:3000"
    environment:
      PGRST_DB_URI: postgres://postgres:${POSTGRES_PASSWORD}@postgres:5432/postgres
      PGRST_DB_SCHEMA: public
      PGRST_DB_ANON_ROLE: anon
      PGRST_JWT_SECRET: ${JWT_SECRET}

  storage:
    image: supabase/storage-api:v0.40.4
    ports:
      - "5000:5000"
    environment:
      STORAGE_BACKEND: s3
      STORAGE_S3_BUCKET: bloom-uploads
      STORAGE_S3_ENDPOINT: http://minio:9000
      STORAGE_S3_REGION: us-east-1
      AWS_ACCESS_KEY_ID: ${MINIO_ACCESS_KEY}
      AWS_SECRET_ACCESS_KEY: ${MINIO_SECRET_KEY}

  minio:
    image: minio/minio:latest
    ports:
      - "9000:9000"
      - "9001:9001"
    command: server /data --console-address ":9001"
    volumes:
      - /hpi/hpi_dev/bloom/minio-data:/data
    environment:
      MINIO_ROOT_USER: ${MINIO_ACCESS_KEY}
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
```

### Environment Configuration

```bash
# bloom-backend/.env
# Database
DATABASE_URL=postgres://postgres:password@localhost:5432/postgres
SUPABASE_URL=http://localhost:3000
SUPABASE_ANON_KEY=<your-anon-key>
SUPABASE_SERVICE_KEY=<your-service-key>

# Storage
MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=<your-access-key>
MINIO_SECRET_KEY=<your-secret-key>
BLOOM_STORAGE_PATH=/hpi/hpi_dev/bloom

# Argo
ARGO_SERVER=gpu-master:8888
ARGO_NAMESPACE=runai-tye-lab
ARGO_TOKEN=Bearer <your-token>
ARGO_HTTP1=true
ARGO_SECURE=false
```

---

## Tasks

### Phase 1: Local Infrastructure Setup

- [ ] **1.1** Provision local server with sufficient storage
  - Minimum: 10TB for experiments + models
  - Network: Accessible from Salk network and RunAI cluster

- [ ] **1.2** Install Docker and Docker Compose on local server

- [ ] **1.3** Deploy Supabase stack (Postgres, PostgREST, Auth)
  - Use self-hosted Supabase Docker setup
  - Configure for production (SSL, backups)

- [ ] **1.4** Deploy MinIO with filesystem backend
  - Mount `/hpi/hpi_dev/bloom/minio-data` as data directory
  - Configure bucket policies for Bloom access

- [ ] **1.5** Verify storage mount on RunAI cluster
  ```bash
  # Test from a RunAI pod
  kubectl run test-mount --rm -it --image=alpine \
    --overrides='{"spec":{"containers":[{"name":"test","image":"alpine","volumeMounts":[{"name":"bloom","mountPath":"/bloom"}]}],"volumes":[{"name":"bloom","hostPath":{"path":"/hpi/hpi_dev/bloom","type":"Directory"}}]}}' \
    -n runai-tye-lab -- ls -la /bloom
  ```

### Phase 2: Argo Integration

- [ ] **2.1** Create Argo service account and RBAC
  ```bash
  kubectl apply -f bloom-pipeline-rbac.yaml
  ```

- [ ] **2.2** Generate and securely store Argo token
  ```bash
  kubectl create token bloom-pipeline -n runai-tye-lab --duration=8760h
  ```

- [ ] **2.3** Verify Argo CLI access with new token
  ```bash
  export ARGO_TOKEN="Bearer <token>"
  argo list -n runai-tye-lab
  ```

- [ ] **2.4** Update WorkflowTemplates for parameterized paths
  - Modify `sleap-roots-pipeline.yaml` to accept `input-path` and `output-path` parameters
  - Test with sample data

### Phase 3: Data Migration

- [ ] **3.1** Export existing data from AWS S3
  ```bash
  aws s3 sync s3://bloom-bucket /hpi/hpi_dev/bloom/migration/
  ```

- [ ] **3.2** Transform data to new directory structure
  - Map existing paths to new `experiments/{id}/images/` structure
  - Generate metadata.json files

- [ ] **3.3** Export Supabase data from production
  ```bash
  pg_dump -h <aws-host> -U postgres bloom > bloom_backup.sql
  ```

- [ ] **3.4** Import data to local Supabase
  ```bash
  psql -h localhost -U postgres -d postgres < bloom_backup.sql
  ```

- [ ] **3.5** Update image paths in database to new storage locations

- [ ] **3.6** Verify data integrity
  - Spot check 10 random experiments
  - Verify image counts match database records

### Phase 4: Bloom Backend Updates

- [ ] **4.1** Update Bloom backend configuration for local services
  - Database connection string
  - Storage endpoints
  - Argo credentials

- [ ] **4.2** Test image upload flow
  - Upload via web UI → appears in MinIO → accessible at `/hpi/hpi_dev/bloom/`

- [ ] **4.3** Test image retrieval
  - Bloom web can display images from new storage

- [ ] **4.4** Update bloom CLI for new storage
  ```bash
  bloom cyl download ./output --experiment_id <id> -p local
  ```

---

## Acceptance Criteria

- [ ] Supabase accessible at local server IP with valid SSL
- [ ] MinIO accessible via S3 API, uploads appear in filesystem
- [ ] `/hpi/hpi_dev/bloom/` mounted and accessible from RunAI pods
- [ ] Argo service account can create/list/delete workflows
- [ ] Existing experiments accessible in Bloom after migration
- [ ] New image uploads flow through entire system
- [ ] Zero data loss during migration (verified by checksums)

---

## Rollback Plan

1. Keep AWS infrastructure running during migration (read-only)
2. Maintain database backup before migration
3. If issues arise, revert Bloom config to AWS endpoints
4. Data remains in both locations until verified

---

## Security Considerations

- [ ] Supabase JWT secrets rotated from defaults
- [ ] MinIO credentials stored in secure secret manager
- [ ] Argo token has minimal required permissions
- [ ] Network policies restrict access to authorized services
- [ ] SSL/TLS for all service endpoints

---

## Estimated Effort

| Task | Estimate |
|------|----------|
| Server provisioning | 1 day |
| Supabase/MinIO setup | 2 days |
| Argo RBAC setup | 0.5 days |
| Data migration | 2-3 days |
| Backend updates | 2 days |
| Testing & verification | 2 days |
| **Total** | **~10 days** |

---

## Labels

`infrastructure`, `migration`, `P0`, `blocking`

## Assignees

- Infrastructure: TBD
- Backend: TBD
- DevOps: TBD

# Scripts Directory

Utility scripts for setting up, managing, and populating the Bloom application.

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `setup-env.sh` | Create environment files with secure credentials | **First step** - before starting Docker |
| `dev_init.ts` | Initialize database with test data | After Docker is running |
| `upload_scrna.py` | Upload single-cell RNA-seq datasets | When adding scRNA data |
| `generate_KEYS` | Generate Supabase JWT keys | When rotating API keys |
| `generateJWT_key.py` | Generate user JWT tokens | For testing authenticated API requests |

---

## Setup & Configuration

### `setup-env.sh` 🔧
**Automated environment configuration with secure random credentials**

```bash
# Development environment
./scripts/setup-env.sh dev

# Production environment
./scripts/setup-env.sh prod
```

**What it does:**
- Creates `.env.dev` or `.env.prod` from `.env.dev.example`
- Auto-generates secure random values using OpenSSL:
  - JWT secrets (48+ characters)
  - Database passwords
  - Encryption keys (Supavisor, Vault, Secret Base)
  - MinIO and dashboard passwords
- Provides clear instructions for completing manual configuration

**When to use:**
- **Before** starting Docker for the first time
- When rotating credentials for security
- When setting up a new environment

**Output:** Creates `.env.dev` or `.env.prod` ready for Docker Compose

---

### `generate_KEYS` 🔑
**Node.js commands to generate Supabase JWT API keys**

Contains one-liners for creating:
- `ANON_KEY` - Public anonymous access key for frontend
- `SERVICE_ROLE_KEY` - Admin/backend service key

**Usage:**
```bash
# 1. Replace YOUR-JWT-SECRET with the value from your .env.dev file

# 2. Generate ANON_KEY
node -e "const jwt=require('jsonwebtoken'); console.log(jwt.sign({ role:'anon', iss:'supabase', aud:'authenticated' }, 'YOUR-JWT-SECRET', { algorithm:'HS256', expiresIn:'10y' }))"

# 3. Generate SERVICE_ROLE_KEY
node -e "const jwt=require('jsonwebtoken'); console.log(jwt.sign({ role:'service_role', iss:'supabase' }, 'YOUR-JWT-SECRET', { algorithm:'HS256', expiresIn:'10y' }))"

# 4. Add these keys to your .env.dev file
```

**When to use:**
- After running `setup-env.sh` (it will prompt you)
- When rotating JWT secrets (must regenerate all keys)
- Both keys must use the **same** `JWT_SECRET`

**Prerequisites:** Node.js with `jsonwebtoken` package (`npm install -g jsonwebtoken`)

---

### `generateJWT_key.py` 🎫
**Generate user authentication tokens for testing**

```bash
# Using default JWT secret
python scripts/generateJWT_key.py

# Using custom secret
export BLOOM_JWT_SECRET="your-jwt-secret"
python scripts/generateJWT_key.py
```

**Output:** JWT token valid for 1 hour
```
Payload: { sub: "test-user", aud: "authenticated", iat: ..., exp: ... }
```

**When to use:**
- Testing authenticated API endpoints with curl/Postman
- Debugging authentication/authorization issues
- Manual API testing

**Example:**
```bash
TOKEN=$(python scripts/generateJWT_key.py)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/rest/v1/species
```

---

## Database Operations

### `dev_init.ts` 📊
**Initialize development database with test users and sample data**

```bash
# From project root (Docker must be running)
npx ts-node scripts/dev_init.ts
```

**What it does:**
1. **Creates test users** in Supabase Auth:
   - `testuser5@salk.edu` / password: `testuser5`
   - `testuser6@salk.edu` / password: `testuser6`
2. **Saves credentials** to `~/.bloom/credentials.dev{1,2}.txt`
3. **Loads CSV data** into database tables (in order):
   - Species, assemblies, genes
   - Cylinder experiments, waves, scans, images
   - Accessions, plants, phenotypers
   - Gene candidates and trait sources
4. **(Optional)** Uploads sample images to MinIO storage

**When to use:**
- After running `make dev-up` for the first time
- After database reset/rebuild
- When you need fresh test data

**Prerequisites:**
- Docker stack running (`make dev-up`)
- CSV files in `test_data/` directory
- `test_data/sample_cyl_scan/` images (optional)

**Data loaded from:**
- `test_data/species.csv`
- `test_data/people.csv`
- `test_data/cyl_experiments.csv`
- etc. (see `table_load_order` in script)

---

### `upload_scrna.py` 🧬
**Bulk upload single-cell RNA-seq datasets**

```bash
python scripts/upload_scrna.py <dataset_dir> <dataset_name> <species_name>
```

**Example:**
```bash
python scripts/upload_scrna.py \
  ./data/arabidopsis_root \
  arab_root_v1 \
  "Arabidopsis thaliana"
```

**What it does:**
1. Creates dataset entry in `scrna_datasets` table
2. Bulk inserts genes from gene IDs file
3. Bulk inserts cell metadata and embeddings
4. Uploads gene expression counts to MinIO as JSON (8 parallel workers)
5. Links uploaded files in `scrna_counts` table

**Required files** in `<dataset_dir>`:
```
<dataset_name>.counts.mtx        # Gene × Cell expression matrix (Matrix Market)
<dataset_name>.gene_ids.txt      # Gene names (one per line)
<dataset_name>.embeddings.tsv    # Cell metadata: barcode, x, y, cluster_id
```

**Configuration:**
- `n_workers = 8` - Parallel upload threads (adjust for your system)
- Hardcoded dev credentials (localhost only)

**When to use:**
- Adding new scRNA-seq datasets to the platform
- Populating data for single-cell visualization features

**Prerequisites:**
- Database running with scRNA schema tables
- MinIO bucket `scrna` exists
- Python packages: `pandas`, `scipy`, `sqlalchemy`, `supabase`

---

## Typical Workflows

### 🚀 First Time Setup
```bash
# 1. Create environment file with secure credentials
./scripts/setup-env.sh dev

# 2. Generate Supabase API keys (follow prompts from step 1)
#    Use commands from generate_KEYS file

# 3. Edit .env.dev to add the generated ANON_KEY and SERVICE_ROLE_KEY

# 4. Start Docker stack
make dev-up

# 5. Initialize database with test users and sample data
npx ts-node scripts/dev_init.ts
```

### 🧬 Adding scRNA Dataset
```bash
# 1. Prepare required files in a directory
ls my_dataset/
# my_dataset.counts.mtx
# my_dataset.gene_ids.txt
# my_dataset.embeddings.tsv

# 2. Upload to database (Docker must be running)
python scripts/upload_scrna.py \
  ./my_dataset \
  my_dataset \
  "Species name"

# 3. Verify upload
psql -h localhost -U postgres -d postgres \
  -c "SELECT * FROM scrna_datasets WHERE name='my_dataset';"
```

### 🔄 Rotating Credentials
```bash
# 1. Generate new environment file
./scripts/setup-env.sh dev
#    This creates NEW random passwords and secrets

# 2. Generate new Supabase keys with NEW JWT_SECRET
#    (use generate_KEYS commands)

# 3. Restart Docker with new credentials
make dev-down
make dev-up

# 4. Re-initialize database if needed
npx ts-node scripts/dev_init.ts
```

### 🧪 Testing API Endpoints
```bash
# 1. Generate authentication token
TOKEN=$(python scripts/generateJWT_key.py)

# 2. Make authenticated request
curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/rest/v1/species

# 3. Test Flask video generation endpoint
curl -X POST http://localhost:5002/generate_video \
     -H "Content-Type: application/json" \
     -d '{"scan_id": 1}'
```

---

## Security Notes

### ⚠️ Development-Only Scripts
These scripts contain **hardcoded credentials** for local development:

- **`dev_init.ts`** (line 17): Hardcoded `SERVICE_ROLE_KEY`
- **`upload_scrna.py`** (lines 14-16): Hardcoded Supabase URL and key

**Safe for local dev, NEVER use in production!**

### 🔒 Production-Safe Scripts
- **`setup-env.sh`**: Generates unique random credentials
- **`generate_KEYS`**: Template for creating production keys

### 🛡️ Best Practices
- Always use `setup-env.sh` to create `.env.prod` with fresh credentials
- Rotate production credentials regularly
- Never commit `.env.dev` or `.env.prod` files (they're gitignored)
- Use different credentials for dev and production

---

## Troubleshooting

### TypeScript Errors
```bash
# Install TypeScript and ts-node globally
npm install -g typescript ts-node @types/node

# Or use npx (no global install needed)
npx ts-node scripts/dev_init.ts
```

### Database Connection Failed
```bash
# Check Docker containers are running
docker ps | grep -E "db-dev|postgres"

# Test PostgreSQL connection
psql -h localhost -U postgres -p 5432 -c "SELECT 1"

# Check environment variables
cat .env.dev | grep POSTGRES
```

### Python Module Not Found
```bash
# Install required packages
pip install pandas scipy sqlalchemy supabase psycopg2-binary

# Or use requirements file (if available)
pip install -r requirements.txt
```

### Permission Denied (Shell Scripts)
```bash
# Make scripts executable
chmod +x scripts/*.sh

# Or run with bash explicitly
bash scripts/setup-env.sh dev
```

### Node.js jsonwebtoken Not Found
```bash
# Install globally
npm install -g jsonwebtoken

# Or install in project
npm install jsonwebtoken
```

---

## Script Execution Order

For a fresh setup, run scripts in this order:

1. **`setup-env.sh`** → Creates config files
2. **`generate_KEYS`** → Get Supabase API keys
3. **`make dev-up`** → Start Docker services
4. **`dev_init.ts`** → Populate test data
5. **`upload_scrna.py`** → Add scRNA datasets (optional)
6. **`generateJWT_key.py`** → Generate tokens for testing (as needed)

---

## Contributing

When adding new scripts to this directory:

1. Add execute permissions: `chmod +x scripts/your-script.sh`
2. Update this README with clear usage instructions
3. Include error handling and helpful output messages
4. Document any hardcoded values (especially credentials)
5. Add examples of typical usage

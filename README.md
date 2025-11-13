# bloom

Packages and infrastructure for bloom web application.

# Overview

This repository contains:

- **web/** – Next.js frontend
- **supabase/** – self-hosted Supabase stack
- **minio/** – S3-compatible storage
- **flaskapp/** - Contains Flask routes for video generation and S3 content access.
- **docker-compose.dev.yml / docker-compose.prod.yml** – environment definitions
- **Makefile** – helper commands to run the full stack

## Prerequisites

### 1. Environment Setup

**IMPORTANT:** This project requires environment files with sensitive credentials. **NEVER commit these files to git!**

Run the setup script to create your environment file:

```bash
./scripts/setup-env.sh dev
```

This script will:

- Create `.env.dev` from the example template
- Generate secure random passwords and encryption keys
- Provide instructions for completing the setup

After running the script, edit `.env.dev` to add your Supabase API keys (see instructions in terminal output).

For production:

```bash
./scripts/setup-env.sh prod
```

### 2. Docker & Docker Compose

Ensure Docker Desktop (macOS) or Docker Engine (Linux) is installed and running.

### 3. MinIO Storage (Optional)

The default configuration uses a local Docker volume. To use a custom path:

1. Edit `.env.dev` and set `MINIO_DATA_PATH=/your/custom/path`
2. Ensure the directory exists and is writable

## Starting the Full Stack

### Development

```bash
make dev-up
```

Access the application at http://localhost:3000

### Production

```bash
make prod-up
```

### Management Commands

**Stop containers:**

```bash
make dev-down   # Stop development stack
make prod-down  # Stop production stack
```

**View logs:**

```bash
make logs
```

**Rebuild from scratch:**

```bash
make rebuild-dev-fresh   # Development
make rebuild-prod-fresh  # Production
```

## Load test files into the database

Use the `dev_init.ts` script to populate the database with test files.

```bash
# Load using .env.dev
NODE_ENV=development ts-node scripts/dev_init.ts

# Load using .env.prod
NODE_ENV=production ts-node scripts/dev_init.ts
```

# bloom
Packages and infrastructure for bloom web application.

## 📚 Documentation

- **[TROUBLESHOOTING.md](./TROUBLESHOOTING.md)** - Complete guide for debugging authentication, nginx routing, and SSR issues
- **[.env.example](./.env.example)** - Environment variable template with explanations
- **[Production Deployment Checklist](./TROUBLESHOOTING.md#production-deployment-checklist)** - Step-by-step guide for deploying to production

# Overview 
This repository contains:
- **web/** – Next.js frontend
- **supabase/** – self-hosted Supabase stack
- **minio/** – S3-compatible storage
- **flaskapp/** - Contains Flask routes for video generation and S3 content access.
- **docker-compose.dev.yml / docker-compose.prod.yml** – environment definitions
- **Makefile** – helper commands to run the full stack

## Prerequisites
###  1: Root-level environment files (for Docker)
These are used when running the full stack with Docker:
- `.env.dev` → for local development (used by `make dev-up`)
- `.env.prod` → for production / deployment (used by `make prod-up`)

###  2: Web app environment file (for running frontend locally)
- `.env.dev` → for local development  
- `.env.prod` → for production / deployment  

### 3: Setting up a folder for minio service to access
This project uses MinIO for object storage. To ensure the service runs correctly, you need to create a folder on your host machine for MinIO to store data.

#### Create folder
sudo mkdir -p /data/minio
#### Give full access to the folder for Docker containers can read/write to this folder
sudo chmod 777 /data/minio 

## Starting the Full Stack in Development
make dev-up

## Starting the Full Stack in Production
make dev-up

### To stop all containers:
make dev-down
make prod-down

### To follow logs:
make dev-logs
make prod-logs

### To rebuild everything from scratch:
make rebuild-dev-fresh
make rebuild-prod-fresh

## Load test files into the database

Use the `dev_init.ts` script to populate the database with test files.

```bash
# Load using .env.dev
NODE_ENV=development ts-node scripts/dev_init.ts

# Load using .env.prod
NODE_ENV=production ts-node scripts/dev_init.ts




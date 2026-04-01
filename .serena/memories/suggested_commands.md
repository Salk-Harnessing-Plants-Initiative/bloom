# Suggested Commands for Bloom Project

## Development Commands

```bash
# Start development stack
make dev-up

# Stop development stack
make dev-down

# Rebuild without cache
make rebuild-dev-fresh

# View logs
make logs
```

## Production Commands

```bash
# Start production stack (detached)
make prod-up

# Stop production stack
make prod-down

# Rebuild production without cache
make rebuild-prod-fresh
```

## Frontend (from /web directory)

```bash
# Development server
npm run dev

# Build for production
npm run build

# Start production server
npm run start
```

## Docker Commands

```bash
# Start dev stack
docker compose -f docker-compose.dev.yml --env-file .env.dev up --build

# Start prod stack
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# View service logs
docker compose -f docker-compose.dev.yml logs -f [service-name]

# Stop and remove volumes
docker compose -f docker-compose.dev.yml down -v --remove-orphans
```

## System-specific Notes (macOS Darwin)

- Standard Unix commands available: ls, cd, grep, find, cat, etc.
- Docker Desktop required for container operations
- MinIO data directory needs to be created with proper permissions:
  ```bash
  sudo mkdir -p /data/minio
  sudo chmod 777 /data/minio
  ```

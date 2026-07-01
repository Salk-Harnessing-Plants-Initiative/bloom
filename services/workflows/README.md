# Bloom Workflows API

A small FastAPI service intended to host workflow-related HTTP endpoints.

This is an early baseline: it exposes only basic test routes today. Login and
data-access endpoints will be added later. Supabase credentials (gateway URL,
keys, JWT secret, DB URL) are already provided to the container via environment
variables so those endpoints can use them when built.

## Run locally

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

| Method | Path      | Purpose          |
|--------|-----------|------------------|
| GET    | `/health` | Health check     |
| GET    | `/`       | Basic test route |

## Configuration

| Env var                     | Default                  | Notes                                             |
|-----------------------------|--------------------------|---------------------------------------------------|
| `WORKFLOWS_CORS_ORIGINS`    | `http://localhost:3000`  | Comma-separated browser origins allowed (frontend)|
| `SUPABASE_URL`              | –                        | Supabase gateway URL (for future data access)     |
| `SUPABASE_ANON_KEY`         | –                        | Supabase anon key (for future data access)        |
| `SUPABASE_SERVICE_KEY`      | –                        | Supabase service role key (for future data access)|
| `JWT_SECRET`                | –                        | Supabase JWT secret (for future login/auth)       |
| `DATABASE_URL`              | –                        | Postgres connection string (for future data access)|

> No authentication yet — do not expose this publicly as-is.

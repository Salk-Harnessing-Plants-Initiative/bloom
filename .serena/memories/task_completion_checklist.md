# Task Completion Checklist

## For Code Changes

### Frontend (TypeScript/Next.js)

- [ ] Run type checking: `cd web && npx tsc --noEmit`
- [ ] Test dev build: `cd web && npm run dev`
- [ ] Test production build: `cd web && npm run build`
- [ ] Check for console errors in browser

### Backend (Flask/Python)

- [ ] Check Python syntax: `python -m py_compile flask/*.py`
- [ ] Test Flask app starts: `cd flask && python app.py`
- [ ] Verify API endpoints respond correctly

### Docker/Infrastructure

- [ ] Build containers: `docker compose -f docker-compose.dev.yml build`
- [ ] Test stack starts: `make dev-up`
- [ ] Check all services healthy: `docker compose -f docker-compose.dev.yml ps`
- [ ] Verify inter-service communication

### Environment Configuration

- [ ] Verify all required env vars are set
- [ ] Check .env.example is up to date
- [ ] Ensure no secrets in committed files
- [ ] Validate environment-specific configs

## For This Project Specifically

### Before Deployment

- [ ] Replace hardcoded paths in docker-compose files
- [ ] Move secrets to proper secret management
- [ ] Resolve React version conflicts
- [ ] Fix Flask Dockerfile to copy all required files
- [ ] Add missing services to production compose
- [ ] Standardize on package manager (pnpm vs npm)

### Testing Strategy (Currently Missing)

- No test framework configured
- Should add: Jest for frontend, pytest for Flask
- Should add: Integration tests for Docker stack

## Current State

- No automated linting or formatting configured
- No CI/CD pipeline detected
- Manual testing required for all changes

# Code Style and Conventions

## TypeScript/Next.js
- **TypeScript Version**: 5.1.6+
- **Target**: ES6 (consider upgrading)
- **Strict Mode**: Enabled
- **Module System**: ESNext with Node resolution
- **JSX**: react-jsx
- **Path Aliases**: `@/*` maps to root
- **Naming**: Not explicitly documented in configs

## Python/Flask
- **Version**: Python 3.11
- **Style**: Not explicitly enforced (no linting config found)
- **Conventions observed**:
  - Snake_case for variables and functions
  - Type hints used in config.py
  - Class names in PascalCase (VideoWriter)

## File Organization
- Monorepo structure with workspaces
- Local packages in `/packages` directory
- Separate Dockerfiles for dev and prod environments
- Environment-specific docker-compose files

## Dependencies Management
- **Specified**: pnpm@10.19.0 (packageManager field)
- **In Practice**: npm used in Dockerfiles and Makefile
- **Inconsistency**: Should standardize on one package manager

## Docker Conventions
- Multi-stage builds for production
- Development uses volume mounts for hot reload
- Environment variables passed via .env files
- Service naming: {service}-{env} pattern

## Currently Missing
- ESLint configuration
- Prettier configuration
- Python linting (black, flake8, mypy)
- Pre-commit hooks
- Testing frameworks setup

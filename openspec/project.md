# Project Context

## Purpose

Bloom is a full-stack web application for biological/scientific data visualization and management, specifically designed for handling cylindrical scan data. The application provides capabilities for storing, managing, and visualizing scientific imaging data, with specialized video generation functionality for cylindrical scan sequences.

## Tech Stack

### Frontend

- **Framework**: Next.js 16.0.0 with React 18.2.0 (overridden from 19.2.0)
- **Language**: TypeScript 5.9.3 (ES6 target, strict mode enabled)
- **UI Library**: Material-UI
- **Build Tool**: Turbo (monorepo)
- **Package Manager**: pnpm 10.19.0 (specified), though npm is used in practice

### Backend

- **API Framework**: Flask (Python 3.11)
- **Database**: PostgreSQL (via Supabase)
- **Auth/Backend Services**: Supabase (self-hosted)
  - Authentication
  - REST API
  - Realtime subscriptions
  - Storage
  - Studio UI

### Storage & Infrastructure

- **Object Storage**: MinIO (S3-compatible) on port 9100-9101
- **API Gateway**: Kong (port 8000)
- **Reverse Proxy**: Nginx (production only)
- **Containerization**: Docker Compose with separate dev/prod configurations
- **Volume Management**: Persistent volumes for Supabase and MinIO data

### Key Ports (Development)

- Web: 3000
- Flask API: 5002
- Kong Gateway: 8000
- PostgreSQL: 5432
- MinIO: 9100-9101
- Supabase Studio: 55323

## Project Conventions

### Code Style

#### TypeScript/JavaScript

- **Strict mode**: Enabled
- **Module system**: ESNext with Node resolution
- **JSX**: react-jsx transform
- **Path aliases**: `@/*` maps to project root
- **Naming**: Follow standard TypeScript conventions
  - PascalCase for components and classes
  - camelCase for functions and variables
  - UPPER_SNAKE_CASE for constants

#### Python/Flask

- **Version**: Python 3.11
- **Naming**:
  - snake_case for functions and variables
  - PascalCase for classes (e.g., VideoWriter)
  - Type hints encouraged (as seen in config.py)
- **Note**: No formal linting configuration currently enforced

#### Missing Tooling

- ESLint configuration (should be added)
- Prettier configuration (should be added)
- Python linting tools (black, flake8, mypy recommended)
- Pre-commit hooks (should be configured)

### Architecture Patterns

#### Monorepo Structure

```
/
├── web/              # Next.js frontend application
├── flask/            # Flask API for video generation and S3 access
├── packages/         # Shared packages
│   ├── bloom-fs/     # File system utilities
│   ├── bloom-js/     # Shared JavaScript utilities
│   └── bloom-nextjs-auth/ # Authentication helpers
├── supabase/         # Supabase configuration and volumes
├── minio/            # MinIO initialization scripts
├── nginx/            # Nginx configuration (production)
├── scripts/          # Utility scripts (dev_init.ts, setup-env.sh)
├── test_data/        # Test data for development
└── volumes/          # Docker volume mounts
```

#### Service Architecture

- **Microservices approach**: Frontend, Flask API, and Supabase services run as separate containers
- **API Gateway pattern**: Kong sits in front of backend services
- **Environment separation**: Distinct docker-compose files for dev and prod
- **Volume persistence**: Data persists across container restarts

#### Key Design Patterns

- **Multi-stage Docker builds** for production optimization
- **Volume mounts** in development for hot reload
- **Environment-based configuration** via .env files
- **Service naming convention**: `{service}-{env}` pattern

### Testing Strategy

**Current State**: No automated testing framework configured

**Recommended Setup**:

- Frontend: Jest + React Testing Library
- Backend: pytest for Flask API
- E2E: Playwright or Cypress
- Test data: Use `dev_init.ts` script to populate test database

### Git Workflow

#### Branching Strategy

- **Main branch**: `main` (default and production branch)
- **Development**: Work directly on feature branches
- **Commit style**: Descriptive, imperative mood
  - Examples from history: "Update README to include test data loading instructions", "add routes for video generation"

#### Environment Management

- `.env.dev` for local development
- `.env.prod` for production deployment
- Separate environment files for web app and Docker stack

## Domain Context

### Scientific Imaging Domain

- **Primary data type**: Cylindrical scan images
- **Data workflow**:
  1. Scan data stored in PostgreSQL (`cyl_scanners`, `cyl_images` tables)
  2. Image files stored in MinIO (S3-compatible storage)
  3. Flask API generates videos from image sequences
  4. Frontend provides visualization and management interface

### Key Database Tables

- `cyl_scanners`: Scanner metadata
- `cyl_images`: Individual scan images with S3 references

### Video Generation

- Custom `VideoWriter` class for creating videos from scan sequences
- Decimation factor: 4 (reduces frame count for performance)
- Processes images stored in S3 bucket
- Authenticated via JWT

## Important Constraints

### Technical Constraints

- **React version locked**: Overridden to 18.2.0 (compatibility requirement)
- **Python version**: Must use Python 3.11 for Flask app
- **Storage requirement**: MinIO needs `/data/minio` directory with 777 permissions
- **Package manager inconsistency**: Specified pnpm but npm used in Docker/Makefile

### Infrastructure Constraints

- **Self-hosted Supabase**: Requires full stack deployment, not using Supabase cloud
- **MinIO configuration**: Requires proper initialization and bucket setup
- **Volume persistence**: Critical data stored in Docker volumes

### Development Constraints

- **Environment files required**: Must have `.env.dev` and `.env.prod` configured
- **Test data**: Use `dev_init.ts` script with appropriate NODE_ENV
- **Port availability**: Multiple services require specific ports (see Tech Stack section)

## External Dependencies

### Required External Services

- **Docker & Docker Compose**: For running the full stack
- **MinIO**: S3-compatible object storage (self-hosted)
- **Supabase**: Full backend platform (self-hosted)
  - PostgreSQL database
  - Auth service
  - Storage service
  - Realtime service
  - REST API
  - Studio UI

### External Packages

- **Frontend**: Next.js, React, Material-UI, TypeScript
- **Backend**: Flask, boto3 (S3 client), Pillow (image processing), numpy, PyJWT
- **Shared**: Supabase client libraries, custom bloom packages
- **Build**: Turbo for monorepo orchestration

### Network Dependencies

- **Kong API Gateway**: Routes requests to appropriate backend services
- **Nginx**: Reverse proxy in production environment
- **Inter-service communication**: Services communicate via Docker network

### Development Tools

- **ts-node**: For running TypeScript scripts
- **Make**: Orchestration via Makefile commands
  - `make dev-up` / `make prod-up`
  - `make dev-down` / `make prod-down`
  - `make rebuild-dev-fresh` / `make rebuild-prod-fresh`

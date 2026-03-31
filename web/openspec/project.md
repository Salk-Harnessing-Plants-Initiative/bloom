# Project Context

## Purpose
Bloom is a web application for sharing and exploring scientific data within the Salk Harnessing Plants Initiative. It provides tools for researchers to:
- Browse and visualize plant phenotype data, genotypes, gene expression, and genomic data
- Access greenhouse experiment data (cylinder experiments, scans, images)
- View and analyze single-cell RNA sequencing (scRNA-seq) data
- Explore plant genes, accessions, and traits
- Integrate JBrowse genome browser for genomic visualization
- Generate videos from time-series plant imaging data

## Tech Stack

### Frontend
- **Next.js 16.0.0** (App Router) - React-based framework with server-side rendering
- **React 19.2.0** - UI library
- **TypeScript 5.x** - Type-safe JavaScript
- **Tailwind CSS** - Utility-first CSS framework
- **Material UI (MUI)** - Component library (@mui/material, @mui/icons-material, @mui/x-data-grid)
- **D3.js** - Data visualization library
- **JBrowse** (@jbrowse/react-app, @jbrowse/core) - Genomic data browser
- **html2canvas** - Screenshot generation

### Backend Services
- **Supabase** (self-hosted) - Backend-as-a-Service platform providing:
  - PostgreSQL database with Row Level Security (RLS)
  - Authentication (Supabase Auth)
  - Storage API
  - Real-time subscriptions
- **Flask (Python)** - Microservice for video generation and S3 content access
- **MinIO** - S3-compatible object storage for images and files
- **Kong** - API gateway (part of Supabase stack)
- **nginx** - Reverse proxy

### Infrastructure
- **Docker Compose** - Container orchestration for local dev and production
- **Turbo** - Monorepo build system
- **pnpm** - Package manager (v10.19.0)

### Internal Packages (Monorepo)
- **@salk-hpi/bloom-js** - Core TypeScript utilities and types
- **@salk-hpi/bloom-fs** - File system utilities, CSV/XLSX parsing, image processing

### Analytics & Monitoring
- **Mixpanel** - User behavior analytics
- **Vercel Analytics** - Performance monitoring

## Project Conventions

### Code Style
- **TypeScript strict mode** enabled in all packages
- **Path aliases**: Use `@/*` for imports relative to the web app root (e.g., `@/components/...`)
- **Naming conventions**:
  - React components: PascalCase (e.g., `UserProfile.tsx`)
  - Utilities/functions: camelCase
  - Database tables: snake_case (e.g., `cyl_experiments`, `cyl_plants`)
  - Environment variables: SCREAMING_SNAKE_CASE
- **File organization**: Next.js App Router structure (`app/` directory with route-based folders)
- **Component structure**: Server components by default, use `'use client'` directive only when needed

### Architecture Patterns
- **Monorepo structure**: Root workspace with `web/`, `packages/`, and service directories
- **Microservices**: Separate services for web frontend, Flask backend, MinIO storage, and Supabase
- **Server-first rendering**: Leverage Next.js App Router with React Server Components
- **Database access patterns**:
  - Use Supabase client for database queries
  - Row Level Security (RLS) policies enforce authentication requirements
  - Service role key used for admin operations in Flask and scripts
- **Storage architecture**:
  - MinIO for S3-compatible object storage
  - Supabase Storage API as abstraction layer
  - Images stored in buckets with specific paths (e.g., `images/`)
- **Authentication**: 
  - Supabase Auth with JWT tokens using `@supabase/ssr` package
  - Server-side auth utilities in `web/lib/supabase/server.ts` (async, for Server Components and API routes)
  - Client-side auth utilities in `web/lib/supabase/client.ts` (sync, for Client Components)
  - Middleware handles auth state refresh using `@supabase/ssr`
- **API routes**: Next.js API routes in `app/api/` for server-side logic

### Testing Strategy
- Test data available in `test_data/` directory with CSV files for seeding database
- `dev_init.ts` script for loading test data into development environment
- Environment-based testing: `.env.dev` for development, `.env.prod` for production

### Git Workflow
- **Repository**: `Salk-Harnessing-Plants-Initiative/bloom` on GitHub
- **Main branch**: `main`
- Docker-based development workflow with hot reloading

### Environment Management
- **Environment files**:
  - Root level: `.env.dev`, `.env.prod` (for Docker Compose)
  - Web app: `web/.env` (for Next.js)
- **Key environment variables**:
  - `NEXT_PUBLIC_SUPABASE_URL` - Supabase API URL
  - `NEXT_PUBLIC_SUPABASE_ANON_KEY` - Supabase anonymous key
  - `SERVICE_ROLE_KEY` - Supabase admin key
  - `JWT_SECRET` - JWT signing secret
  - `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` - MinIO credentials

### Build & Development
- **Start development**: `make dev-up` (runs `docker-compose.dev.yml`)
- **Start production**: `make prod-up` (runs `docker-compose.prod.yml`)
- **Local web dev**: `npm run dev` (port 3000)
- **Package manager**: pnpm for monorepo, npm in containers
- **TypeScript compilation**: `tsc --build` for packages, Next.js handles web app

## Domain Context

### Scientific Data Types
- **Species**: Plant species data (genus, species, common name)
- **Phenotypers**: Scientists conducting phenotyping experiments
- **Accessions**: Plant accession identifiers (e.g., PI458606)
- **Genes**: Gene information with candidates and supporting evidence
- **Expression data**: Single-cell RNA-seq counts stored as JSON in MinIO (`scrna/counts/{dataset}/{gene}.json`)
- **Cylinder (CYL) experiments**: Automated greenhouse phenotyping system data
  - Experiments, waves, plants, scanners, scans, images
  - Time-series imaging data for plant growth monitoring
  - Camera settings and scan metadata
- **Traits**: Phenotypic traits with sources and measurements
- **Genotypes**: Genetic variation data
- **JBrowse**: Genome browser sessions for genomic sequence visualization

### Key Data Relationships
- Experiments contain waves, which contain plants
- Plants are scanned by scanners, producing scans with associated images
- Images are stored in MinIO and referenced by database records
- Species linked to accessions, which are linked to experiments

## Important Constraints
- **Authentication required**: Most database operations require authenticated users (enforced by RLS policies)
- **Image rendering**: Remote image patterns must be configured in `next.config.js` for Next.js Image optimization
- **Storage access**: MinIO requires specific S3 bucket configuration and credentials
- **Database timeouts**: Configured to 5 minutes for long-running queries
- **React version pinning**: React 18.2.0 used as override due to dependency compatibility (though React 19.2.0 also referenced)
- **Docker requirement**: Full stack requires Docker and Docker Compose
- **MinIO volume**: Requires host directory `/data/minio` with proper permissions (777)

## External Dependencies
- **Supabase services**: 
  - PostgreSQL database (port 5432)
  - Kong API gateway (port 8000)
  - Auth server
  - Storage API
  - Realtime server
- **MinIO**: S3-compatible storage (default bucket configured via environment)
- **Flask service**: Port 5002 for video generation and image processing
- **External domains**:
  - Production: `api.bloom.salk.edu`
  - Staging: `api.bloom-staging.salkhpi.org`
  - Development: `localhost:8000` (Supabase), `localhost:3000` (Next.js), `localhost:5002` (Flask)
- **Python dependencies**: Flask app uses boto3, PIL, numpy for video/image processing

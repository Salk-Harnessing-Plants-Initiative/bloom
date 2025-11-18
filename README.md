# Bloom - Plant Phenotyping Platform

A comprehensive web application for plant phenotyping data management, built with Next.js, Supabase, and MinIO.

## Getting Started

Choose your setup:

- **[Development Setup](./DEV_SETUP.md)** - For local development ([Web version](https://htmlpreview.github.io/?https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/setup-guide/DEV_SETUP.html))
- **[Production Setup](./PROD_SETUP.md)** - For production deployment ([Web version](https://htmlpreview.github.io/?https://github.com/Salk-Harnessing-Plants-Initiative/bloom/blob/setup-guide/PROD_SETUP.html))

## Prerequisites

Before starting, ensure you have:

- Docker installed
- Python 3.8 or higher

## Repository Structure

```
bloom-v2/
├── web/                    # Next.js frontend application
├── flask/                  # Flask API for video generation
├── supabase/              # Supabase configuration and migrations
│   └── migrations/        # Database migration files
├── minio/                 # MinIO storage configuration
│   └── init/              # Bucket initialization scripts
├── scripts/               # Utility scripts for data loading
├── test_data/             # Sample data for testing
├── docker-compose.dev.yml # Development stack configuration
├── docker-compose.prod.yml # Production stack configuration
└── Makefile               # Helper commands
```

## Architecture Overview

### Development Stack
- **Frontend**: Next.js (hot reload) - http://localhost:3000
- **Database**: PostgreSQL via Supabase - localhost:5432
- **Storage**: MinIO S3 - http://localhost:9100
- **API**: Flask - http://localhost:5002
- **Studio**: Supabase Studio - http://localhost:55323

### Production Stack
- **Frontend**: Next.js (optimized build) - http://yourdomain.com
- **Database**: PostgreSQL via Supabase
- **Storage**: MinIO S3
- **API**: Flask
- **All services behind Nginx reverse proxy**


## Available Commands

### Stack Management
```bash
make dev-up              # Start development stack
make dev-down            # Stop development stack
make prod-up             # Start production stack
make prod-down           # Stop production stack
make dev-logs            # View development logs
make prod-logs           # View production logs
```

### Database Operations
```bash
make reset-storage       # Reset database and storage (DEV only)
make load-test-data      # Load CSV test data into database
```

### Storage (S3 Buckets) Operations
```bash
make upload-images       # Upload test images to MinIO
make create-bucket BUCKET=name [PUBLIC=true]  # Create new bucket
make list-buckets        # List all storage buckets
```

## Test Data

The repository includes sample test data:
- 14 CSV files with reference data
- 72 sample plant scan images
- Scripts to load data automatically

Load test data with:
```bash
make load-test-data      # Loads CSV data into database
make upload-images       # Uploads images to MinIO/S3
```

## Support

For issues and questions:
2. Review logs with `make dev-logs` or `make prod-logs`
3. Open an issue on GitHub




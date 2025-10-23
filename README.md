# bloom
Packages and infrastructure for bloom web application.

# Overview 
This repository contains:
- **web/** – Next.js frontend
- **supabase/** – self-hosted Supabase stack
- **minio/** – S3-compatible storage
- **docker-compose.dev.yml / docker-compose.prod.yml** – environment definitions
- **Makefile** – helper commands to run the full stack

## Prerequisites
###  1: Root-level environment files (for Docker)
These are used when running the full stack with Docker:
- `.env.dev` → for local development (used by `make dev`)
- `.env.prod` → for production / deployment (used by `make prod`)

###  2: Web app environment file (for running frontend locally)
- `.env.dev` → for local development  
- `.env.prod` → for production / deployment  

## Starting the Full Stack in Development
make dev

## Starting the Full Stack in Production
make dev


### To stop all containers:
make prod

### To follow logs:
make logs

### To rebuild everything from scratch:
make rebuild



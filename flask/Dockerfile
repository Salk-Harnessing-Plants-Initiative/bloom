# syntax=docker/dockerfile:1

# Use uv's official Python image which includes uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS base

# Install system dependencies (ffmpeg for video processing)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Development stage
FROM base AS dev

# Install dependencies including dev dependencies
RUN uv sync --all-extras

# Copy application code
COPY . .

# Expose port
EXPOSE 5002

# Run Flask in development mode
CMD ["uv", "run", "flask", "run", "--host=0.0.0.0", "--port=5002"]

# Production stage
FROM base AS prod

# Install only production dependencies (no dev deps)
RUN uv sync --no-dev

# Copy application code
COPY . .

# Expose port
EXPOSE 5002

# Run Flask application
CMD ["uv", "run", "python", "app.py"]

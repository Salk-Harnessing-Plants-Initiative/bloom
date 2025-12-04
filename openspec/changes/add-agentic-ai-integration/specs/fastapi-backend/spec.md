# FastAPI Backend Capability Specification

## ADDED Requirements

### Requirement: FastAPI Service Architecture

The system SHALL provide a FastAPI-based backend service replacing Flask.

#### Scenario: FastAPI service startup

- **WHEN** FastAPI service starts
- **THEN** service binds to configured port (default 5003)
- **AND** service exposes `/docs` endpoint with interactive API documentation
- **AND** service exposes `/health` endpoint for health checks
- **AND** service connects to PostgreSQL database
- **AND** service connects to MinIO storage

#### Scenario: Request validation with Pydantic

- **WHEN** client sends POST request to `/api/agent/query`
- **AND** request body contains `query` field as string
- **THEN** Pydantic validates request structure automatically
- **WHEN** request is invalid (e.g., missing required field)
- **THEN** FastAPI returns 422 Unprocessable Entity with validation details

#### Scenario: Async request handling

- **WHEN** multiple clients send concurrent requests
- **THEN** FastAPI handles requests asynchronously
- **AND** requests do not block each other
- **AND** system maintains performance under load

### Requirement: Video Generation Migration

The system SHALL migrate video generation from Flask to FastAPI.

#### Scenario: Video generation request

- **WHEN** client sends POST to `/api/videos/generate`
- **AND** request includes experiment_id, decimation_factor, fps
- **THEN** FastAPI validates request with VideoGenerationRequest model
- **AND** system generates video asynchronously
- **AND** system returns job ID or video URL

#### Scenario: S3/MinIO integration

- **WHEN** video generation requires image retrieval
- **THEN** system fetches images from MinIO using boto3
- **AND** system processes images asynchronously
- **AND** system uploads generated video to MinIO

#### Scenario: JWT authentication

- **WHEN** client sends request with JWT token
- **THEN** FastAPI middleware validates token
- **AND** system extracts user information
- **AND** system applies user-specific permissions

### Requirement: REST API Endpoints

The system SHALL provide REST API endpoints for all operations.

#### Scenario: List experiments endpoint

- **WHEN** client sends GET to `/api/experiments`
- **THEN** system queries database for experiments
- **AND** system returns JSON array of experiments
- **AND** system supports pagination via query params

#### Scenario: Get dataset endpoint

- **WHEN** client sends GET to `/api/datasets/{dataset_id}`
- **THEN** system retrieves dataset from database
- **AND** system returns dataset details
- **AND** system returns 404 if dataset not found

#### Scenario: Database query endpoint

- **WHEN** client sends POST to `/api/database/query`
- **AND** request includes table name and filters
- **THEN** system validates table name exists
- **AND** system applies Guardrails validation
- **AND** system executes query and returns results

### Requirement: Error Handling

The system SHALL handle errors gracefully with informative responses.

#### Scenario: Validation error

- **WHEN** request fails Pydantic validation
- **THEN** system returns 422 status code
- **AND** response includes field-specific error messages
- **AND** response follows RFC 7807 problem details format

#### Scenario: Database error

- **WHEN** database query fails
- **THEN** system logs full error details
- **AND** system returns 500 status code
- **AND** response includes user-friendly error message (not DB internals)

#### Scenario: Authentication error

- **WHEN** JWT token is invalid or expired
- **THEN** system returns 401 Unauthorized
- **AND** response includes "token expired" or "invalid token" message

### Requirement: API Documentation

The system SHALL provide auto-generated API documentation.

#### Scenario: OpenAPI schema

- **WHEN** client accesses `/docs`
- **THEN** system displays interactive Swagger UI
- **AND** documentation includes all endpoints
- **AND** documentation includes Pydantic model schemas
- **AND** documentation includes example requests/responses

#### Scenario: Alternative documentation formats

- **WHEN** client accesses `/redoc`
- **THEN** system displays ReDoc UI
- **WHEN** client accesses `/openapi.json`
- **THEN** system returns OpenAPI 3.0 schema

### Requirement: Configuration Management

The system SHALL load configuration from environment variables.

#### Scenario: Database configuration

- **WHEN** service starts
- **THEN** system reads DATABASE_URL from environment
- **AND** system connects to PostgreSQL
- **AND** system validates connection on startup

#### Scenario: Storage configuration

- **WHEN** service starts
- **THEN** system reads MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY
- **AND** system configures boto3 client
- **AND** system validates MinIO connection

#### Scenario: Model configuration

- **WHEN** service starts
- **THEN** system reads DEFAULT_MODEL, CODE_MODEL, FAST_MODEL from environment
- **AND** system validates Ollama connection
- **AND** system falls back to defaults if env vars not set

### Requirement: Logging and Monitoring

The system SHALL log all operations and provide monitoring endpoints.

#### Scenario: Request logging

- **WHEN** any API request is received
- **THEN** system logs HTTP method, path, user, timestamp
- **AND** system logs response status code and latency
- **AND** logs follow structured JSON format

#### Scenario: Error logging

- **WHEN** error occurs during request processing
- **THEN** system logs full stack trace
- **AND** system logs request details for reproduction
- **AND** system assigns error ID for tracking

#### Scenario: Health check endpoint

- **WHEN** client sends GET to `/health`
- **THEN** system checks database connection
- **AND** system checks MinIO connection
- **AND** system checks Ollama connection (if configured)
- **AND** system returns 200 if all healthy
- **AND** system returns 503 with details if any unhealthy

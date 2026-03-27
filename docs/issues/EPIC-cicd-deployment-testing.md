# EPIC: CI/CD Pipeline with Staging & Production Environments

## Summary

Implement a complete CI/CD pipeline using GitHub Actions with a self-hosted runner for automated testing and deployment to staging and production environments on a local server.

## Background

Currently, Bloom deployment is entirely manual via Makefile commands. There is no:
- Automated testing infrastructure
- CI/CD pipeline
- Staging environment for pre-production validation
- Automated deployment process

This makes deployments risky, slow, and error-prone.

## Goals

1. **Automated Testing**: Unit, integration, and E2E tests run on every PR/push
2. **Staging Environment**: Automatic deployment to staging on push to `dev`
3. **Production Environment**: Manual-approval deployment on push to `main`
4. **Self-Hosted Runner**: CI/CD runs on local server for security and performance
5. **Environment Isolation**: Separate databases, storage, and ports per environment

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        GitHub Repository                         │
├─────────────────────────────────────────────────────────────────┤
│  Push to 'dev'              Push to 'main'                      │
│       │                          │                               │
│       ▼                          ▼                               │
│  ┌─────────────────────────────────────────────────┐            │
│  │              CI Pipeline (All Branches)          │            │
│  │  1. Lint & Type Check                           │            │
│  │  2. Unit Tests (Vitest)                         │            │
│  │  3. Build (turbo build)                         │            │
│  │  4. Integration Tests (API against test DB)     │            │
│  │  5. E2E Tests (Playwright)                      │            │
│  └─────────────────────────────────────────────────┘            │
│       │ (tests pass)             │ (tests pass)                 │
│       ▼                          ▼                               │
│  Auto Deploy                Manual Approval                     │
│  to STAGING                 then Deploy to PROD                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                Local Server (Self-Hosted Runner)                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────┐    ┌──────────────────────┐          │
│  │  STAGING             │    │  PRODUCTION          │          │
│  │                      │    │                      │          │
│  │  Port: 3000          │    │  Port: 443 (nginx)   │          │
│  │  DB: bloom_staging   │    │  DB: bloom_prod      │          │
│  │  MinIO bucket prefix:│    │  MinIO bucket prefix:│          │
│  │    staging-*         │    │    prod-*            │          │
│  │                      │    │                      │          │
│  │  docker-compose      │    │  docker-compose      │          │
│  │    .staging.yml      │    │    .prod.yml         │          │
│  └──────────────────────┘    └──────────────────────┘          │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  GitHub Actions Runner (self-hosted)                      │  │
│  │  - Runs CI jobs                                           │  │
│  │  - Has Docker access                                      │  │
│  │  - Manages deployments                                    │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Issues in this EPIC

| Issue | Title | Priority | Dependencies |
|-------|-------|----------|--------------|
| #cicd-0 | Server Setup (pbiob-gh.salk.edu) | P0 | None (blocking) |
| #cicd-1 | Self-Hosted Runner Setup | P0 | #cicd-0 |
| #cicd-2 | Test Infrastructure Setup | P0 | None (can start now) |
| #cicd-3 | CI Pipeline Workflow | P0 | #cicd-1, #cicd-2 |
| #cicd-4 | Staging Environment Setup | P0 | #cicd-0, #cicd-1 |
| #cicd-5 | Production Deployment Workflow | P0 | #cicd-3, #cicd-4 |

## Implementation Order

```
Phase 0: Server Setup (BLOCKED - waiting on IT)
└── #cicd-0: Server Setup (pbiob-gh.salk.edu)
    ├── IT tasks: DNS, firewall, Docker, user accounts
    └── Our tasks: SSL, nginx, directories

Phase 1: Foundation
├── #cicd-1: Self-Hosted Runner Setup (needs server)
└── #cicd-2: Test Infrastructure Setup ← CAN START NOW (local dev)

Phase 2: CI Pipeline
└── #cicd-3: CI Pipeline Workflow (depends on Phase 1)

Phase 3: Environments & Deployment
├── #cicd-4: Staging Environment Setup (needs server + DNS + SSL)
└── #cicd-5: Production Deployment Workflow (depends on #cicd-3, #cicd-4)
```

## Server: pbiob-gh.salk.edu

**OS**: Ubuntu 24.04 LTS
**SSL**: Let's Encrypt via certbot
**Reverse Proxy**: nginx

### Subdomains

| Environment | Service | Subdomain |
|-------------|---------|-----------|
| Production | Bloom Web | `bloom.pbiob-gh.salk.edu` |
| Production | API (Kong) | `api.pbiob-gh.salk.edu` |
| Production | MinIO | `storage.pbiob-gh.salk.edu` |
| Staging | Bloom Web | `staging.pbiob-gh.salk.edu` |
| Staging | API (Kong) | `staging-api.pbiob-gh.salk.edu` |
| Staging | MinIO | `staging-storage.pbiob-gh.salk.edu` |

## Success Metrics

- [ ] All PRs require passing CI before merge
- [ ] Push to `dev` automatically deploys to staging within 10 minutes
- [ ] Production deployments require manual approval
- [ ] Test coverage reported on every PR
- [ ] Zero manual steps required for staging deployments
- [ ] Rollback capability for failed deployments

## Environment Configuration

### Staging
- **URL**: `staging.bloom.local` or `localhost:3000`
- **Database**: `bloom_staging` (separate from prod)
- **MinIO**: Buckets prefixed with `staging-`
- **Triggered by**: Push to `dev` branch

### Production
- **URL**: `bloom.local` or server's domain
- **Database**: `bloom_prod`
- **MinIO**: Buckets prefixed with `prod-`
- **Triggered by**: Push to `main` + manual approval

## Labels

`epic`, `cicd`, `infrastructure`, `testing`, `deployment`

---

## Related Documents

- [Self-Hosted Runner Setup](./cicd-1-self-hosted-runner.md)
- [Test Infrastructure Setup](./cicd-2-test-infrastructure.md)
- [CI Pipeline Workflow](./cicd-3-ci-pipeline.md)
- [Staging Environment Setup](./cicd-4-staging-environment.md)
- [Production Deployment Workflow](./cicd-5-production-deployment.md)
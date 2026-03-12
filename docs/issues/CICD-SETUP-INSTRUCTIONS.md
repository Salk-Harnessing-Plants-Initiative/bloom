# CI/CD Setup Instructions

## Creating GitHub Issues

Your GitHub token needs to be updated (org requires token lifetime ≤ 366 days). Once fixed:

```bash
# Unset the conflicting token first
unset GITHUB_TOKEN

# Authenticate with gh cli
gh auth login

# Create labels first (if they don't exist)
gh label create cicd --color 0E8A16 --description "CI/CD pipeline work"
gh label create infrastructure --color D4C5F9 --description "Infrastructure changes"
gh label create testing --color FBCA04 --description "Testing related"
gh label create deployment --color 1D76DB --description "Deployment related"
gh label create staging --color C5DEF5 --description "Staging environment"
gh label create runner --color BFD4F2 --description "GitHub Actions runner"
gh label create github-actions --color 0366D6 --description "GitHub Actions workflows"
gh label create epic --color 7057FF --description "Epic issue"

# Create the Epic issue
gh issue create \
  --title "EPIC: CI/CD Pipeline with Staging & Production Environments" \
  --body-file docs/issues/EPIC-cicd-deployment-testing.md \
  --label "epic,cicd,infrastructure"

# Create individual issues (note the issue numbers for linking)
gh issue create \
  --title "Server Setup for Bloom Deployment (pbiob-gh.salk.edu)" \
  --body-file docs/issues/cicd-0-server-setup.md \
  --label "cicd,infrastructure"

gh issue create \
  --title "Self-Hosted GitHub Actions Runner Setup" \
  --body-file docs/issues/cicd-1-self-hosted-runner.md \
  --label "cicd,infrastructure,runner"

gh issue create \
  --title "Test Infrastructure Setup" \
  --body-file docs/issues/cicd-2-test-infrastructure.md \
  --label "cicd,testing,infrastructure"

gh issue create \
  --title "CI Pipeline Workflow" \
  --body-file docs/issues/cicd-3-ci-pipeline.md \
  --label "cicd,github-actions,testing"

gh issue create \
  --title "Staging Environment Setup" \
  --body-file docs/issues/cicd-4-staging-environment.md \
  --label "cicd,infrastructure,staging,deployment"

gh issue create \
  --title "Production Deployment Workflow" \
  --body-file docs/issues/cicd-5-production-deployment.md \
  --label "cicd,deployment"
```

## Implementation Order

```
Phase 0: Server Setup (BLOCKED - waiting on IT/Fernando)
└── #cicd-0: Server Setup (pbiob-gh.salk.edu)
    ├── IT: DNS records, firewall, Docker, user accounts
    └── Us: SSL certs, nginx config, directories

Phase 1: Foundation
├── #cicd-1: Self-Hosted Runner Setup ← needs server
└── #cicd-2: Test Infrastructure Setup ← CAN START NOW

Phase 2: CI Pipeline
└── #cicd-3: CI Pipeline Workflow

Phase 3: Environments
├── #cicd-4: Staging Environment Setup
└── #cicd-5: Production Deployment Workflow
```

## What You Can Start Now

While waiting for the server (expected Friday):

1. **Test Infrastructure (#cicd-2)**
   - Install Vitest and Playwright
   - Write initial test files
   - Configure test scripts

2. **CI Workflow drafting (#cicd-3)**
   - Create `.github/workflows/ci.yml`
   - Can test locally with `act` tool
   - Won't run until runner is set up

3. **Prepare for server setup (#cicd-0)**
   - Draft nginx configs for 6 subdomains
   - Draft docker-compose.staging.yml
   - Prepare .env templates

## Files Created

| File | Description |
|------|-------------|
| [EPIC-cicd-deployment-testing.md](./EPIC-cicd-deployment-testing.md) | Epic overview |
| [cicd-0-server-setup.md](./cicd-0-server-setup.md) | Server & SSL setup |
| [cicd-1-self-hosted-runner.md](./cicd-1-self-hosted-runner.md) | Runner setup |
| [cicd-2-test-infrastructure.md](./cicd-2-test-infrastructure.md) | Testing setup |
| [cicd-3-ci-pipeline.md](./cicd-3-ci-pipeline.md) | CI workflow |
| [cicd-4-staging-environment.md](./cicd-4-staging-environment.md) | Staging env |
| [cicd-5-production-deployment.md](./cicd-5-production-deployment.md) | Prod deploy |
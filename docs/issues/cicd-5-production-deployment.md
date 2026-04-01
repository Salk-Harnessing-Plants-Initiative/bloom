# Issue: Production Deployment Workflow

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0
**Dependencies**: #cicd-3 (CI Pipeline), #cicd-4 (Staging Environment)
**Labels**: `cicd`, `deployment`, `production`

## Summary

Create a production deployment workflow with manual approval gates, ensuring only tested and reviewed code reaches production.

## Background

Production deployments require additional safeguards:
- Manual approval before deployment
- Verification that code passed all CI checks
- Confirmation that staging deployment was successful
- Ability to quickly rollback if issues arise

## Requirements

### Pre-Deployment
- [ ] All CI checks must pass
- [ ] Code must be merged to `main` branch
- [ ] Manual approval required from authorized team member
- [ ] Staging verification completed

### Deployment
- [ ] Zero-downtime deployment
- [ ] Database migrations run before app update
- [ ] Health checks verify successful deployment
- [ ] Automatic rollback on health check failure

### Post-Deployment
- [ ] Deployment logged with commit SHA, timestamp, deployer
- [ ] Notification sent on success/failure
- [ ] Easy rollback procedure documented

## Implementation

### 1. Production Deployment Workflow

```yaml
# .github/workflows/deploy-production.yml
name: Deploy to Production

on:
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      skip_approval:
        description: 'Skip manual approval (emergency only)'
        required: false
        default: 'false'
        type: boolean

concurrency:
  group: production-deploy
  cancel-in-progress: false

jobs:
  ci:
    uses: ./.github/workflows/ci.yml

  approval:
    name: Await Approval
    runs-on: [self-hosted, bloom]
    needs: [ci]
    if: github.event.inputs.skip_approval != 'true'
    environment:
      name: production
      url: https://bloom.local
    steps:
      - name: Approval checkpoint
        run: echo "Deployment approved by ${{ github.actor }}"

  deploy:
    name: Deploy to Production
    runs-on: [self-hosted, bloom]
    needs: [ci, approval]
    if: always() && needs.ci.result == 'success' && (needs.approval.result == 'success' || needs.approval.result == 'skipped')
    steps:
      - uses: actions/checkout@v4

      - name: Record deployment start
        id: deploy-start
        run: |
          echo "start_time=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> $GITHUB_OUTPUT
          echo "commit_sha=${{ github.sha }}" >> $GITHUB_OUTPUT
          echo "deployer=${{ github.actor }}" >> $GITHUB_OUTPUT

      - name: Create .env.prod from secrets
        run: |
          cat > .env.prod << EOF
          POSTGRES_PASSWORD=${{ secrets.PROD_DB_PASSWORD }}
          JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}
          ANON_KEY=${{ secrets.PROD_ANON_KEY }}
          SERVICE_ROLE_KEY=${{ secrets.PROD_SERVICE_ROLE_KEY }}
          MINIO_ROOT_USER=${{ secrets.PROD_MINIO_ROOT_USER }}
          MINIO_ROOT_PASSWORD=${{ secrets.PROD_MINIO_ROOT_PASSWORD }}
          MINIO_DATA_PATH=/var/lib/bloom/production/minio
          EOF

      - name: Backup current state
        run: |
          mkdir -p /var/lib/bloom/backups/production
          docker-compose -f docker-compose.prod.yml exec -T db \
            pg_dump -U postgres bloom_prod > \
            /var/lib/bloom/backups/production/pre-deploy-$(date +%Y%m%d-%H%M%S).sql || true

          # Save current image tags for rollback
          docker-compose -f docker-compose.prod.yml config | \
            grep "image:" > /var/lib/bloom/backups/production/images-$(date +%Y%m%d-%H%M%S).txt || true

      - name: Build production images
        run: |
          docker-compose -f docker-compose.prod.yml build --pull

      - name: Run database migrations
        run: |
          docker-compose -f docker-compose.prod.yml run --rm \
            -e DATABASE_URL=postgres://postgres:${{ secrets.PROD_DB_PASSWORD }}@db:5432/bloom_prod \
            bloom-web pnpm db:migrate

      - name: Deploy with rolling update
        id: deploy
        run: |
          # Stop old containers and start new ones
          docker-compose -f docker-compose.prod.yml up -d --remove-orphans

          echo "deployed=true" >> $GITHUB_OUTPUT

      - name: Health check
        id: health
        run: |
          echo "Waiting for production to be healthy..."

          # Wait up to 3 minutes for health
          for i in {1..36}; do
            if curl -sf http://localhost/api/health > /dev/null 2>&1; then
              echo "Production is healthy!"
              echo "healthy=true" >> $GITHUB_OUTPUT
              exit 0
            fi
            echo "Attempt $i/36 - waiting..."
            sleep 5
          done

          echo "Health check failed!"
          echo "healthy=false" >> $GITHUB_OUTPUT
          exit 1

      - name: Rollback on failure
        if: failure() && steps.deploy.outputs.deployed == 'true'
        run: |
          echo "::error::Deployment failed, initiating rollback..."

          # Get previous commit
          PREVIOUS_SHA=$(git rev-parse HEAD~1)

          # Checkout previous version
          git checkout $PREVIOUS_SHA

          # Rebuild and redeploy
          docker-compose -f docker-compose.prod.yml build bloom-web
          docker-compose -f docker-compose.prod.yml up -d

          echo "Rollback complete to $PREVIOUS_SHA"

      - name: Record deployment success
        if: success()
        run: |
          echo "## Production Deployment Successful :rocket:" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Detail | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|--------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Commit | \`${{ github.sha }}\` |" >> $GITHUB_STEP_SUMMARY
          echo "| Deployer | @${{ github.actor }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Time | ${{ steps.deploy-start.outputs.start_time }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Environment | Production |" >> $GITHUB_STEP_SUMMARY

      - name: Notify on success
        if: success()
        run: |
          echo "Production deployment successful!"
          # Add Slack/email notification here
          # curl -X POST -H 'Content-type: application/json' \
          #   --data '{"text":"Production deployed: ${{ github.sha }}"}' \
          #   ${{ secrets.SLACK_WEBHOOK_URL }}

      - name: Notify on failure
        if: failure()
        run: |
          echo "::error::Production deployment failed!"
          # Add critical alert notification here
```

### 2. GitHub Environment Configuration

Create production environment in: Repository → Settings → Environments → New environment

**Environment name**: `production`

**Protection rules**:
- [x] Required reviewers: Add team members who can approve deployments
- [x] Wait timer: 0 minutes (optional: add delay for "cooling off")
- [ ] Prevent self-review (optional)

**Environment secrets**: (Same structure as staging, but with production values)

### 3. Rollback Workflow

```yaml
# .github/workflows/rollback-production.yml
name: Rollback Production

on:
  workflow_dispatch:
    inputs:
      target_sha:
        description: 'Commit SHA to rollback to (leave empty for previous)'
        required: false
        type: string

jobs:
  rollback:
    name: Rollback Production
    runs-on: [self-hosted, bloom]
    environment: production
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Determine rollback target
        id: target
        run: |
          if [ -n "${{ github.event.inputs.target_sha }}" ]; then
            echo "sha=${{ github.event.inputs.target_sha }}" >> $GITHUB_OUTPUT
          else
            echo "sha=$(git rev-parse HEAD~1)" >> $GITHUB_OUTPUT
          fi

      - name: Checkout target
        run: git checkout ${{ steps.target.outputs.sha }}

      - name: Create .env.prod from secrets
        run: |
          cat > .env.prod << EOF
          POSTGRES_PASSWORD=${{ secrets.PROD_DB_PASSWORD }}
          JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}
          ANON_KEY=${{ secrets.PROD_ANON_KEY }}
          SERVICE_ROLE_KEY=${{ secrets.PROD_SERVICE_ROLE_KEY }}
          MINIO_ROOT_USER=${{ secrets.PROD_MINIO_ROOT_USER }}
          MINIO_ROOT_PASSWORD=${{ secrets.PROD_MINIO_ROOT_PASSWORD }}
          MINIO_DATA_PATH=/var/lib/bloom/production/minio
          EOF

      - name: Rebuild and deploy
        run: |
          docker-compose -f docker-compose.prod.yml build bloom-web
          docker-compose -f docker-compose.prod.yml up -d

      - name: Verify rollback
        run: |
          sleep 30
          curl -sf http://localhost/api/health || exit 1
          echo "Rollback successful!"
```

### 4. Update Makefile

```makefile
# Production commands
prod-up:
	docker-compose -f docker-compose.prod.yml up -d

prod-down:
	docker-compose -f docker-compose.prod.yml down

prod-logs:
	docker-compose -f docker-compose.prod.yml logs -f

prod-status:
	docker-compose -f docker-compose.prod.yml ps

prod-backup:
	docker-compose -f docker-compose.prod.yml exec -T db \
		pg_dump -U postgres bloom_prod > \
		/var/lib/bloom/backups/production/manual-$(shell date +%Y%m%d-%H%M%S).sql
	@echo "Backup created!"

prod-restart:
	docker-compose -f docker-compose.prod.yml restart
```

## Deployment Checklist

Before triggering production deployment:

- [ ] All CI checks pass on `main`
- [ ] Changes have been tested on staging
- [ ] Database migrations are backwards-compatible
- [ ] Rollback plan is documented for this release
- [ ] Team is aware of deployment

## GitHub Secrets Required (Production)

| Secret | Description |
|--------|-------------|
| `PROD_DB_PASSWORD` | PostgreSQL password |
| `PROD_JWT_SECRET` | JWT signing secret |
| `PROD_ANON_KEY` | Supabase anonymous key |
| `PROD_SERVICE_ROLE_KEY` | Supabase service role key |
| `PROD_MINIO_ROOT_USER` | MinIO admin username |
| `PROD_MINIO_ROOT_PASSWORD` | MinIO admin password |
| `SLACK_WEBHOOK_URL` | (Optional) For notifications |

## Verification Checklist

- [ ] Push to `main` triggers workflow
- [ ] Approval gate pauses deployment until approved
- [ ] Only authorized users can approve
- [ ] Deployment succeeds after approval
- [ ] Health check validates deployment
- [ ] Failed deployment triggers rollback
- [ ] Rollback workflow works
- [ ] Notifications sent on success/failure

## Monitoring After Deployment

After each production deployment, verify:

1. **Application health**: Check `/api/health` endpoint
2. **Database connectivity**: Verify queries work
3. **MinIO access**: Confirm file uploads/downloads work
4. **Auth flow**: Test login/logout
5. **Error rates**: Check for increased errors in logs

## References

- [GitHub Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments/using-environments-for-deployment)
- [Deployment Best Practices](https://docs.github.com/en/actions/deployment/about-deployments/deploying-with-github-actions)
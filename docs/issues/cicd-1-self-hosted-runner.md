# Issue: Self-Hosted GitHub Actions Runner Setup

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0 (Blocking)
**Dependencies**: None
**Labels**: `cicd`, `infrastructure`, `runner`

## Summary

Set up a self-hosted GitHub Actions runner on the local server to execute CI/CD workflows with access to Docker and local resources.

## Background

GitHub's cloud-hosted runners cannot directly deploy to our local server. A self-hosted runner allows us to:
- Run CI jobs on the same machine where deployments happen
- Access Docker daemon for building and running containers
- Avoid network complexity of deploying from cloud to local server
- Keep all operations within the local network for security

## Requirements

### Functional
- [ ] Runner connects to GitHub and receives jobs
- [ ] Runner has Docker access for building images
- [ ] Runner can execute docker-compose commands
- [ ] Runner starts automatically on server boot
- [ ] Runner handles concurrent jobs appropriately

### Security
- [ ] Runner runs as non-root user
- [ ] Runner has minimal required permissions
- [ ] Secrets stored securely (GitHub encrypted secrets)
- [ ] Runner labeled to only accept jobs from this repo

## Implementation Plan

### 1. Create Runner User

```bash
# Create dedicated user for the runner
sudo useradd -m -s /bin/bash github-runner
sudo usermod -aG docker github-runner
```

### 2. Install GitHub Actions Runner

```bash
# As github-runner user
cd /home/github-runner
mkdir actions-runner && cd actions-runner

# Download latest runner (check GitHub for current version)
curl -o actions-runner-linux-x64-2.311.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.311.0/actions-runner-linux-x64-2.311.0.tar.gz

tar xzf ./actions-runner-linux-x64-2.311.0.tar.gz
```

### 3. Configure Runner

```bash
# Get token from: GitHub Repo → Settings → Actions → Runners → New self-hosted runner
./config.sh --url https://github.com/Salk-Harnessing-Plants-Initiative/bloom \
  --token YOUR_TOKEN \
  --name bloom-server \
  --labels bloom,self-hosted,linux \
  --work _work
```

### 4. Install as Service

```bash
sudo ./svc.sh install github-runner
sudo ./svc.sh start
sudo ./svc.sh status
```

### 5. Verify Docker Access

```bash
# As github-runner user
docker ps
docker-compose --version
```

## Directory Structure

```
/home/github-runner/
├── actions-runner/          # GitHub Actions runner
│   ├── _work/               # Job workspaces
│   ├── config.sh
│   ├── run.sh
│   └── svc.sh
└── bloom/                   # Deployment directory (optional)
    ├── staging/
    └── production/
```

## GitHub Workflow Usage

Once configured, workflows can target this runner:

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, bloom]
    steps:
      - uses: actions/checkout@v4
      - name: Deploy
        run: docker-compose up -d
```

## Verification Checklist

- [ ] Runner appears in GitHub: Settings → Actions → Runners
- [ ] Runner status shows "Idle" (green)
- [ ] Test workflow runs successfully on the runner
- [ ] Docker commands work within workflow
- [ ] Runner survives server reboot

## Documentation Updates

- [ ] Add runner setup instructions to deployment docs
- [ ] Document how to update the runner
- [ ] Document how to add additional runners if needed

## Estimated Effort

- Initial setup: 2-3 hours
- Testing and verification: 1 hour
- Documentation: 30 minutes

## References

- [GitHub Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [Runner Security](https://docs.github.com/en/actions/hosting-your-own-runners/about-self-hosted-runners#self-hosted-runner-security)
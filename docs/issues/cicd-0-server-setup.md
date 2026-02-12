# Issue: Server Setup for Bloom Deployment (pbiob-gh.salk.edu)

**Epic**: [CI/CD Pipeline with Staging & Production Environments](./EPIC-cicd-deployment-testing.md)
**Priority**: P0 (Blocking - must complete before CI/CD)
**Dependencies**: None
**Blocks**: #cicd-1 (Runner), #cicd-4 (Staging), #cicd-5 (Production)
**Labels**: `cicd`, `infrastructure`, `blocked-by-IT`

## Summary

Set up the pbiob-gh.salk.edu server for Bloom staging and production deployments, including DNS, SSL certificates, and Docker environment.

## Background

We're migrating Bloom from Vercel to a self-hosted Ubuntu 24 server managed by Salk IT (Fernando). The server will host:
- Production environment (bloom.pbiob-gh.salk.edu)
- Staging environment (staging.pbiob-gh.salk.edu)
- GitHub Actions self-hosted runner for CI/CD

## Server Details

- **Server**: pbiob-gh.salk.edu
- **OS**: Ubuntu 24.04 LTS
- **Managed by**: Fernando (Salk IT)
- **Expected availability**: Friday (pending IT completion)

---

## Tasks: What IT Needs to Do

These tasks require IT administrator access or coordination with Salk networking:

### 1. DNS Records (IT Required)

Create A records pointing to the server's public IP:

| Subdomain | Purpose |
|-----------|---------|
| `bloom.pbiob-gh.salk.edu` | Production web app |
| `api.pbiob-gh.salk.edu` | Production API (Kong gateway) |
| `storage.pbiob-gh.salk.edu` | Production MinIO console |
| `staging.pbiob-gh.salk.edu` | Staging web app |
| `staging-api.pbiob-gh.salk.edu` | Staging API |
| `staging-storage.pbiob-gh.salk.edu` | Staging MinIO console |

- [ ] Request DNS records from Fernando/IT
- [ ] Verify all 6 subdomains resolve correctly: `nslookup bloom.pbiob-gh.salk.edu`

### 2. Firewall / External Access (IT Required)

- [ ] Port 80 (HTTP) open to internet - required for Let's Encrypt validation
- [ ] Port 443 (HTTPS) open to internet - all application traffic
- [ ] Confirm server is accessible from outside Salk network
- [ ] Verify outbound access to: GitHub, Docker Hub, Let's Encrypt (ACME)

### 3. User Accounts (IT Required)

- [ ] Create local accounts: Elizabeth, Nolan, Benfica
- [ ] Grant sudo access to these accounts
- [ ] Add users to `docker` group
- [ ] Confirm SSH access works from Salk network

### 4. Docker Installation (IT Required)

- [ ] Install Docker Engine (latest stable)
- [ ] Install Docker Compose v2
- [ ] Verify: `docker --version` and `docker compose version`

---

## Tasks: What We Do Ourselves

These tasks we can do once we have SSH access and the prerequisites above:

### 5. Directory Structure Setup

```bash
# Create deployment directories
sudo mkdir -p /opt/bloom/{production,staging}
sudo mkdir -p /var/lib/bloom/{prod-db,staging-db,prod-minio,staging-minio,backups}
sudo chown -R $USER:docker /opt/bloom /var/lib/bloom
```

- [ ] Create directory structure
- [ ] Set appropriate permissions

### 6. SSL Certificates (Let's Encrypt + Certbot)

```bash
# Install certbot
sudo apt update
sudo apt install -y certbot python3-certbot-nginx

# Get certificates for all subdomains (run after DNS is configured)
sudo certbot certonly --nginx \
  -d bloom.pbiob-gh.salk.edu \
  -d api.pbiob-gh.salk.edu \
  -d storage.pbiob-gh.salk.edu \
  -d staging.pbiob-gh.salk.edu \
  -d staging-api.pbiob-gh.salk.edu \
  -d staging-storage.pbiob-gh.salk.edu

# Verify auto-renewal is set up
sudo systemctl status certbot.timer
```

- [ ] Install certbot
- [ ] Obtain certificates for all 6 subdomains
- [ ] Verify auto-renewal timer is active
- [ ] Test renewal: `sudo certbot renew --dry-run`

### 7. Nginx Configuration

Update nginx config to:
- Terminate SSL for all subdomains
- Route to appropriate Docker containers
- Handle Let's Encrypt challenge responses

```nginx
# /etc/nginx/sites-available/bloom-prod
server {
    listen 443 ssl http2;
    server_name bloom.pbiob-gh.salk.edu;

    ssl_certificate /etc/letsencrypt/live/bloom.pbiob-gh.salk.edu/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bloom.pbiob-gh.salk.edu/privkey.pem;

    location / {
        proxy_pass http://localhost:3000;  # bloom-web container
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# Similar blocks for api, storage, staging-*, etc.
```

- [ ] Create nginx site configs for all 6 subdomains
- [ ] Enable sites: `sudo ln -s /etc/nginx/sites-available/bloom-* /etc/nginx/sites-enabled/`
- [ ] Test config: `sudo nginx -t`
- [ ] Reload nginx: `sudo systemctl reload nginx`

### 8. GitHub Actions Runner

```bash
# Create runner user
sudo useradd -m -s /bin/bash github-runner
sudo usermod -aG docker github-runner

# Install runner (as github-runner user)
sudo -u github-runner bash
cd /home/github-runner
mkdir actions-runner && cd actions-runner
# Download and configure (see cicd-1-self-hosted-runner.md)
```

- [ ] Create github-runner user
- [ ] Install GitHub Actions runner
- [ ] Configure as service
- [ ] Verify runner appears in GitHub repo settings

### 9. Clone Repository & Initial Deployment

```bash
cd /opt/bloom/production
git clone https://github.com/Salk-Harnessing-Plants-Initiative/bloom.git .

# Create environment file from template
cp .env.example .env.prod
# Edit with production secrets

# Start production stack
docker compose -f docker-compose.prod.yml up -d
```

- [ ] Clone repository to production directory
- [ ] Clone repository to staging directory
- [ ] Configure environment files
- [ ] Verify both stacks start successfully

---

## Port Mapping

All services run in Docker and are accessed via nginx reverse proxy:

| Subdomain | Nginx → | Docker Service | Internal Port |
|-----------|---------|----------------|---------------|
| bloom.pbiob-gh.salk.edu | localhost:3000 | bloom-web (prod) | 3000 |
| api.pbiob-gh.salk.edu | localhost:8000 | kong (prod) | 8000 |
| storage.pbiob-gh.salk.edu | localhost:9001 | minio (prod) | 9001 |
| staging.pbiob-gh.salk.edu | localhost:3100 | bloom-web (staging) | 3000 |
| staging-api.pbiob-gh.salk.edu | localhost:8100 | kong (staging) | 8000 |
| staging-storage.pbiob-gh.salk.edu | localhost:9101 | minio (staging) | 9001 |

**Note**: Staging containers use different host ports (3100, 8100, 9101) to avoid conflicts with production.

---

## Relationship to CI/CD Plan

This issue is **Phase 0** - the prerequisite for all CI/CD work:

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 0: Server Setup (THIS ISSUE)                         │
│  - DNS, SSL, Docker, nginx, directories                     │
│  - BLOCKS everything else                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 1: Foundation (can start in parallel)                │
│  ├── #cicd-1: Self-Hosted Runner ← needs server access      │
│  └── #cicd-2: Test Infrastructure ← can do locally now      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 2: CI Pipeline                                        │
│  └── #cicd-3: CI Workflow ← needs runner                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Phase 3: Deployment                                         │
│  ├── #cicd-4: Staging Environment ← needs subdomains + SSL  │
│  └── #cicd-5: Production Deployment ← needs subdomains + SSL│
└─────────────────────────────────────────────────────────────┘
```

**What you can do NOW (before server is ready):**
- #cicd-2: Test Infrastructure - set up Vitest, Playwright locally
- Draft nginx configs
- Draft docker-compose.staging.yml
- Prepare environment variable templates

**What requires server access:**
- #cicd-1: Self-hosted runner installation
- SSL certificate generation (needs DNS first)
- #cicd-4 & #cicd-5: Actual deployments

---

## Verification Checklist

### IT Tasks Complete
- [ ] All 6 DNS records resolve
- [ ] Ports 80/443 accessible from internet
- [ ] SSH access working
- [ ] Docker installed and working
- [ ] User accounts created with docker group

### Our Tasks Complete
- [ ] Directory structure created
- [ ] SSL certificates obtained for all subdomains
- [ ] Nginx configured and serving HTTPS
- [ ] GitHub runner installed and connected
- [ ] Production stack running
- [ ] Staging stack running
- [ ] Can access all 6 subdomains via HTTPS

---

## Message for Fernando

```
Hi Fernando,

Thanks for setting up the server! Here's what we need:

1. **DNS Records** - 6 A records pointing to the server's public IP:
   - bloom.pbiob-gh.salk.edu
   - api.pbiob-gh.salk.edu
   - storage.pbiob-gh.salk.edu
   - staging.pbiob-gh.salk.edu
   - staging-api.pbiob-gh.salk.edu
   - staging-storage.pbiob-gh.salk.edu

2. **Firewall** - Ports 80 and 443 open to the internet

3. **Docker** - Docker Engine + Docker Compose v2

4. **User accounts** - Elizabeth, Nolan, Benfica with:
   - sudo access
   - Added to docker group
   - SSH access

We'll handle SSL certificates ourselves using Let's Encrypt/certbot.

Questions:
- What's the server's public IP? (for DNS records)
- Is external access already configured?

Thanks!
```

---

## References

- [Let's Encrypt + Certbot](https://certbot.eff.org/instructions?ws=nginx&os=ubuntufocal)
- [GitHub Self-Hosted Runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [Docker Install on Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
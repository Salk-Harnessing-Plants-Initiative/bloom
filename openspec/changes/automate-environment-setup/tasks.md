# Implementation Tasks

## 1. Create Environment Templates
- [ ] 1.1 Create `.env.dev.example` from current `.env.dev` (replace all secrets with placeholders)
- [ ] 1.2 Add comments to `.env.dev.example` explaining each variable
- [ ] 1.3 Create `.env.prod.example` with production-specific settings
- [ ] 1.4 Verify templates contain no actual secrets (only `CHANGEME_*` or `<placeholder>` values)
- [ ] 1.5 Test that templates are valid format (can be parsed as env files)

## 2. Implement Automated Setup Script
- [ ] 2.1 Create `scripts/setup-env.sh` with proper shebang and error handling
- [ ] 2.2 Implement argument parsing (dev|prod, --force flag)
- [ ] 2.3 Implement credential generation functions:
  - [ ] 2.3.1 Generate database passwords (32 chars, alphanumeric)
  - [ ] 2.3.2 Generate JWT secrets (48+ chars, base64)
  - [ ] 2.3.3 Generate encryption keys for Supavisor/Vault (48 chars)
  - [ ] 2.3.4 Generate MinIO/Logflare API keys (32 chars)
- [ ] 2.4 Implement template substitution (replace placeholders with generated values)
- [ ] 2.5 Add idempotency check (don't overwrite existing .env files unless --force)
- [ ] 2.6 Add post-generation instructions (what to do next)
- [ ] 2.7 Make script executable: `chmod +x scripts/setup-env.sh`
- [ ] 2.8 Test script generates valid `.env.dev` file
- [ ] 2.9 Test script with --force flag overwrites correctly

## 3. Security Improvements
- [ ] 3.1 Update `.gitignore` to exclude all env files (`.env*`)
- [ ] 3.2 Remove duplicate gitignore entries
- [ ] 3.3 Verify `.env.dev` and `.env.prod` are properly ignored
- [ ] 3.4 Test that `git status` does not show env files
- [ ] 3.5 Add pre-commit hook check for accidentally staged env files (optional)

## 4. Documentation Updates
- [ ] 4.1 Update README.md "Getting Started" section:
  - [ ] 4.1.1 Replace manual env setup with `./scripts/setup-env.sh dev` command
  - [ ] 4.1.2 Fix `make dev` → `make dev-up` throughout
  - [ ] 4.1.3 Add security warning about env files
  - [ ] 4.1.4 Explain difference between setup-env.sh and dev_init.ts
- [ ] 4.2 Create SETUP.md with detailed instructions (optional):
  - [ ] 4.2.1 Environment variable reference table
  - [ ] 4.2.2 Troubleshooting common setup issues
  - [ ] 4.2.3 Production deployment checklist
- [ ] 4.3 Update CONTRIBUTING.md with setup instructions for contributors

## 5. Testing
- [ ] 5.1 Test fresh clone setup workflow:
  - [ ] 5.1.1 Clone repo to new directory
  - [ ] 5.1.2 Run `./scripts/setup-env.sh dev`
  - [ ] 5.1.3 Verify `.env.dev` is created
  - [ ] 5.1.4 Verify `.env.dev` is not tracked by git
  - [ ] 5.1.5 Run `make dev-up` and verify all services start
- [ ] 5.2 Test idempotency:
  - [ ] 5.2.1 Run setup script twice without --force
  - [ ] 5.2.2 Verify existing .env file is not overwritten
  - [ ] 5.2.3 Verify warning message is shown
- [ ] 5.3 Test force overwrite:
  - [ ] 5.3.1 Run `./scripts/setup-env.sh dev --force`
  - [ ] 5.3.2 Verify new secrets are generated
  - [ ] 5.3.3 Verify old .env file is backed up
- [ ] 5.4 Test production setup:
  - [ ] 5.4.1 Run `./scripts/setup-env.sh prod`
  - [ ] 5.4.2 Verify `.env.prod` is created with appropriate values
  - [ ] 5.4.3 Verify production-specific settings are correct

## 6. Security Verification
- [ ] 6.1 Verify no secrets in git history (run git log search)
- [ ] 6.2 Verify template files contain only placeholders
- [ ] 6.3 Test generated secrets have sufficient entropy
- [ ] 6.4 Verify generated JWT secrets are valid base64
- [ ] 6.5 Verify OpenSSL random generation is used (not weak PRNG)
- [ ] 6.6 Add security audit checklist to PR template

## 7. Team Onboarding
- [ ] 7.1 Create onboarding checklist for new developers
- [ ] 7.2 Update team documentation with new setup process
- [ ] 7.3 Notify existing team members about new setup script
- [ ] 7.4 Provide migration instructions for existing developers
- [ ] 7.5 Schedule team walkthrough of new setup process

## Success Criteria

- [ ] Fresh clone → running stack in <5 minutes
- [ ] No environment files can be committed to git
- [ ] All generated secrets are cryptographically secure
- [ ] Setup script works on macOS, Linux (primary development platforms)
- [ ] Zero "how do I set up env?" support requests after rollout
- [ ] README is accurate and complete

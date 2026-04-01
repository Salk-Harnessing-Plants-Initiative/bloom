# Design: Automated Environment Setup

## Context

Bloom requires 40+ environment variables for both development and production:

- Database credentials (Postgres, MinIO)
- JWT secrets for authentication
- Encryption keys for Supavisor and Vault
- API keys for Supabase, Logflare
- Service configuration (ports, URLs, feature flags)

**Previous process:**

1. Copy example file manually
2. Generate each secret manually (JWT via online tools, passwords via random.org)
3. Edit 40+ variables one by one
4. Hope you didn't miss anything
5. Debug cryptic errors if setup was incorrect

**Security incident:** `.env.dev` with actual credentials was committed to git history

## Goals / Non-Goals

**Goals:**

- Automate credential generation (0 manual work)
- Ensure cryptographically secure random values
- Prevent secrets from being committed to git
- Reduce onboarding time from 1-2 hours to <5 minutes
- Provide clear error messages if setup incomplete
- Support both dev and prod environments with same script

**Non-Goals:**

- Secret management system (like Vault) - overkill for local dev
- Cloud secret storage - adds complexity
- Environment variable validation at runtime - separate concern
- Automatic Supabase key fetching - requires auth, too complex

## Decisions

### Decision 1: Bash Script for Setup Automation

**What:** Create `scripts/setup-env.sh` bash script to generate `.env.dev`/`.env.prod`

**Why:**

- **Universal**: Bash available on all macOS/Linux (primary dev platforms)
- **Simple**: No additional runtime dependencies
- **Scriptable**: Can be run in CI/CD for automated deployments
- **Transparent**: Developers can read the script and see what it does
- **Fast**: Runs in <1 second

**Alternatives considered:**

1. **Node.js script**: Requires node, adds complexity, not simpler than bash
2. **Python script**: Requires Python, not all devs have it installed
3. **Manual instructions**: Current approach, error-prone and slow
4. **Make target**: Less portable, less clear than standalone script

### Decision 2: OpenSSL for Cryptographically Secure Random Generation

**What:** Use `openssl rand` to generate all secrets

**Why:**

- **Secure**: OpenSSL uses cryptographically secure PRNG
- **Available**: Pre-installed on macOS/Linux
- **Battle-tested**: Industry-standard cryptographic library
- **Flexible**: Can generate hex, base64, alphanumeric formats

**Generation patterns:**

```bash
# 32-character alphanumeric password
openssl rand -base64 24 | tr -d '/+=' | head -c 32

# 48-character base64 JWT secret
openssl rand -base64 64 | head -c 48

# 32-character hex key
openssl rand -hex 16
```

**Alternatives considered:**

1. **/dev/urandom**: Secure but less portable (format handling)
2. **UUID generation**: Not sufficient entropy for secrets
3. **Date-based hashes**: Predictable, insecure
4. **User-chosen passwords**: Weak, defeats automation purpose

### Decision 3: Template-Based Substitution

**What:** Use placeholder patterns in `.env.dev.example`, replace with generated values

**Placeholder patterns:**

- `CHANGEME_PASSWORD_<NAME>` - Will be replaced with 32-char password
- `CHANGEME_JWT` - Will be replaced with 48-char base64 JWT secret
- `CHANGEME_KEY_<NAME>` - Will be replaced with encryption key
- `<MANUAL: instructions>` - Requires manual input (Supabase keys)

**Why:**

- **Clear intent**: Developers know what's auto-generated vs manual
- **Simple parsing**: Easy to find/replace with bash/sed
- **Self-documenting**: Placeholder names explain purpose
- **Validation**: Can detect if manual placeholders remain

**Example template:**

```bash
POSTGRES_PASSWORD=CHANGEME_PASSWORD_POSTGRES
JWT_SECRET=CHANGEME_JWT
SUPABASE_ANON_KEY=<MANUAL: Get from Supabase dashboard>
```

**After script:**

```bash
POSTGRES_PASSWORD=x9kL2mP8nQ4rT7wY1zB5vC3hN6jM0sA9
JWT_SECRET=aGVsbG8gd29ybGQgdGhpcyBpcyBhIGp3dCBzZWNyZXQxMjM
SUPABASE_ANON_KEY=<MANUAL: Get from Supabase dashboard>
```

### Decision 4: Idempotency - Don't Overwrite Without --force

**What:** Script refuses to overwrite existing `.env.dev` unless `--force` flag provided

**Why:**

- **Safety**: Prevents accidental destruction of working configuration
- **Developer-friendly**: Doesn't disrupt existing setups
- **Explicit**: Forcing overwrite requires intentional action
- **Backup**: When forcing, back up old file as `.env.dev.backup`

**Behavior:**

```bash
# First run - creates .env.dev
./scripts/setup-env.sh dev  # ✅ Creates .env.dev

# Second run - refuses to overwrite
./scripts/setup-env.sh dev  # ⚠️ ".env.dev already exists. Use --force to overwrite."

# Force run - overwrites with backup
./scripts/setup-env.sh dev --force  # ✅ Backs up to .env.dev.backup, creates new
```

### Decision 5: Clear Post-Generation Instructions

**What:** Script prints next steps after generation (what user must do manually)

**Why:**

- **Guidance**: New developers don't have to guess next steps
- **Completeness**: Ensures all manual steps are completed
- **Context**: Explains why certain variables need manual input

**Example output:**

```
✅ .env.dev created successfully!

⚠️  MANUAL STEPS REQUIRED:

1. Get your Supabase keys from the dashboard:
   - Visit: http://localhost:55323 (Studio dashboard)
   - Copy ANON_KEY and SERVICE_ROLE_KEY
   - Add to .env.dev (search for <MANUAL: ...)

2. (Optional) Update MinIO paths if needed:
   - Default: ./minio_data
   - Change MINIO_DATA_PATH if different location

Next: Run 'make dev-up' to start the stack
```

## Implementation Details

### Script Structure

```bash
#!/bin/bash
set -euo pipefail  # Exit on error, undefined vars, pipe failures

# Functions
generate_password() { ... }
generate_jwt_secret() { ... }
generate_encryption_key() { ... }
substitute_placeholders() { ... }
print_instructions() { ... }

# Main
parse_args "$@"
check_prerequisites
create_env_file
print_success_message
```

### Placeholder Replacement Strategy

Use `sed` for simple find/replace:

```bash
sed "s/CHANGEME_PASSWORD_POSTGRES/$(generate_password)/g" .env.dev.example > .env.dev
```

**Alternative:** Use `envsubst` if more complex logic needed

## Risks / Trade-offs

### Risk: OpenSSL Not Available

**Risk:** Some minimal Linux distributions might not have OpenSSL

**Mitigation:**

- Check for OpenSSL in prerequisites
- Provide clear error message with installation instructions
- Most macOS/Linux systems have OpenSSL pre-installed
- Document OpenSSL requirement in README

### Risk: Script Generates Invalid Values

**Risk:** Generated secrets might not meet format requirements (e.g., base64 with invalid chars)

**Mitigation:**

- Test script thoroughly with all credential types
- Use well-tested OpenSSL commands (base64, hex, etc.)
- Validate generated values before writing to file
- Include format validation in script

### Risk: Windows Compatibility

**Risk:** Bash script won't work on Windows without WSL/Git Bash

**Mitigation:**

- Document that Windows requires WSL or Git Bash
- Most developers on macOS/Linux (primary platforms)
- Can create PowerShell version later if needed
- Docker Desktop for Windows includes Git Bash

### Trade-off: Automated vs Manual Supabase Keys

**Trade-off:** Script can't auto-fetch Supabase keys (requires dashboard login)

**Decision:** Keep manual for now because:

- Supabase keys are per-project, not random
- Fetching requires authentication to Supabase
- Adds complexity for minimal benefit
- Developers only need to do this once

**Future:** Could integrate with Supabase CLI if it supports key export

## Security Considerations

### Generated Secret Entropy

- **Passwords**: 32 chars × log2(62) ≈ 190 bits (62 = alphanumeric)
- **JWT secrets**: 48 chars × log2(64) ≈ 288 bits (base64)
- **Sufficient**: 128 bits considered secure for symmetric keys

### Git History Cleanup

If `.env.dev` was committed with secrets:

1. **Immediate**: Rotate all credentials in production
2. **Cleanup**: Use `git filter-branch` or BFG Repo-Cleaner to remove from history
3. **Prevention**: Update `.gitignore` (this proposal) prevents future incidents

### Secret Storage

- `.env.dev` files should NEVER be backed up to cloud services
- Add to `.gitignore_global` for personal repositories
- Use encrypted volume for production secrets (separate from this proposal)

## Success Metrics

- **Onboarding time**: <5 minutes (from clone to running stack)
- **Support tickets**: Zero env setup issues after rollout
- **Security incidents**: Zero committed secrets
- **Developer satisfaction**: >90% prefer automated setup vs manual
- **Adoption**: 100% of team uses setup script within 1 week

## Future Enhancements

1. **Secret rotation script**: Regenerate all secrets with one command
2. **Environment validation**: Check for missing/invalid variables before starting stack
3. **Supabase CLI integration**: Auto-fetch keys if possible
4. **Windows PowerShell version**: For Windows-native developers
5. **Docker integration**: Generate secrets inside container for maximum portability

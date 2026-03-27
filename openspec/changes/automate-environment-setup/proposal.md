# Automate Environment Setup with Secure Credential Generation

## Why

The project currently requires manual configuration of 40+ environment variables to run the development and production environments. This creates several critical problems:

- **Security risk**: Previously, `.env.dev` with actual credentials was committed to the repository (security incident)
- **Onboarding friction**: New developers spend 1-2 hours manually generating secure passwords, JWT secrets, and encryption keys
- **Inconsistent environments**: Manual credential generation leads to weak passwords or copy-paste errors
- **Documentation burden**: README must explain how to generate each credential type (JWT secrets, encryption keys, database passwords)
- **Setup errors**: Missing or incorrect environment variables cause cryptic failures during `make dev-up`
- **No production deployment guide**: No clear process for generating production credentials securely

These gaps lead to:

- Actual security incidents (credentials in git history)
- Frustrated new developers unable to start the stack
- Weak credentials in development (copy-paste defaults)
- Production deployment delays (manual credential generation)
- Support burden (helping developers troubleshoot env setup)

## What Changes

### 1. Environment File Templates

- Create `.env.dev.example` with placeholder values for all 40+ required variables
- Create `.env.prod.example` with placeholder values and production-specific settings
- Templates include comments explaining each variable's purpose
- No actual secrets in template files (safe to commit)

### 2. Automated Setup Script

- Create `scripts/setup-env.sh` bash script for environment generation
- Automatically generates cryptographically secure random values:
  - **Database passwords** (32 characters, alphanumeric)
  - **JWT secrets** (48+ characters, base64-encoded)
  - **Encryption keys** for Supavisor and Vault (48 characters)
  - **API keys** for MinIO, Logflare (32 characters)
- Supports both dev and prod environments: `./scripts/setup-env.sh dev|prod`
- Uses OpenSSL for cryptographically secure random generation
- Provides clear instructions for manual steps (e.g., obtaining Supabase keys)
- Idempotent: Won't overwrite existing `.env.dev` unless forced

### 3. Security Improvements

- **Update `.gitignore`**: Properly exclude `.env.dev`, `.env.prod`, `.env*` (not just `.env`)
- **Remove duplicates**: Clean up duplicate gitignore entries
- **Prevent future incidents**: Ensure env files can never be committed
- **Audit git history**: Document that sensitive commits have been removed (if needed)

### 4. Documentation Updates

- **Update README.md**:
  - Fix incorrect commands (`make dev` → `make dev-up`)
  - Add security warnings about environment files
  - Clear setup instructions: "Run `./scripts/setup-env.sh dev` first"
  - Explain difference between `setup-env.sh` (before Docker) and `dev_init.ts` (after Docker)
- **Create SETUP.md** (optional):
  - Detailed environment variable reference
  - Troubleshooting guide for common setup issues
  - Production deployment checklist

## Impact

- **Affected specs**: `development-environment` (new capability spec)
- **Affected code**:

  - **New files**:
    - `.env.dev.example` - Development environment template
    - `.env.prod.example` - Production environment template
    - `scripts/setup-env.sh` - Automated setup script
    - `SETUP.md` (optional) - Detailed setup guide
  - **Modified files**:
    - `.gitignore` - Improve env file exclusions, remove duplicates
    - `README.md` - Fix commands, add setup instructions, security warnings
  - **Deleted files**:
    - None (but `.env.dev` and `.env.prod` should never be committed)

- **Breaking changes**: None (additive only)

- **Dependencies**:

  - OpenSSL (already installed on macOS/Linux)
  - Bash 4.0+ (standard on most systems)

- **Migration required**:

  - Existing developers: Keep their `.env.dev` files (script won't overwrite)
  - New developers: Run setup script during onboarding
  - Production: Generate new `.env.prod` with script before deployment

- **Benefits**:
  - **Security**: Cryptographically secure credentials, no secrets in git
  - **Developer experience**: 1-2 hours → 2 minutes for environment setup
  - **Consistency**: All environments use strong, unique credentials
  - **Production-ready**: Same script works for prod deployment
  - **Reduced support**: Self-service setup, fewer "it doesn't work" issues
  - **Onboarding**: New developers can start in <5 minutes

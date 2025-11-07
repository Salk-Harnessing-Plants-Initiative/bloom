# Development Environment Capability Specification

## ADDED Requirements

### Requirement: Automated Environment File Generation

The project SHALL provide an automated script to generate environment files (`.env.dev`, `.env.prod`) with cryptographically secure credentials, eliminating manual credential generation.

#### Scenario: Developer generates development environment

- **WHEN** a developer runs `./scripts/setup-env.sh dev`
- **THEN** a `.env.dev` file is created in the project root
- **AND** all placeholder values are replaced with generated secrets
- **AND** passwords are 32 characters, alphanumeric, cryptographically random
- **AND** JWT secrets are 48+ characters, base64-encoded, cryptographically random
- **AND** encryption keys are 48 characters, cryptographically random
- **AND** the file is created in <2 seconds

#### Scenario: Developer generates production environment

- **WHEN** a developer runs `./scripts/setup-env.sh prod`
- **THEN** a `.env.prod` file is created in the project root
- **AND** production-specific values are used (e.g., different URLs, stricter settings)
- **AND** all secrets are generated with the same security standards as dev

#### Scenario: Script does not overwrite existing environment

- **WHEN** a developer runs the setup script and `.env.dev` already exists
- **THEN** the script displays a warning message
- **AND** the existing `.env.dev` is NOT overwritten
- **AND** the script suggests using `--force` to overwrite

#### Scenario: Script overwrites with force flag

- **WHEN** a developer runs `./scripts/setup-env.sh dev --force`
- **THEN** the existing `.env.dev` is backed up to `.env.dev.backup`
- **AND** a new `.env.dev` is created with fresh secrets
- **AND** the backup is timestamped or versioned

### Requirement: Environment File Templates

The project SHALL provide example environment files (`.env.dev.example`, `.env.prod.example`) with placeholder values and comments explaining each variable.

#### Scenario: Template files are safe to commit

- **WHEN** template files are checked into git
- **THEN** they contain only placeholder values (no actual secrets)
- **AND** placeholders follow the pattern `CHANGEME_*` or `<MANUAL: ...>`
- **AND** git pre-commit hooks do not flag template files as unsafe

#### Scenario: Template includes all required variables

- **WHEN** a developer reviews the `.env.dev.example` file
- **THEN** all 40+ required environment variables are present
- **AND** each variable has a comment explaining its purpose
- **AND** variables requiring manual input are clearly marked with `<MANUAL: ...>`
- **AND** the template structure matches the generated `.env.dev` structure

#### Scenario: Template documents variable types

- **WHEN** a developer reads the template
- **THEN** password fields are marked as `CHANGEME_PASSWORD_*`
- **AND** JWT secrets are marked as `CHANGEME_JWT`
- **AND** encryption keys are marked as `CHANGEME_KEY_*`
- **AND** manual fields are marked as `<MANUAL: instructions>`

### Requirement: Cryptographically Secure Credential Generation

The setup script SHALL generate all secrets using cryptographically secure random number generation (OpenSSL), ensuring sufficient entropy for production use.

#### Scenario: Passwords have sufficient entropy

- **WHEN** the script generates a password
- **THEN** the password is exactly 32 characters long
- **AND** the password uses alphanumeric characters (a-z, A-Z, 0-9)
- **AND** the password is generated using `openssl rand` (cryptographically secure)
- **AND** the password has ~190 bits of entropy (sufficient for security)

#### Scenario: JWT secrets have sufficient entropy

- **WHEN** the script generates a JWT secret
- **THEN** the secret is 48+ characters long
- **AND** the secret is base64-encoded
- **AND** the secret is generated using `openssl rand`
- **AND** the secret has ~288 bits of entropy

#### Scenario: Encryption keys are generated securely

- **WHEN** the script generates encryption keys (Supavisor, Vault)
- **THEN** the keys are 48 characters long
- **AND** the keys are generated using OpenSSL
- **AND** the keys meet the security requirements of the encryption libraries

### Requirement: Git Ignore for Environment Files

The project SHALL properly configure `.gitignore` to prevent any environment files containing secrets from being committed to the repository.

#### Scenario: Environment files are ignored by git

- **WHEN** a developer runs `git status` after creating `.env.dev`
- **THEN** `.env.dev` is NOT listed in untracked or modified files
- **AND** `.env.prod` is NOT listed in untracked or modified files
- **AND** `.gitignore` includes patterns to exclude all `.env*` files

#### Scenario: Template files are tracked by git

- **WHEN** a developer runs `git status` with template files
- **THEN** `.env.dev.example` IS tracked by git (safe to commit)
- **AND** `.env.prod.example` IS tracked by git (safe to commit)
- **AND** only template files with example values are committable

#### Scenario: Git prevents accidental commits

- **WHEN** a developer accidentally tries to `git add .env.dev`
- **THEN** git ignores the file and does not stage it
- **AND** the developer is protected from committing secrets

### Requirement: Clear Post-Generation Instructions

The setup script SHALL provide clear, actionable instructions after generating the environment file, guiding developers through required manual steps.

#### Scenario: Script outputs next steps

- **WHEN** the setup script completes successfully
- **THEN** a success message is displayed
- **AND** instructions for manual steps are clearly listed (e.g., "Add Supabase keys")
- **AND** the next command to run is shown (e.g., "`make dev-up`")
- **AND** links or references to documentation are provided if needed

#### Scenario: Script identifies incomplete variables

- **WHEN** the setup script generates the env file
- **THEN** it lists any variables still containing `<MANUAL: ...>` placeholders
- **AND** it provides specific instructions for each manual variable
- **AND** it warns that the stack will not start until manual steps are complete

### Requirement: Documentation Updates for Setup Process

The project SHALL update documentation (README.md, CONTRIBUTING.md) to reflect the automated setup process and deprecate manual setup instructions.

#### Scenario: README includes automated setup instructions

- **WHEN** a new developer reads the README
- **THEN** the "Getting Started" section prominently features `./scripts/setup-env.sh dev`
- **AND** manual credential generation instructions are removed
- **AND** the setup process is described as taking <5 minutes
- **AND** troubleshooting tips are provided for common issues

#### Scenario: README fixes incorrect commands

- **WHEN** a developer follows README instructions
- **THEN** commands are accurate (e.g., `make dev-up` not `make dev`)
- **AND** all command examples have been tested and verified
- **AND** deprecated commands are removed or marked as deprecated

#### Scenario: Security warnings are prominent

- **WHEN** a developer reads setup documentation
- **THEN** a clear warning about not committing `.env.dev` is displayed
- **AND** the importance of secure credential generation is explained
- **AND** consequences of committing secrets are described

### Requirement: Development Environment Startup Validation

The project SHALL validate that all required environment variables are present and correctly formatted before starting Docker services.

#### Scenario: Missing required variables are detected

- **WHEN** a developer runs `make dev-up` with incomplete `.env.dev`
- **THEN** a clear error message identifies which variables are missing
- **AND** the error message suggests running the setup script
- **AND** Docker services do not start until the issue is resolved

#### Scenario: Invalid variable formats are detected

- **WHEN** a developer runs `make dev-up` with malformed variables
- **THEN** a clear error message identifies which variables are invalid
- **AND** the error message explains the expected format
- **AND** the error message provides an example of a valid value

### Requirement: Cross-Platform Compatibility

The setup script SHALL work on macOS and Linux (primary development platforms) with minimal dependencies.

#### Scenario: Script works on macOS

- **WHEN** a developer runs the setup script on macOS
- **THEN** the script executes without errors
- **AND** OpenSSL is available (pre-installed on macOS)
- **AND** all generated secrets are valid

#### Scenario: Script works on Linux

- **WHEN** a developer runs the setup script on Linux
- **THEN** the script executes without errors
- **AND** OpenSSL is available (pre-installed on most distributions)
- **AND** all generated secrets are valid

#### Scenario: Script provides helpful error for missing dependencies

- **WHEN** the script runs on a system without OpenSSL
- **THEN** a clear error message is displayed
- **AND** installation instructions for OpenSSL are provided
- **AND** the script exits gracefully without creating a broken `.env` file

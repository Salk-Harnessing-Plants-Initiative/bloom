#!/usr/bin/env bash
# =============================================================================
# Enforce that CVE-remediation changes ship in their own PR.
#
# CVE work (Trivy suppressions, security base-image bumps) must not be folded
# into unrelated feature/fix PRs — it hides security-relevant edits in noisy
# diffs and couples a security decision to unrelated review. The unambiguous
# CVE artifact is `.trivyignore`; if a PR touches it, every other changed file
# must be part of the CVE-fix surface (Dockerfiles + dependency lockfiles).
#
# Rule:
#   If a PR modifies `.trivyignore`, it may modify ONLY:
#     - .trivyignore
#     - any Dockerfile (Dockerfile, *.Dockerfile)
#     - dependency lockfiles (package-lock.json, uv.lock, poetry.lock)
#   Any other changed file fails the check.
#
# A PR that does NOT touch `.trivyignore` is never flagged.
#
# Usage:
#   scripts/lint_cve_isolation.sh [BASE_REF]
#     BASE_REF defaults to origin/main. Pass a different ref for testing.
#
# Exit codes:
#   0  isolated (or .trivyignore untouched)
#   1  .trivyignore changed alongside non-CVE files
#   2  could not fetch BASE_REF to compare (misconfiguration)
# =============================================================================

set -euo pipefail

BASE_REF="${1:-origin/main}"

# Fetch only if it's a remote ref. Fail fast if missing — a silent fallback
# would make every PR pass the check trivially.
if [[ "$BASE_REF" == origin/* ]]; then
  remote_branch="${BASE_REF#origin/}"
  if ! git fetch origin "$remote_branch" --depth=1 2>/dev/null; then
    echo "::error title=lint_cve_isolation: cannot fetch base ref::git fetch origin ${remote_branch} failed. Cannot diff changed files."
    exit 2
  fi
fi

# Files changed on the PR side since it diverged from BASE_REF.
CHANGED=$(git diff --name-only "${BASE_REF}...HEAD")

# A change to .trivyignore is the trigger; no trivyignore change → nothing to enforce.
if ! grep -qx '\.trivyignore' <<<"$CHANGED"; then
  echo "CVE-isolation check passed (.trivyignore not modified)."
  exit 0
fi

# Whitelist of paths allowed alongside a .trivyignore change — the CVE-fix surface.
_is_cve_surface() {
  local f="$1"
  [ "$f" = ".trivyignore" ] && return 0
  case "$(basename "$f")" in
    Dockerfile | *.Dockerfile | package-lock.json | uv.lock | poetry.lock) return 0 ;;
  esac
  return 1
}

violations=()
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if ! _is_cve_surface "$f"; then
    violations+=("$f")
  fi
done <<<"$CHANGED"

if [ "${#violations[@]}" -ne 0 ]; then
  echo "::error title=CVE change not isolated::This PR modifies .trivyignore but also changes files outside the CVE-fix surface. CVE/security suppressions must ship in their own PR. Offending files:"
  for f in "${violations[@]}"; do
    echo "::error::  $f"
  done
  echo "Allowed alongside .trivyignore: Dockerfiles (Dockerfile, *.Dockerfile) and lockfiles (package-lock.json, uv.lock, poetry.lock). Move the .trivyignore change to its own PR."
  exit 1
fi

echo "CVE-isolation check passed (.trivyignore changed alongside only CVE-fix-surface files)."

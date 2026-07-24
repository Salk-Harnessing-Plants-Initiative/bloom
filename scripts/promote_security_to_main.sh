#!/usr/bin/env bash
# =============================================================================
# Select security commits that landed on `staging` but are not yet on `main`,
# and cherry-pick them onto a promotion branch off `main`.
#
# The team's flow is feature -> staging -> (periodic promotion PR) -> main.
# Security fixes (CVE bumps, Trivy suppressions) merge to staging like any other
# work and often sit there for weeks before production sees them. This script
# batches the pending security commits into one branch so a single reviewed PR
# promotes them all to main/prod.
#
# A commit counts as "security" when EITHER holds:
#   - its subject matches the security convention:
#       ci(security): | fix(security): | chore(security):
#       chore(deps): ... (CVE-NNNN | GHSA-... | PYSEC-...)
#   - it touches `.trivyignore` — the unambiguous CVE artifact (a Trivy
#     suppression). Lockfiles are deliberately NOT a trigger: they change on any
#     dependency addition, so a lockfile touch is not a security signal. Genuine
#     dependency security bumps are caught by the subject convention above.
# Merge commits are skipped; their child commits carry the real change and are
# evaluated individually, so squash-merged and old-style merged PRs both work.
#
# Usage:
#   scripts/promote_security_to_main.sh [--dry-run] [--min N]
#     --dry-run   List the selected commits and exit 0; make no branch/changes.
#     --min N     Require MORE THAN N pending security commits, else exit 3
#                 (no-op). Default 5.
#
# Env:
#   BASE_BRANCH    default: main      (promote onto this)
#   SOURCE_BRANCH  default: staging   (promote from this)
#   PROMO_BRANCH   default: promote/security-<date>  (see PROMO_DATE)
#   PROMO_DATE     date tag for the branch/PR; caller supplies for determinism.
#
# Outputs (when not --dry-run and threshold met):
#   - Leaves HEAD on a new PROMO_BRANCH with the commits cherry-picked.
#   - Writes the PR body to $PR_BODY_FILE (default: promotion_pr_body.md).
#   - Prints `promoted=<n>` and `branch=<name>` to $GITHUB_OUTPUT if set.
#
# Exit codes:
#   0  success (threshold met and branch built) OR --dry-run
#   2  misconfiguration (cannot fetch a ref)
#   3  below threshold, or nothing applied cleanly — nothing to do
# Cherry-pick conflicts are never fatal: the pick is aborted and the commit is
# listed for manual promotion, so the branch is never left mid-pick.
# =============================================================================

set -euo pipefail

BASE_BRANCH="${BASE_BRANCH:-main}"
SOURCE_BRANCH="${SOURCE_BRANCH:-staging}"
MIN="${MIN:-5}"
DRY_RUN=0
PR_BODY_FILE="${PR_BODY_FILE:-promotion_pr_body.md}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --min) MIN="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

PROMO_DATE="${PROMO_DATE:-manual}"
PROMO_BRANCH="${PROMO_BRANCH:-promote/security-${PROMO_DATE}}"

# Subject convention for security work.
_subject_is_security() {
  local subj="$1"
  # Explicit security scope: ci/fix/chore(security): ...
  echo "$subj" | grep -qiE '^(ci|fix|chore)\(security\):' && return 0
  # Dependency bump that names a vuln — a deps-scoped subject mentioning a CVE,
  # GHSA/PYSEC id, or "vuln" is a security bump; plain feature bumps don't.
  echo "$subj" | grep -qiE '^(ci|fix|chore)\(deps\):.*(CVE|GHSA-|PYSEC-|vulnerab)' && return 0
  return 1
}

# The CVE-fix surface — must stay in sync with scripts/lint_cve_isolation.sh.
_is_cve_surface() {
  case "$(basename "$1")" in
    .trivyignore | Dockerfile | *.Dockerfile | Dockerfile.* | package-lock.json | uv.lock | poetry.lock) return 0 ;;
    *) return 1 ;;
  esac
}

# A commit is auto-promotable only if EVERY file it changes is on the CVE-fix
# surface. This is the same set the isolation lint blesses: pure Trivy
# suppressions and dependency CVE bumps, nothing else. It deliberately excludes
# pre-isolation-era commits that touched .trivyignore alongside app/DB code, and
# security *code* fixes (grants, error-redaction, CodeQL) — those carry real
# behavioural change and must go through normal review, not an auto-batch.
_commit_is_pure_cve_surface() {
  local sha="$1" f any=0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    any=1
    _is_cve_surface "$f" || return 1
  done < <(git diff-tree --no-commit-id --name-only -r "$sha")
  [ "$any" = 1 ]  # a commit that changed nothing is not promotable
}

# Does this commit touch .trivyignore at all?
_touches_trivyignore() {
  git diff-tree --no-commit-id --name-only -r "$1" | grep -qx '\.trivyignore'
}

# Fetch the two refs we compare. Fail fast — a silent fallback would make the
# selection empty and look like "nothing pending".
for ref in "$BASE_BRANCH" "$SOURCE_BRANCH"; do
  if ! git fetch origin "$ref" 2>/dev/null; then
    echo "::error::cannot fetch origin/${ref}" >&2
    exit 2
  fi
done

# Candidate commits: on staging, not on main, oldest first, no merge commits.
CANDIDATES=()
while IFS= read -r sha; do
  [ -n "$sha" ] && CANDIDATES+=("$sha")
done < <(git rev-list --reverse --no-merges "origin/${BASE_BRANCH}..origin/${SOURCE_BRANCH}")

# Classify each pending commit:
#   SELECTED — security AND entirely on the CVE-fix surface: auto-promotable and
#     lint-clean when bundled. Security signal = a security/CVE subject, or a
#     .trivyignore edit (covers convention-less suppressions and reverts).
#   MANUAL   — an explicit `*(security):`/CVE-subject commit that also touches
#     non-surface files (pyproject/package.json manifest bumps, .sql, app code).
#     These need re-locking or real review, so we list them for a human rather
#     than cherry-pick them. Intent here is subject-only: a non-surface commit
#     that merely grazed .trivyignore in the pre-isolation era is not security.
SELECTED=()
MANUAL=()
for sha in ${CANDIDATES[@]+"${CANDIDATES[@]}"}; do
  subj="$(git log -1 --format=%s "$sha")"
  if _commit_is_pure_cve_surface "$sha"; then
    if _subject_is_security "$subj" || _touches_trivyignore "$sha"; then
      SELECTED+=("$sha")
    fi
  elif _subject_is_security "$subj"; then
    MANUAL+=("$sha")
  fi
done

N="${#SELECTED[@]}"
echo "Found ${N} auto-promotable security commit(s) on ${SOURCE_BRANCH} not in ${BASE_BRANCH} (+${#MANUAL[@]} needing manual review)."

if [ "$N" -gt 0 ]; then
  echo "---- auto-promotable ----"
  for sha in "${SELECTED[@]}"; do
    echo "  $(git log -1 --format='%h %s' "$sha")"
  done
  echo "----"
fi

if [ "${#MANUAL[@]}" -gt 0 ]; then
  echo "---- needs manual review (security, but touches non-CVE-surface files) ----"
  for sha in "${MANUAL[@]}"; do
    echo "  $(git log -1 --format='%h %s' "$sha")"
  done
  echo "----"
fi

if [ "$DRY_RUN" = 1 ]; then
  exit 0
fi

# Threshold: strictly MORE THAN MIN, matching "if more than 5".
if [ "$N" -le "$MIN" ]; then
  echo "Below threshold (need > ${MIN}); nothing to promote."
  exit 3
fi

# Build the promotion branch off the current base tip.
git switch -C "$PROMO_BRANCH" "origin/${BASE_BRANCH}"

# Cherry-pick each selected commit in order. Three outcomes per commit:
#   PICKED   — applied cleanly onto the promotion branch.
#   SKIPPED  — became empty (its change is already on ${BASE_BRANCH}).
#   FLAGGED  — hit a merge conflict (typically a generated lockfile whose diff
#              assumes a prior state main doesn't share). We abort just that
#              pick and list it for manual promotion, so the branch never wedges
#              and the clean commits still ship.
PICKED=()
SKIPPED=()
FLAGGED=()   # entries: "<sha>|<comma-separated conflicting files>"
for sha in "${SELECTED[@]}"; do
  if git cherry-pick -x "$sha"; then
    PICKED+=("$sha")
    continue
  fi
  # Non-zero exit is either a real conflict or an empty pick (already applied).
  conflicts="$(git diff --name-only --diff-filter=U | paste -sd, - | sed 's/,$//')"
  if [ -n "$conflicts" ]; then
    echo "note: ${sha} ($(git log -1 --format=%s "$sha")) conflicts on: ${conflicts} — flagging for manual promotion."
    git cherry-pick --abort || true
    FLAGGED+=("${sha}|${conflicts}")
    continue
  fi
  # No unmerged paths => the pick became empty because its change is already on
  # ${BASE_BRANCH} (e.g. promoted earlier). Skip it and keep going.
  echo "note: ${sha} ($(git log -1 --format=%s "$sha")) is already in ${BASE_BRANCH} — skipping."
  git cherry-pick --skip
  SKIPPED+=("$sha")
done

NPICKED="${#PICKED[@]}"
if [ "$NPICKED" -eq 0 ]; then
  echo "No commit applied cleanly (${#SKIPPED[@]} already in ${BASE_BRANCH}, ${#FLAGGED[@]} conflicted, ${#MANUAL[@]} need review); nothing to open a PR for."
  for e in ${FLAGGED[@]+"${FLAGGED[@]}"}; do
    echo "::warning::needs manual promotion (lockfile conflict): ${e%%|*} $(git log -1 --format=%s "${e%%|*}")"
  done
  for sha in ${MANUAL[@]+"${MANUAL[@]}"}; do
    echo "::warning::needs manual review (non-surface security change): ${sha} $(git log -1 --format=%s "$sha")"
  done
  git switch --detach "origin/${BASE_BRANCH}" >/dev/null 2>&1 || true
  exit 3
fi

# Compose the PR body: what's promoted, plus what still needs a human.
{
  echo "## Security promotion to \`${BASE_BRANCH}\`"
  echo
  echo "Batches **${NPICKED}** security commit(s) that landed on \`${SOURCE_BRANCH}\` but are not yet in production. Opened automatically because the pending count exceeded ${MIN}."
  echo
  echo "Each commit is a CVE remediation or dependency security bump. Commits are auto-selected because they touch **only** CVE-fix files (\`.trivyignore\` / Dockerfiles / lockfiles) — selection is by file *path*, not diff *content*, so please read the actual Dockerfile/lockfile diffs before merging rather than rubber-stamping on the \"security\" framing."
  echo
  echo "### Promoted (${NPICKED})"
  for sha in "${PICKED[@]}"; do
    echo "- \`$(git log -1 --format=%h "$sha")\` $(git log -1 --format=%s "$sha")"
  done
  if [ "${#FLAGGED[@]}" -gt 0 ] || [ "${#MANUAL[@]}" -gt 0 ]; then
    total_manual=$(( ${#FLAGGED[@]} + ${#MANUAL[@]} ))
    echo
    echo "### ⚠️ Needs manual promotion (${total_manual})"
    if [ "${#FLAGGED[@]}" -gt 0 ]; then
      echo
      echo "**Cherry-pick conflicts** — these applied onto a much older \`${BASE_BRANCH}\` and clashed on a generated/ordered file; re-apply and re-lock by hand:"
      for e in "${FLAGGED[@]}"; do
        sha="${e%%|*}"; files="${e#*|}"
        echo "- \`$(git log -1 --format=%h "$sha")\` $(git log -1 --format=%s "$sha") — conflicts: \`${files}\`"
      done
    fi
    if [ "${#MANUAL[@]}" -gt 0 ]; then
      echo
      echo "**Non-mechanical security changes** — these touch app/DB/manifest code and need real review; promote via the normal flow:"
      for sha in "${MANUAL[@]}"; do
        echo "- \`$(git log -1 --format=%h "$sha")\` $(git log -1 --format=%s "$sha")"
      done
    fi
  fi
  if [ "${#SKIPPED[@]}" -gt 0 ]; then
    echo
    echo "<sub>Skipped ${#SKIPPED[@]} commit(s) already present in \`${BASE_BRANCH}\`.</sub>"
  fi
  echo
  echo "> Generated by \`.github/workflows/promote-security-to-main.yml\`."
} > "$PR_BODY_FILE"

echo "Wrote PR body to ${PR_BODY_FILE}."

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  {
    echo "promoted=${NPICKED}"
    echo "flagged=${#FLAGGED[@]}"
    echo "branch=${PROMO_BRANCH}"
  } >> "$GITHUB_OUTPUT"
fi

echo "Promotion branch ${PROMO_BRANCH} ready: ${NPICKED} promoted, ${#FLAGGED[@]} flagged, ${#SKIPPED[@]} already in ${BASE_BRANCH}."

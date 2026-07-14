#!/usr/bin/env sh
# Bloom dev-environment preflight doctor.
#
# Inspects the developer's environment BEFORE stack bring-up and reports
# actionable findings. Dependency-light on purpose: pure POSIX sh plus coreutils
# and the CLIs it probes for (uv/node/npm/supabase/make/docker) — it must NOT
# depend on uv/python/node, because one of its checks is "are those installed?".
#
# Exit code: non-zero if any ERROR is found; 0 otherwise (advisories print but
# never fail). A hard error takes precedence over advisories. DOCTOR_SKIP=1
# short-circuits to a clean exit (used by CI, where the environment is known-good).
#
# Testability env overrides (see openspec design.md):
#   DOCTOR_REPO_PATH  path classified for the /mnt/ check   (default: repo root)
#   DOCTOR_WSL        1|0 to force WSL detection            (default: auto)
#   DOCTOR_PORT       host port probed for the in-use check (default: .env.dev / 5432)
#   DOCTOR_SCAN_ROOT  root for the CRLF scan                (default: repo root)
#   DOCTOR_MNT_PREFIX Windows-drive mount prefix            (default: /mnt/)
#   DOCTOR_PIN_FILE   supabase version pin file             (default: .supabase-version)
#
# See openspec/specs/development-environment (Preflight Environment Doctor).

set -u

if [ "${DOCTOR_SKIP:-}" = "1" ]; then
  echo "doctor: DOCTOR_SKIP=1 — skipping preflight."
  exit 0
fi

SCRIPT_DIR=$(unset CDPATH; cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=${SCRIPT_DIR%/scripts}
REPO_PATH=${DOCTOR_REPO_PATH:-$REPO_ROOT}
SCAN_ROOT=${DOCTOR_SCAN_ROOT:-$REPO_ROOT}
# The Windows-drive mount prefix. Overridable so tests can simulate a leaked
# toolchain / a /mnt repo without a real (writable) /mnt.
MNT_PREFIX=${DOCTOR_MNT_PREFIX:-/mnt/}

ERRORS=0
WARNINGS=0
CR=$(printf '\r')

err()  { printf 'ERROR: %s\n' "$1" >&2; ERRORS=$((ERRORS + 1)); }
warn() { printf 'WARN:  %s\n' "$1" >&2; WARNINGS=$((WARNINGS + 1)); }
ok()   { printf 'ok:    %s\n' "$1"; }

is_wsl() {
  if [ -n "${DOCTOR_WSL:-}" ]; then
    [ "$DOCTOR_WSL" = "1" ]
    return
  fi
  # Detect WSL from the kernel string ONLY. Do not infer WSL from a /mnt/ repo
  # path — /mnt is an ordinary mount point on native Linux too, and inferring
  # WSL from it would raise a spurious hard error on a normal Linux box whose
  # repo happens to live under /mnt (e.g. /mnt/data/repos/bloom).
  [ -r /proc/version ] && grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null
}

port_in_use() {
  _p=$1
  if command -v ss >/dev/null 2>&1; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${_p}\$"
    return
  fi
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$_p" >/dev/null 2>&1
    return
  fi
  return 1
}

echo "== Bloom environment doctor =="

# --- Check 1: repo on the Windows filesystem (/mnt/) under WSL — ERROR ---
if is_wsl; then
  case "$REPO_PATH" in
    "$MNT_PREFIX"*)
      err "Repo is on the Windows filesystem ($REPO_PATH). MinIO's /data bind mount fails there ('input/output error'). Clone into the WSL2 Linux filesystem (e.g. ~/repos/bloom) and run from there."
      ;;
    *)
      ok "repo is on the Linux filesystem ($REPO_PATH)"
      ;;
  esac
fi

# --- Check 2: required tools present (ERROR) + Windows-mount leak (WARN) ---
for tool in uv node npm supabase make docker; do
  tool_path=$(command -v "$tool" 2>/dev/null || true)
  if [ -z "$tool_path" ]; then
    err "required tool '$tool' not found on PATH. Install it inside your WSL Ubuntu (not on Windows)."
    continue
  fi
  if is_wsl; then
    case "$tool_path" in
      "$MNT_PREFIX"*)
        warn "'$tool' resolves to $tool_path — the Windows install, leaking via /mnt. Install '$tool' inside WSL Ubuntu so it shadows the Windows one."
        ;;
    esac
  fi
done

# --- Check 3: supabase CLI version vs pinned .supabase-version (WARN) ---
pin_file="${DOCTOR_PIN_FILE:-$REPO_ROOT/.supabase-version}"
if [ -r "$pin_file" ] && command -v supabase >/dev/null 2>&1; then
  pinned=$(tr -d " \t\r\n" < "$pin_file")
  # Extract the semver only — the CLI may print "supabase 2.92.1", "v2.92.1",
  # or an update-available notice line; match how CI greps for the version.
  actual=$(supabase --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)
  if [ -n "$pinned" ] && [ -n "$actual" ] && [ "$pinned" != "$actual" ]; then
    warn "supabase CLI is $actual but the repo pins $pinned (.supabase-version). Install the pinned version so 'make migrate-local' matches CI."
  fi
fi

# --- Check 4: configured POSTGRES_HOST_PORT already in use pre-bring-up (WARN) ---
port=${DOCTOR_PORT:-}
if [ -z "$port" ]; then
  if [ -r "$REPO_ROOT/.env.dev" ]; then
    port=$(sed -n 's/^POSTGRES_HOST_PORT=//p' "$REPO_ROOT/.env.dev" | head -n1 | sed 's/#.*//' | tr -d " \t\r")
  fi
  port=${port:-5432}
fi
if [ -n "$port" ] && port_in_use "$port"; then
  warn "host port $port is already in use before bring-up (a foreign Postgres — e.g. a WSL-relayed one — or your own Bloom stack is already running). If it's foreign, set POSTGRES_HOST_PORT to a free port (e.g. 5433) in .env.dev."
fi

# --- Check 5: CRLF in bind-mounted init scripts (WARN) ---
# Safety net for working trees that predate the .gitattributes LF rules; detects
# what .gitattributes prevents on a fresh clone (defense in depth).
# Scope to the actual init scripts (*.sh/*.sql) and PRUNE volumes/db/data/ — the
# live Postgres cluster bind-mount is gitignored, huge, and binary (0x0D bytes
# are normal in heap/WAL files); scanning it would be slow and flag false CRLF.
crlf_files=$(
  {
    for f in "$SCAN_ROOT"/minio/init/*.sh; do
      [ -f "$f" ] && printf '%s\n' "$f"
    done
    [ -d "$SCAN_ROOT/volumes/db" ] && find "$SCAN_ROOT/volumes/db" \
      -type d -name data -prune -o \
      -type f \( -name '*.sh' -o -name '*.sql' \) -print 2>/dev/null
  } | while IFS= read -r f; do
    [ -f "$f" ] && LC_ALL=C grep -lq "$CR" "$f" 2>/dev/null && printf '%s\n' "$f"
  done
)
if [ -n "$crlf_files" ]; then
  warn "CRLF line endings in bind-mounted init script(s): $(echo "$crlf_files" | tr '\n' ' '). These must be LF or the Linux containers fail to run them. Fix: git add --renormalize ."
fi

# --- Summary + exit ---
echo ""
if [ "$ERRORS" -gt 0 ]; then
  printf 'doctor: %d error(s), %d warning(s) — cannot continue. Fix the errors above, or set DOCTOR_SKIP=1 to bypass this preflight (e.g. a false positive).\n' "$ERRORS" "$WARNINGS" >&2
  exit 1
fi
if [ "$WARNINGS" -gt 0 ]; then
  printf 'doctor: %d warning(s) — advisory only, continuing.\n' "$WARNINGS"
else
  echo "doctor: environment looks good."
fi
exit 0

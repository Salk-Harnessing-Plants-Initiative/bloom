#!/usr/bin/env sh
# Ensure bloommcp's bind-mounted data directories exist and are writable by the
# bloommcp container's non-root runtime user, BEFORE `docker compose up` runs.
#
# Docker auto-creates a missing bind-mount source directory owned by the Docker
# daemon's user (root, on a typical Linux/WSL2/CI install). The bloommcp image
# runs as a non-root system user ('bloom'), which then cannot write into a
# root-owned host directory — breaking every tool that writes to local disk (the
# plotting tools always do; the QC/analysis tools do in fully-local storage-
# backend mode). See issue #472.
#
# Runs unconditionally as a Makefile prerequisite of `dev-up` — deliberately
# NOT part of scripts/doctor.sh, because CI runs `DOCTOR_SKIP=1 make dev-up`
# (see tests/unit/test_ci_dev_stack_smoke.py); folding this into doctor.sh would
# make CI silently skip the fix it exists to verify.
#
# Dependency-light on purpose: pure POSIX sh, no uv/python/node required.
#
# Testability override: BLOOMMCP_DATA_ROOT (default: bloommcp/data, relative to
# the repo root this script is invoked from).

set -u

ROOT="${BLOOMMCP_DATA_ROOT:-bloommcp/data}"

# A root-owned leftover can be at a leaf (SLEAP_OUT_CSV/PLOTS_DIR/
# ANALYSIS_OUTPUT) OR at $ROOT itself: if $ROOT never existed before a prior
# `docker compose up` ran without this fix, Docker's own bind-mount setup
# creates $ROOT AND every leaf as root — confirmed empirically (see this
# change's tasks.md). $ROOT can also be correctly-owned but merely narrow-mode
# (e.g. 0700, from something unrelated to Docker) — every leaf under it would
# then still get created/chmod'd fine (the owner can always write their own
# directory), and this script would report success, while the container's
# non-root user still can't traverse $ROOT to reach any leaf. So $ROOT itself
# is ensured (mkdir + chmod) explicitly below, not just implied by `mkdir -p`'s
# parent-creation.
remedy() {
  printf 'ensure_bloommcp_data_dirs: cannot make %s writable —\n' "$1" >&2
  printf '  likely root-owned from a "docker compose up" that ran before this fix existed\n' >&2
  printf '  (Docker auto-creates a missing bind-mount source, and its parent, as root).\n' >&2
  printf '  Fix: sudo chown -R $(id -u):$(id -g) %s   (or: sudo rm -rf %s && re-run make dev-up)\n' "$ROOT" "$ROOT" >&2
}

ensure_dir() {
  dir="$1"
  if [ ! -d "$dir" ]; then
    if ! mkdir -p "$dir" 2>/dev/null; then
      remedy "$dir"
      return 1
    fi
  fi
  if ! chmod 777 "$dir" 2>/dev/null; then
    remedy "$dir"
    return 1
  fi
  return 0
}

ERRORS=0

ensure_dir "$ROOT" || ERRORS=$((ERRORS + 1))

for name in SLEAP_OUT_CSV PLOTS_DIR ANALYSIS_OUTPUT; do
  ensure_dir "$ROOT/$name" || ERRORS=$((ERRORS + 1))
done

if [ "$ERRORS" -gt 0 ]; then
  printf 'ensure_bloommcp_data_dirs: %d director%s not writable — aborting before docker compose up.\n' \
    "$ERRORS" "$([ "$ERRORS" -eq 1 ] && echo y || echo ies)" >&2
  exit 1
fi

echo "ensure_bloommcp_data_dirs: SLEAP_OUT_CSV, PLOTS_DIR, ANALYSIS_OUTPUT are writable."
exit 0

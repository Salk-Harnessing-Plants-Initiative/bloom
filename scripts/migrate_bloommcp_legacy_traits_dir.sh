#!/usr/bin/env sh
# Migrates a pre-existing bloommcp/data/SLEAP_OUT_CSV directory (the pre-#477 name) to
# bloommcp/data/TRAITS_DIR in place, preserving its contents, before docker compose up
# runs. bloommcp/data/ is gitignored, so `git reset --hard` on the deploy host never
# renames it — without this, a real production/staging host's already-populated
# SLEAP_OUT_CSV would be silently orphaned: Docker would auto-create an empty TRAITS_DIR
# to satisfy docker-compose.prod.yml's renamed bind-mount, and every subsequent qc_clean
# call would fail (it always re-derives from raw input, never just on an experiment's
# first run). See issue #477.
#
# MUST run BEFORE scripts/ensure_bloommcp_data_dirs.sh in both deploy-production and
# deploy-staging — that preflight's mkdir -p would otherwise auto-create an empty
# TRAITS_DIR first, and this script's own already-migrated-or-fresh-host branch would then
# (correctly, per its own logic) skip renaming the real, populated legacy directory,
# silently orphaning it exactly as if no migration step existed at all.
#
# Testability override: BLOOMMCP_DATA_ROOT (default: bloommcp/data, relative to the repo
# root this script is invoked from) — mirrors scripts/ensure_bloommcp_data_dirs.sh.

set -u

ROOT="${BLOOMMCP_DATA_ROOT:-bloommcp/data}"
OLD="$ROOT/SLEAP_OUT_CSV"
NEW="$ROOT/TRAITS_DIR"

# Both existing is an ambiguous state (e.g. a prior manual test, or an earlier deploy that
# auto-created an empty TRAITS_DIR before this migration existed) — silently treating this
# as "already migrated" would leave real, un-migrated data in $OLD behind with no warning,
# the exact silent-misconfiguration class of bug this whole change exists to fix. Refuse
# and let a human reconcile it instead.
if [ -d "$OLD" ] && [ -e "$NEW" ]; then
  printf 'migrate_bloommcp_legacy_traits_dir: both %s and %s exist — ambiguous state.\n' "$OLD" "$NEW" >&2
  printf '  Refusing to silently discard %s. Manually compare contents, remove the stale\n' "$OLD" >&2
  printf '  directory once reconciled, then re-run this deploy.\n' >&2
  exit 1
fi

if [ -d "$OLD" ] && [ ! -e "$NEW" ]; then
  # A rename needs write permission on the CONTAINING directory ($ROOT), not the leaf
  # being renamed — the same root-owned-parent mechanism that broke the three leaf
  # directories can also apply to $ROOT itself. Fail loud with an actionable remedy
  # instead of letting a bare `mv: Permission denied` be the only signal.
  if [ ! -w "$ROOT" ]; then
    printf 'migrate_bloommcp_legacy_traits_dir: %s is not writable — cannot rename %s to %s.\n' "$ROOT" "$OLD" "$NEW" >&2
    printf '  A rename needs write permission on the containing directory, not just the leaf\n' >&2
    printf '  being renamed. Likely root-owned from a "docker compose up" that ran before this\n' >&2
    printf '  fix existed.\n' >&2
    printf '  Fix: sudo chown -R $(id -u):$(id -g) %s\n' "$ROOT" >&2
    exit 1
  fi
  mv "$OLD" "$NEW"
  printf 'migrate_bloommcp_legacy_traits_dir: migrated %s -> %s\n' "$OLD" "$NEW"
  exit 0
fi

echo 'migrate_bloommcp_legacy_traits_dir: no migration needed (already migrated or fresh host)'
exit 0

#!/usr/bin/env bash
# Idempotent installer for the bloom-weekly-backup systemd timer + service.
# Runs MANUALLY on the bloom server. See _WIKI/SCHEDULEDJOBS/weekly-backup.md
# for the rclone Box setup steps that must be done BEFORE this script runs.
set -euo pipefail

usage() {
    cat <<EOF
Usage: sudo bash $0 --env <staging|prod> [--dry-run]

  --env <staging|prod>   Required. Determines which deploy tree's .env file
                         the service reads at runtime.
  --dry-run              After install, invoke backup.py with --dry-run to
                         verify pg_dump + mc mirror succeed end-to-end
                         (skips the rclone upload to Box).
EOF
    exit 1
}

ENV_NAME=""
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENV_NAME="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done

if [[ -z "$ENV_NAME" ]]; then
    echo "ERROR: --env is required" >&2
    usage
fi
if [[ "$ENV_NAME" != "staging" && "$ENV_NAME" != "prod" ]]; then
    echo "ERROR: --env must be 'staging' or 'prod', got '$ENV_NAME'" >&2
    exit 1
fi

if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: must run with sudo" >&2
    exit 1
fi

if ! id -nG bloom-deploy 2>/dev/null | tr ' ' '\n' | grep -qw docker; then
    echo "ERROR: user 'bloom-deploy' is not in the 'docker' group." >&2
    echo "       Run: sudo usermod -aG docker bloom-deploy" >&2
    exit 1
fi

# Prerequisites for the backup script — surfaced here rather than at first
# scheduled run so the install fails fast with a clear error.
for bin in docker pg_dump mc rclone gzip; do
    # pg_dump runs inside the container, so we don't strictly need it on the
    # host — but mc, rclone, gzip, docker we do.
    if [[ "$bin" == "pg_dump" ]]; then continue; fi
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "ERROR: required binary '$bin' not on PATH for root" >&2
        echo "       Install before running this script. See WIKI for rclone." >&2
        exit 1
    fi
done

# rclone config sanity: confirm there's a remote configured for bloom-deploy.
# Box auth is interactive; this script does not attempt to set it up.
RCLONE_REMOTES=$(sudo -u bloom-deploy rclone listremotes 2>/dev/null || true)
if [[ -z "$RCLONE_REMOTES" ]]; then
    echo "ERROR: bloom-deploy has no rclone remotes configured." >&2
    echo "       Run: sudo -u bloom-deploy rclone config" >&2
    echo "       See _WIKI/SCHEDULEDJOBS/weekly-backup.md for the Box setup." >&2
    exit 1
fi

DEPLOY_DIR="/data/bloom/${ENV_NAME}"
ENV_FILE="${DEPLOY_DIR}/.env.${ENV_NAME}"

if [[ ! -d "$DEPLOY_DIR" ]]; then
    echo "ERROR: deploy dir $DEPLOY_DIR does not exist" >&2
    exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: env file $ENV_FILE does not exist" >&2
    exit 1
fi

# State dir for the temporary backup-build workspace.
# Mode 0700: only bloom-deploy can read/write. The systemd service runs as
# bloom-deploy; nothing else needs access.
STATE_DIR="/var/lib/bloom-weekly-backup"
if [[ ! -d "$STATE_DIR" ]]; then
    install -d -m 0700 -o bloom-deploy -g bloom-deploy "$STATE_DIR"
    echo "Created $STATE_DIR"
elif [[ "$(stat -c '%a' "$STATE_DIR")" != "700" ]]; then
    chmod 0700 "$STATE_DIR"
    echo "Tightened $STATE_DIR to mode 0700"
fi

# Render the unit files from the templates checked into the repo.
# Per-env naming so staging + prod can coexist on the same host —
# `bloom-weekly-backup-staging.{service,timer}` + `bloom-weekly-backup-prod.{service,timer}`.
TEMPLATE_DIR="${DEPLOY_DIR}/scheduled-jobs/weekly-backup"
UNIT_BASE="bloom-weekly-backup-${ENV_NAME}"
SERVICE_DEST="/etc/systemd/system/${UNIT_BASE}.service"
TIMER_DEST="/etc/systemd/system/${UNIT_BASE}.timer"

SERVICE_RENDERED=$(mktemp)
TIMER_RENDERED=$(mktemp)
trap 'rm -f "$SERVICE_RENDERED" "$TIMER_RENDERED"' EXIT

sed -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__DEPLOY_DIR__|${DEPLOY_DIR}|g" \
    -e "s|__ENV_NAME__|${ENV_NAME}|g" \
    "${TEMPLATE_DIR}/bloom-weekly-backup.service" > "$SERVICE_RENDERED"
sed -e "s|__ENV_NAME__|${ENV_NAME}|g" \
    "${TEMPLATE_DIR}/bloom-weekly-backup.timer" > "$TIMER_RENDERED"

UNITS_CHANGED=0
if ! cmp -s "$SERVICE_RENDERED" "$SERVICE_DEST" 2>/dev/null; then
    install -m 0644 -o root -g root "$SERVICE_RENDERED" "$SERVICE_DEST"
    echo "Wrote $SERVICE_DEST"
    UNITS_CHANGED=1
fi
if ! cmp -s "$TIMER_RENDERED" "$TIMER_DEST" 2>/dev/null; then
    install -m 0644 -o root -g root "$TIMER_RENDERED" "$TIMER_DEST"
    echo "Wrote $TIMER_DEST"
    UNITS_CHANGED=1
fi

if [[ "$UNITS_CHANGED" -eq 1 ]]; then
    systemctl daemon-reload
    echo "systemctl daemon-reload done"
fi

systemctl enable --now "${UNIT_BASE}.timer"
echo "Timer ${UNIT_BASE}.timer enabled and started"

if ! systemctl list-timers --all 2>/dev/null | grep -q "${UNIT_BASE}"; then
    echo "ERROR: ${UNIT_BASE}.timer not listed by systemctl after install" >&2
    exit 1
fi
echo "Install verified — ${UNIT_BASE}.timer is scheduled."

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "Running a dry-run backup (pg_dump + mc mirror, skipping upload)..."
    # Pass ONLY the BACKUP_* + POSTGRES_* env vars to the dry-run subprocess.
    # `set -a; source $ENV_FILE` would expose every secret the bloom stack
    # uses; extract just what backup.py reads.
    BACKUP_VARS=()
    while IFS= read -r line; do
        BACKUP_VARS+=("$line")
    done < <(grep -E '^(BACKUP_[A-Z_]+|POSTGRES_[A-Z_]+)=' "$ENV_FILE")
    sudo -u bloom-deploy env -i \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        HOME=/home/bloom-deploy \
        "${BACKUP_VARS[@]}" \
        /usr/bin/python3 \
        "${DEPLOY_DIR}/scheduled-jobs/weekly-backup/backup.py" \
        --env "${ENV_NAME}" --dry-run
    echo "Dry-run complete."
fi

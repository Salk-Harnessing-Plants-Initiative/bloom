#!/usr/bin/env bash
# Idempotent installer for the bloom-box-object-backup timer + service.
# Runs MANUALLY on the bloom server. The rclone Box remote must exist first —
# see _WIKI/SCHEDULEDJOBS/box-object-backup.md.
#
# Installing the timer does NOT start the multi-day seed. Run the seed by
# hand first (the wiki page has the command); the timer only ever has the
# weekly delta to move.
set -euo pipefail

usage() {
    cat <<EOF
Usage: sudo bash $0 --env <staging|prod> [--dry-run]

  --env <staging|prod>   Required. Selects the deploy tree whose .env file
                         the service reads at runtime.
  --dry-run              After install, plan a backup without copying, to
                         prove the DB query and bucket filter work.
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

# rclone itself is NOT required on the host — the job runs it in a container
# on the deploy network, because MinIO's S3 port is never published. What we
# do need on the host is docker, python3, and the Box remote's config file.
for bin in docker python3; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        echo "ERROR: required binary '$bin' not on PATH for root" >&2
        exit 1
    fi
done

RCLONE_CONF="/home/bloom-deploy/.config/rclone/rclone.conf"
if [[ ! -f "$RCLONE_CONF" ]]; then
    echo "ERROR: no rclone config at $RCLONE_CONF" >&2
    echo "       Box auth is interactive; this script does not attempt it." >&2
    echo "       See _WIKI/SCHEDULEDJOBS/box-object-backup.md." >&2
    exit 1
fi
if ! grep -q '^\[box\]' "$RCLONE_CONF"; then
    echo "ERROR: $RCLONE_CONF has no '[box]' remote." >&2
    echo "       Run: sudo -u bloom-deploy rclone config" >&2
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

# Ledger dir. Mode 0700: it records every object path in the deploy, which is
# not secret but is nobody else's business either.
STATE_DIR="/var/lib/bloom-box-object-backup"
if [[ ! -d "$STATE_DIR" ]]; then
    install -d -m 0700 -o bloom-deploy -g bloom-deploy "$STATE_DIR"
    echo "Created $STATE_DIR"
elif [[ "$(stat -c '%a' "$STATE_DIR")" != "700" ]]; then
    chmod 0700 "$STATE_DIR"
    echo "Tightened $STATE_DIR to mode 0700"
fi

# Per-env unit names so staging + prod can coexist on one host.
TEMPLATE_DIR="${DEPLOY_DIR}/scheduled-jobs/box-object-backup"
UNIT_BASE="bloom-box-object-backup-${ENV_NAME}"
SERVICE_DEST="/etc/systemd/system/${UNIT_BASE}.service"
TIMER_DEST="/etc/systemd/system/${UNIT_BASE}.timer"

SERVICE_RENDERED=$(mktemp)
TIMER_RENDERED=$(mktemp)
trap 'rm -f "$SERVICE_RENDERED" "$TIMER_RENDERED"' EXIT

sed -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__DEPLOY_DIR__|${DEPLOY_DIR}|g" \
    -e "s|__ENV_NAME__|${ENV_NAME}|g" \
    "${TEMPLATE_DIR}/bloom-box-object-backup.service" > "$SERVICE_RENDERED"
sed -e "s|__ENV_NAME__|${ENV_NAME}|g" \
    "${TEMPLATE_DIR}/bloom-box-object-backup.timer" > "$TIMER_RENDERED"

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
    echo "Planning a backup (no copying)..."
    # Pass ONLY the vars the job reads. `set -a; source $ENV_FILE` would hand
    # the subprocess every secret the stack owns.
    BACKUP_VARS=()
    while IFS= read -r line; do
        BACKUP_VARS+=("$line")
    done < <(grep -E '^(BACKUP_[A-Z_]+|POSTGRES_[A-Z_]+|MINIO_ROOT_[A-Z_]+)=' "$ENV_FILE")
    sudo -u bloom-deploy env -i \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        HOME=/home/bloom-deploy \
        "${BACKUP_VARS[@]}" \
        /usr/bin/python3 \
        "${TEMPLATE_DIR}/backup_objects.py" \
        --env "${ENV_NAME}" --dry-run
    echo "Dry-run complete."
fi

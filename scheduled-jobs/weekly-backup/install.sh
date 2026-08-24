#!/usr/bin/env bash
# Idempotent installer for the bloom-weekly-backup timer + service.
#
# Runs MANUALLY on the bloom server, once per environment. The rclone Box remote
# must already be configured for the deploy user — see
# _WIKI/SCHEDULEDJOBS/weekly-backup.md, which this script cannot do for you
# because Box authorisation is interactive.
set -euo pipefail

STATE_DIR="/var/lib/bloom-weekly-backup"
DEFAULT_DEPLOY_USER="bloom-deploy"

usage() {
    cat <<EOF
Usage: sudo bash $0 --env <staging|prod> --deploy-dir <path> [--deploy-user <user>] [--dry-run]

  --env <staging|prod>   Required. Selects the env file and unit names.
  --deploy-dir <path>    Required. The deploy tree holding docker-compose.prod.yml
                         and .env.<env>. Differs per host and per environment, so
                         it is never assumed.
  --deploy-user <user>   Runs the backup as this user (default: ${DEFAULT_DEPLOY_USER}).
  --dry-run              After install, run the backup once with --dry-run to prove
                         the dump and verification work, without uploading to Box.
EOF
    exit 1
}

ENV_NAME=""
DEPLOY_DIR=""
DEPLOY_USER="$DEFAULT_DEPLOY_USER"
DRY_RUN=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENV_NAME="${2:-}"; shift 2 ;;
        --deploy-dir) DEPLOY_DIR="${2:-}"; shift 2 ;;
        --deploy-user) DEPLOY_USER="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage ;;
        *) echo "Unknown arg: $1" >&2; usage ;;
    esac
done

[[ -n "$ENV_NAME" ]] || { echo "ERROR: --env is required" >&2; usage; }
[[ -n "$DEPLOY_DIR" ]] || { echo "ERROR: --deploy-dir is required" >&2; usage; }
if [[ "$ENV_NAME" != "staging" && "$ENV_NAME" != "prod" ]]; then
    echo "ERROR: --env must be 'staging' or 'prod', got '$ENV_NAME'" >&2
    exit 1
fi
if [[ "$EUID" -ne 0 ]]; then
    echo "ERROR: must run with sudo" >&2
    exit 1
fi

DEPLOY_DIR="$(cd "$DEPLOY_DIR" 2>/dev/null && pwd)" || {
    echo "ERROR: --deploy-dir does not exist or is not a directory" >&2
    exit 1
}
ENV_FILE="${DEPLOY_DIR}/.env.${ENV_NAME}"
COMPOSE_FILE="${DEPLOY_DIR}/docker-compose.prod.yml"
BACKUP_SCRIPT="${DEPLOY_DIR}/scheduled-jobs/weekly-backup/backup.py"

# Everything the scheduled run will need, checked now so a missing piece is an
# install-time error rather than a silent Sunday-night failure.
for path in "$ENV_FILE" "$COMPOSE_FILE" "$BACKUP_SCRIPT"; do
    [[ -f "$path" ]] || { echo "ERROR: required file missing: $path" >&2; exit 1; }
done

if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    echo "ERROR: user '$DEPLOY_USER' does not exist" >&2
    exit 1
fi
if ! id -nG "$DEPLOY_USER" | tr ' ' '\n' | grep -qw docker; then
    echo "ERROR: user '$DEPLOY_USER' is not in the 'docker' group." >&2
    echo "       Run: sudo usermod -aG docker $DEPLOY_USER" >&2
    exit 1
fi

for bin in docker rclone gzip python3; do
    command -v "$bin" >/dev/null 2>&1 || {
        echo "ERROR: required binary '$bin' is not on PATH" >&2
        exit 1
    }
done

# Box auth is interactive and out of this script's reach; all it can do is
# refuse to install a timer that would fail on its first upload.
if [[ -z "$(sudo -u "$DEPLOY_USER" rclone listremotes 2>/dev/null)" ]]; then
    echo "ERROR: user '$DEPLOY_USER' has no rclone remotes configured." >&2
    echo "       Run: sudo -u $DEPLOY_USER rclone config" >&2
    echo "       See _WIKI/SCHEDULEDJOBS/weekly-backup.md for the Box setup." >&2
    exit 1
fi

# Mode 0700: the working copy holds a full database dump.
if [[ ! -d "$STATE_DIR" ]]; then
    install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$STATE_DIR"
    echo "Created $STATE_DIR"
else
    chown "$DEPLOY_USER:$DEPLOY_USER" "$STATE_DIR"
    chmod 0700 "$STATE_DIR"
    echo "Reasserted ownership and mode 0700 on $STATE_DIR"
fi

# Per-env unit names so staging and prod coexist on one host.
TEMPLATE_DIR="${DEPLOY_DIR}/scheduled-jobs/weekly-backup"
UNIT_BASE="bloom-weekly-backup-${ENV_NAME}"
SERVICE_DEST="/etc/systemd/system/${UNIT_BASE}.service"
TIMER_DEST="/etc/systemd/system/${UNIT_BASE}.timer"

SERVICE_RENDERED=$(mktemp)
TIMER_RENDERED=$(mktemp)
trap 'rm -f "$SERVICE_RENDERED" "$TIMER_RENDERED"' EXIT

render() {
    sed -e "s|__ENV_FILE__|${ENV_FILE}|g" \
        -e "s|__DEPLOY_DIR__|${DEPLOY_DIR}|g" \
        -e "s|__DEPLOY_USER__|${DEPLOY_USER}|g" \
        -e "s|__STATE_DIR__|${STATE_DIR}|g" \
        -e "s|__ENV_NAME__|${ENV_NAME}|g" \
        "$1" > "$2"
}
render "${TEMPLATE_DIR}/bloom-weekly-backup.service" "$SERVICE_RENDERED"
render "${TEMPLATE_DIR}/bloom-weekly-backup.timer" "$TIMER_RENDERED"

if grep -q '__[A-Z_]*__' "$SERVICE_RENDERED" "$TIMER_RENDERED"; then
    echo "ERROR: a template placeholder was left unrendered:" >&2
    grep -Hn '__[A-Z_]*__' "$SERVICE_RENDERED" "$TIMER_RENDERED" >&2
    exit 1
fi

install -m 0644 "$SERVICE_RENDERED" "$SERVICE_DEST"
install -m 0644 "$TIMER_RENDERED" "$TIMER_DEST"
echo "Installed $SERVICE_DEST and $TIMER_DEST"

systemctl daemon-reload
systemctl enable --now "${UNIT_BASE}.timer"
echo "Enabled ${UNIT_BASE}.timer"
systemctl list-timers "${UNIT_BASE}.timer" --no-pager || true

if [[ "$DRY_RUN" -eq 1 ]]; then
    echo
    echo "Running a dry-run backup (dump + verify, no upload)..."
    # Runs the unit rather than the script directly, so the dry run exercises
    # the same env file, user, and hardening the scheduled run will use.
    systemd-run --uid="$DEPLOY_USER" --wait --collect --pipe \
        --property="EnvironmentFile=${ENV_FILE}" \
        --property="WorkingDirectory=${DEPLOY_DIR}" \
        /usr/bin/python3 "$BACKUP_SCRIPT" --env "$ENV_NAME" \
        --deploy-dir "$DEPLOY_DIR" --dry-run
    echo "Dry run complete."
fi

echo
echo "Done. Check health with:"
echo "  systemctl list-timers ${UNIT_BASE}.timer"
echo "  systemctl --failed | grep ${UNIT_BASE} || echo 'no failed backup unit'"
echo "  journalctl -u ${UNIT_BASE}.service -n 50"

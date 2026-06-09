#!/usr/bin/env bash
# Idempotent installer for the bloom-cert-monitor systemd timer + service.
# Runs MANUALLY on bloom-dev; See _WIKI/CADDY.
set -euo pipefail

usage() {
    cat <<EOF
Usage: sudo bash $0 --env <staging|prod> [--test-send]

  --env <staging|prod>   Required. Determines which deploy tree's .env file
                         the service reads at runtime.
  --test-send            After install, invoke the monitor with
                         --force-notification preflight to verify SMTP
                         delivery end-to-end.
EOF
    exit 1
}

ENV_NAME=""
TEST_SEND=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env) ENV_NAME="${2:-}"; shift 2 ;;
        --test-send) TEST_SEND=1; shift ;;
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

# State dir for the per-env JSON.
STATE_DIR="/var/lib/bloom-cert-monitor"
if [[ ! -d "$STATE_DIR" ]]; then
    install -d -m 0750 -o bloom-deploy -g bloom-deploy "$STATE_DIR"
    echo "Created $STATE_DIR"
fi

# Render the unit files from the templates checked into the repo.
TEMPLATE_DIR="${DEPLOY_DIR}/scheduled-jobs/cert-monitor"
SERVICE_DEST="/etc/systemd/system/bloom-cert-monitor.service"
TIMER_DEST="/etc/systemd/system/bloom-cert-monitor.timer"

SERVICE_RENDERED=$(mktemp)
TIMER_RENDERED=$(mktemp)
trap 'rm -f "$SERVICE_RENDERED" "$TIMER_RENDERED"' EXIT

sed -e "s|__ENV_FILE__|${ENV_FILE}|g" \
    -e "s|__DEPLOY_DIR__|${DEPLOY_DIR}|g" \
    "${TEMPLATE_DIR}/bloom-cert-monitor.service" > "$SERVICE_RENDERED"
cp "${TEMPLATE_DIR}/bloom-cert-monitor.timer" "$TIMER_RENDERED"

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

systemctl enable --now bloom-cert-monitor.timer
echo "Timer enabled and started"

# Verify install actually landed.
if ! systemctl list-timers --all 2>/dev/null | grep -q bloom-cert-monitor; then
    echo "ERROR: bloom-cert-monitor.timer not listed by systemctl after install" >&2
    exit 1
fi
echo "Install verified — timer is scheduled."

if [[ "$TEST_SEND" -eq 1 ]]; then
    echo "Sending preflight email..."
    set -a; source "$ENV_FILE"; set +a
    sudo -u bloom-deploy /usr/bin/python3 \
        "${DEPLOY_DIR}/scheduled-jobs/cert-monitor/monitor.py" \
        --force-notification preflight
    echo "Preflight email dispatched. Check recipient inboxes."
fi

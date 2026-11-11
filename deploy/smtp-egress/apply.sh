#!/usr/bin/env bash
# Lets only Bloom's mail senders reach port 25 (Salk's relay trusts bloom-dev's address).
#
# Usage: apply.sh [--print|--remove] ENV_FILE...
#   For each environment's env file (.env.prod, .env.staging), in Docker's DOCKER-USER chain:
#   accept tcp/25 from MAIL_IP, AUTH_IP and RNASEQ_POLLER_IP, drop tcp/25 from SUPANET_SUBNET.
#   Idempotent: it first removes every rule it added before (tagged "bloom-smtp-egress").
#   --print shows the iptables commands instead of running them; --remove only removes.
set -euo pipefail

TAG="bloom-smtp-egress"
CHAIN="DOCKER-USER"
mode="apply"
case "${1:-}" in
  --print) mode="print"; shift ;;
  --remove) mode="remove"; shift ;;
esac
if [ "$mode" != "remove" ] && [ "$#" -eq 0 ]; then
  echo "usage: $0 [--print|--remove] ENV_FILE..." >&2
  exit 2
fi

run() {
  if [ "$mode" = "print" ]; then
    echo "iptables $*"
  else
    iptables "$@"
  fi
}

value() {  # value KEY FILE: the KEY=... line's value, required
  local v
  v=$(grep -E "^$1=" "$2" | tail -n1 | cut -d= -f2-)
  if [ -z "$v" ]; then
    echo "$1 is not set in $2" >&2
    exit 1
  fi
  printf '%s' "$v"
}

remove_tagged() {
  if [ "$mode" = "print" ]; then
    echo "# remove every $CHAIN rule tagged $TAG"
    return
  fi
  iptables -S "$CHAIN" | grep -- "--comment $TAG" | sed 's/^-A /-D /' |
    while read -r rule; do
      # shellcheck disable=SC2086  # the rule is a list of iptables arguments
      iptables $rule
    done
}

if [ "$mode" != "print" ] && ! iptables -S "$CHAIN" >/dev/null 2>&1; then
  echo "$CHAIN chain not found: is Docker running?" >&2
  exit 1
fi

# Read and check every setting first, so a missing one stops before any rule changes.
plans=()
for env_file in "$@"; do
  subnet=$(value SUPANET_SUBNET "$env_file")
  mail=$(value MAIL_IP "$env_file")
  auth=$(value AUTH_IP "$env_file")
  poller=$(value RNASEQ_POLLER_IP "$env_file")
  plans+=("$subnet $mail $auth $poller")
done

remove_tagged
[ "$mode" = "remove" ] && exit 0

for plan in "${plans[@]}"; do
  read -r subnet mail auth poller <<<"$plan"
  # The drop goes in first and each accept is inserted above it.
  run -I "$CHAIN" -s "$subnet" -p tcp --dport 25 -m comment --comment "$TAG" -j DROP
  for sender in "$mail" "$auth" "$poller"; do
    run -I "$CHAIN" -s "$sender/32" -p tcp --dport 25 -m comment --comment "$TAG" -j ACCEPT
  done
done

# Port-25 rule: only the mail senders reach the relay

Salk's relay (`neoemex1.salk.edu:25`) trusts bloom-dev's address with no password, and every
container's traffic leaves through that address. This rule lets only `mail`, `auth` (GoTrue)
and `rnaseq-status-poller` reach port 25; every other container on an environment's internal
network is dropped. It sits in Docker's `DOCKER-USER` chain and uses the fixed addresses in
`.env.prod` / `.env.staging` (`SUPANET_SUBNET`, `MAIL_IP`, `AUTH_IP`, `RNASEQ_POLLER_IP`).

## Install (once, on bloom-dev)

1. Deploy the compose change to **staging** first. If Compose stops with an error that
   `bloom_v2_staging_supanet` needs to be recreated (its range is now pinned), recreate it in
   a quiet window: every staging container restarts once (a few minutes).
2. Check the senders have their addresses:
   `sudo docker inspect -f '{{.Name}} {{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' bloom_v2_staging-mail-1 bloom_v2_staging-auth-1 bloom_v2_staging-rnaseq-status-poller-1`
3. Install the script and the unit:
   `sudo install -m 0755 deploy/smtp-egress/apply.sh /usr/local/sbin/bloom-smtp-egress`
   `sudo install -m 0644 deploy/smtp-egress/bloom-smtp-egress.service /etc/systemd/system/`
4. Preview, then apply: `sudo bloom-smtp-egress --print /data/bloom/staging/.env.staging`, then
   `sudo systemctl daemon-reload && sudo systemctl enable --now bloom-smtp-egress`.
   The unit applies both environments; until prod is deployed with its fixed addresses, apply
   staging only by hand (`sudo bloom-smtp-egress /data/bloom/staging/.env.staging`) and enable
   the unit after step 6.
5. Check, on staging:
   - a sign-up email from `mail` and a password-reset email (GoTrue) still arrive;
   - another container can't reach the relay:
     `sudo docker exec bloom_v2_staging-bloommcp-1 python -c "import socket; socket.create_connection(('neoemex1.salk.edu', 25), 5)"`
     must time out, while the same from `bloom_v2_staging-mail-1` connects.
6. Repeat 1, 2 and 5 for prod after promotion, then enable the unit.

## Undo

`sudo systemctl disable --now bloom-smtp-egress` (its stop removes the rules), or
`sudo bloom-smtp-egress --remove`. The fixed addresses can stay; they change nothing else.

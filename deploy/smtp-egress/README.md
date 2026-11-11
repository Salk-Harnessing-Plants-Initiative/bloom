# Port-25 rule: only the mail senders reach the relay

Salk's relay (`neoemex1.salk.edu:25`) trusts bloom-dev's address with no password, and every
container's traffic leaves through that address. Only `mail`, `auth` (GoTrue) and
`rnaseq-status-poller` should send email.

Those three join a small network of their own, `mailnet`, and send all traffic leaving
bloom-dev through it (`gw_priority` in `docker-compose.prod.yml`); everything inside Bloom still
goes over `supanet`. A host firewall rule then allows port 25 only from `mailnet`'s bridge and
drops it from every other Docker network, including `cloudflared` and the Box backup job.
The rule is applied by hand on bloom-dev and is not part of the deploy.

| | prod | staging |
|---|---|---|
| `MAILNET_SUBNET` | 172.30.240.0/28 | 172.30.241.0/28 |
| `MAILNET_BRIDGE` | `bloom-mail` | `bloom-mail-stg` |

## The rule

In Docker's `DOCKER-USER` chain, which only sees container traffic, so programs on bloom-dev
itself (the cert-expiry check, CI jobs on the self-hosted runner) are not affected. Each line is
inserted at the top, so run them in this order and the accepts end up above the drops:

```
sudo iptables -I DOCKER-USER -i docker0 -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j DROP
sudo iptables -I DOCKER-USER -i br-+ -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j DROP
sudo iptables -I DOCKER-USER -i bloom-mail-stg -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j ACCEPT
sudo iptables -I DOCKER-USER -i bloom-mail -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j ACCEPT
```

The drops cover prod too, so apply this only once **both** environments run with `mailnet`.

## Rollout

1. **Staging deploy.** Only `mail`, `auth` and `rnaseq-status-poller` restart. Check their route
   leaves through `mailnet` (the gateway is `172.30.241.1`):
   `sudo docker exec bloom_v2_staging-mail-1 python -c "print(open('/proc/net/route').read())"`
   (the `00000000` destination row's gateway reads `01F11EAC`, i.e. 172.30.241.1 reversed).
2. **Staging test rule**, staging only, until prod is deployed:
   ```
   sudo iptables -I DOCKER-USER -s 172.18.0.0/16 -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j DROP
   sudo iptables -I DOCKER-USER -i bloom-mail-stg -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j ACCEPT
   ```
   Then run the checks below on staging.
3. **Prod deploy** on promotion; check the route the same way (`bloom_v2_prod-mail-1`, gateway
   `172.30.240.1`, `01F01EAC`).
4. **Full rule.** Remove the test rule (Undo below), apply the rule above, run the checks.

## Check

- `sudo iptables -S DOCKER-USER | grep bloom-smtp-egress` lists the accepts above the drops.
- A sign-up email from `mail` and a password-reset email (GoTrue) still arrive.
- Another container can't reach the relay; this must time out:
  `sudo docker exec bloom_v2_staging-bloommcp-1 python -c "import socket; socket.create_connection(('neoemex1.salk.edu', 25), 5)"`
  while the same from `bloom_v2_staging-mail-1` connects.

## After bloom-dev restarts

iptables rules are not saved: a restart of the bloom-dev server clears them (a normal deploy
does not). After one, run `sudo iptables -S DOCKER-USER | grep bloom-smtp-egress`; if it prints
nothing, apply the rule again, then run the checks.

## Undo

```
sudo iptables -S DOCKER-USER | grep bloom-smtp-egress | sed 's/^-A/-D/' | while read -r r; do sudo iptables $r; done
```

`mailnet` can stay; it changes nothing else. Reverting it restarts only the three senders.

# Port-25 rule: only the mail senders reach the relay

Salk's relay (`neoemex1.salk.edu:25`) trusts bloom-dev's address with no password, and every
container's traffic leaves through that address. A host firewall rule lets only `mail`,
`auth` (GoTrue) and `rnaseq-status-poller` reach port 25; every other container on
bloom-dev (including `cloudflared` and the Box backup job) is dropped. The rule is applied by hand on bloom-dev and is not
part of the deploy.

The senders' addresses are fixed in `.env.prod.defaults` / `.env.staging.defaults`:

| | prod | staging |
|---|---|---|
| internal network (`SUPANET_SUBNET`) | 172.19.0.0/16 | 172.18.0.0/16 |
| `mail` (`MAIL_IP`) | 172.19.0.2 | 172.18.0.2 |
| `auth` (`AUTH_IP`) | 172.19.0.3 | 172.18.0.3 |
| `rnaseq-status-poller` (`RNASEQ_POLLER_IP`) | 172.19.0.4 | 172.18.0.4 |

## The rule

In Docker's `DOCKER-USER` chain. The drops match traffic coming in from any Docker network
(`docker0` is Docker's default network, `br-+` every other one), so networks added later are
covered too. Each line is inserted at the top, so run them in this order and the accepts end up
above the drops:

```
sudo iptables -I DOCKER-USER -i docker0 -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j DROP
sudo iptables -I DOCKER-USER -i br-+ -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j DROP
for ip in 172.19.0.2 172.19.0.3 172.19.0.4 172.18.0.2 172.18.0.3 172.18.0.4; do
  sudo iptables -I DOCKER-USER -s "$ip/32" -p tcp --dport 25 -m comment --comment bloom-smtp-egress -j ACCEPT
done
```

The chain only sees container traffic, so programs running on bloom-dev itself are not affected.

## After bloom-dev restarts

iptables rules are not saved: a restart of the bloom-dev server, or of Docker on it, clears
them (a normal deploy does not). After one, run `sudo iptables -S DOCKER-USER | grep
bloom-smtp-egress`; if it prints nothing, apply the commands above again, then run
the checks below.

## Check

- `sudo iptables -S DOCKER-USER | grep bloom-smtp-egress` lists the six accepts above the two drops.
- A sign-up email from `mail` and a password-reset email (GoTrue) still arrive.
- Another container can't reach the relay; this must time out:
  `sudo docker exec bloom_v2_staging-bloommcp-1 python -c "import socket; socket.create_connection(('neoemex1.salk.edu', 25), 5)"`
  while the same from `bloom_v2_staging-mail-1` connects.

## Undo

Delete each tagged rule: run `sudo iptables -S DOCKER-USER | grep bloom-smtp-egress` and repeat
each line with `-A` changed to `-D`. The fixed addresses can stay; they change nothing else.

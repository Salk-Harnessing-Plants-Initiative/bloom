# bloom-cert-monitor

Weekly observer of Caddy's TLS cert renewal activity.

Emails the team on success, failure, or silent-expiry. Code lives in one folder: [`scheduled-jobs/cert-monitor/`](../../scheduled-jobs/cert-monitor/) — entry point [`monitor.py`](../../scheduled-jobs/cert-monitor/monitor.py), logic in [`monitor_lib.py`](../../scheduled-jobs/cert-monitor/monitor_lib.py), systemd unit templates [`bloom-cert-monitor.service`](../../scheduled-jobs/cert-monitor/bloom-cert-monitor.service) + [`bloom-cert-monitor.timer`](../../scheduled-jobs/cert-monitor/bloom-cert-monitor.timer), installer [`install.sh`](../../scheduled-jobs/cert-monitor/install.sh).

## Why this exists

Caddy renews TLS certs automatically every ~60 days via DNS-01 against Let's Encrypt. If a renewal silently fails (Cloudflare token revoked, Salk CNAME removed, plugin regression), Caddy keeps retrying internally but doesn't notify anyone. Let's Encrypt [deprecated its expiration-notification email service on 2025-06-04](https://letsencrypt.org/2025/06/26/expiration-notification-service-has-ended), so setting an `email` directive in the Caddyfile no longer triggers any actionable warning.

The previous detection path was "a browser shows `NET::ERR_CERT_DATE_INVALID` to a user." The cert renewal monitor closes that gap. Issue [#285](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/285) was the trigger.

## What it detects

The monitor reads each Caddy container's currently-serving cert and the last 7 days of its container logs, then classifies the result into one or more of four states:

| State                              | Trigger                                                                                                                                                                                                                                                                                                                 | Email urgency                                                                        |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `renewed`                        | Cert's `notBefore` advanced since the last weekly run (stored in `/var/lib/bloom-cert-monitor/<env>.json`)                                                                                                                                                                                                          | Informational. Mute via `CERT_MONITOR_NOTIFY_ON_SUCCESS=false`.                    |
| `failed`                         | Caddy log within the last 7 days contains a structured failure marker —`"logger":"tls.obtain"` + `"could not get certificate from issuer"` (primary), or `"logger":"tls"` + `"job failed"`, or `"logger":"http.acme_client"` + `"cleaning up solver"`                                                      | Urgent. Body includes the last 3 matched error lines and any retry-in-progress info. |
| `silent_expiry` (preferred path) | `now > selected_time + 7 days` AND `notBefore` unchanged AND no success event in the 7-day log window. Uses Caddy's own `selected_time` field from `tls.cache.maintenance` logs — Caddy is publicly committing to a renewal date. If that date passes and `notBefore` hasn't moved, the renewal didn't land. | Urgent.                                                                              |
| `silent_expiry` (fallback path)  | Used when no `selected_time` is available (older Caddy, log rotation, etc.): cert expires in less than `CERT_MONITOR_EXPIRY_ALERT_DAYS` (default 14) AND no success in window.                                                                                                                                      | Urgent.                                                                              |
| `partial_success`                | The env's expected count of `certificate obtained successfully` events doesn't match what's in the log window. Prod expects 2 (apex + wildcard); staging expects 1 (wildcard only).                                                                                                                                   | Urgent.                                                                              |

The detection patterns are **grounded in real Caddy log fixtures** captured during this session — see [`scheduled-jobs/cert-monitor/test_fixtures/`](../../scheduled-jobs/cert-monitor/test_fixtures/). The test suite ([`scheduled-jobs/cert-monitor/monitor_test.py`](../../scheduled-jobs/cert-monitor/monitor_test.py)) asserts against the actual `msg` strings and `logger` paths Caddy 2.11.3 emits, so an upstream format change breaks the test before it silently breaks the running monitor.

## Configuration

All knobs are env vars. Defaults live in `.env.staging.defaults` and `.env.prod.defaults`; operators can change without a code push.

| Var                                | Default                                                      | Notes                                                                                                                                                                                          |
| ---------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CERT_MONITOR_RECIPIENTS`        | `bfernando@salk.edu,eberrigan@salk.edu,nhartwick@salk.edu` | Comma-separated. Add a recipient → edit the env file → restart the timer (no code change).                                                                                                   |
| `CERT_MONITOR_SMTP_HOST`         | `neoemex1.salk.edu`                                        | Salk's IP-whitelisted relay. No auth, no STARTTLS — required by neither end.                                                                                                                  |
| `CERT_MONITOR_FROM`              | `bloom-cert-monitor@bloom-dev.salk.edu`                    | Sender address. Doesn't need to be a real mailbox.                                                                                                                                             |
| `CERT_MONITOR_EXPIRY_ALERT_DAYS` | `14`                                                       | Fallback silent-expiry threshold.                                                                                                                                                              |
| `CERT_MONITOR_STATE_DIR`         | `/var/lib/bloom-cert-monitor`                              | Per-env JSON state files live here. Created by the installer with `bloom-deploy:bloom-deploy` ownership, mode 0750.                                                                          |
| `CERT_MONITOR_NOTIFY_ON_SUCCESS` | `true`                                                     | When `true`, successful renewals send an informational email (~once per env per 60 days). When `false`, only failures and silent-expiry alerts fire. State file always updates regardless. |

## Schedule

The systemd timer fires once a week - Sundays at 09:00 server time (`OnCalendar=Sun 09:00`). Reasoning:

- Cert is 90 days valid; Caddy renews at day 60 (30 days before expiry). The renewal window is 30 days wide.
- Weekly = 4 checks during the renewal window. Worst-case detection lag is 7 days, leaving 23+ days of runway before the cert actually expires.
- Daily would be noise without proportional value; monthly might miss the window entirely.
- Sunday morning means alerts arrive in time for Monday-morning triage.

`Persistent=true` is set so missed runs (server off during the scheduled fire) catch up on next boot. The timer survives reboots via `WantedBy=timers.target`.

## Installing or reinstalling

Run as a sudoer on bloom-dev, separately for staging and prod:

```bash
cd /data/bloom/staging
sudo bash scheduled-jobs/cert-monitor/install.sh --env staging --test-send

cd /data/bloom/production
sudo bash scheduled-jobs/cert-monitor/install.sh --env prod --test-send
```

The `--test-send` flag sends one test email at install time so you know the email path works before waiting a week for the first scheduled run.

Step by step, the installer:

1. **Checks that the `bloom-deploy` account is allowed to run Docker commands.**
   * The monitor reads Caddy's logs through Docker, so without this permission it can't see anything.
2. **Creates a folder for the monitor's memory.**
   * That folder (`/var/lib/bloom-cert-monitor/`) holds one small file per environment recording what the monitor saw last week, so next week's run can tell whether the cert got renewed since last time.
3. **Generates the timer's setup files and writes them only if they're different from what's already there.**
   * The repo ships two templates under `scripts/systemd/` with placeholders like `__ENV_FILE__` and `__DEPLOY_DIR__`.
   * The installer fills those in (with the staging or prod paths) and saves the result to the standard system location for scheduled jobs (`/etc/systemd/system/`).
   * If the rendered content matches what's already on disk, nothing gets written, so a re-run is a complete no-op when nothing's changed.
4. **If anything got rewritten in step 3, tells the system to refresh its scheduled-job config.**
   * Linux's scheduler caches its config in memory; this command forces it to pick up the new file.
   * Skipped entirely when step 3 didn't actually change anything, to avoid cluttering system logs.
5. **Activates the timer.**
   * This is the line that actually makes the weekly check start happening.
   * Before this step, the two setup files just sit on disk doing nothing.
   * This command does two things in one shot:
     * (a) starts the timer right now, so the system begins counting down to the next Sunday at 09:00, and
     * (b) marks the timer as "auto-start at boot" so it survives server reboots.
6. **Confirms the timer is actually scheduled now**, by listing the system's queued-up jobs and checking that ours appears.
   * If it's missing at this point, the installer aborts loudly rather than silently leaving you with a broken install.

* Verifying it's installed and running

```bash
# Is the timer scheduled?
systemctl list-timers | grep bloom-cert-monitor

# Is the timer healthy?
systemctl status bloom-cert-monitor.timer

# What did the last run log?
journalctl -u bloom-cert-monitor.service -n 100 --no-pager

# Manual one-off invocation (sends a preflight email to confirm SMTP)
sudo -u bloom-deploy /usr/bin/python3 \
    /data/bloom/staging/scheduled-jobs/cert-monitor/monitor.py \
    --force-notification preflight
```

## Removing

```bash
sudo systemctl disable --now bloom-cert-monitor.timer
sudo rm /etc/systemd/system/bloom-cert-monitor.timer
sudo rm /etc/systemd/system/bloom-cert-monitor.service
sudo systemctl daemon-reload
# Optional, keeps state for re-install:
# sudo rm -rf /var/lib/bloom-cert-monitor
```

Related

- [Issue #285](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/285) — the gap this monitor closes
- [Issue #287](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/287) — separate cleanup of the certbot orphan (different concern)
- [PR #284](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/pull/284) — the Caddy work that proved the gap was real

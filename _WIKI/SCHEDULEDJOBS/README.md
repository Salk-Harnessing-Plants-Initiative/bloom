# Scheduled Jobs

Index of every scheduled background job that runs on bloom-dev outside the
Docker Compose stack.

These jobs run on the host (not inside containers) and are installed
manually by an operator over SSH — the GitHub Actions deploy workflow does
not touch them.

 See each job's page for the rationale and runbook.

## Active jobs

| Job                          | What it does                                                                                            | Page                                              |
| ---------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `bloom-cert-monitor.timer` | Weekly check of Caddy's TLS cert renewal activity; emails the team on success / failure / silent-expiry | [cert-renewal-monitor.md](./cert-renewal-monitor.md) |

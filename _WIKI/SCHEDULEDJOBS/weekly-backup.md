# Weekly backup (`bloom-weekly-backup.timer`)

Per-environment systemd timer that runs once a week and ships a tarball of
Bloom's state to Box for disaster-recovery purposes. Replaces the
previous "we'll figure it out when we need it" backup posture for V2.

## What gets backed up

1. **Postgres** — full `pg_dump` of the bloom database, gzipped, emitted
   via `docker exec` into the running `db-prod` container so the dump
   uses a matched server/client version.
2. **MinIO buckets** — `mc mirror` against every bucket listed in
   `BACKUP_BUCKETS` (`.env.{env}.defaults`). Each bucket lands in its
   own subdirectory under the tarball.

Both are wrapped into a single timestamped tarball
(`bloom-{env}-backup-YYYYMMDDTHHMMSSZ.tar.gz`) and pushed to a
preconfigured rclone Box remote.

## Schedule

Sunday 00:00 server time, weekly. Configured in
`bloom-weekly-backup-{env}.timer`. Adjust `OnCalendar` if needed.

## Retention

Configurable via `BACKUP_RETENTION_WEEKS` in `.env.{env}.defaults`.
After the upload succeeds, `rclone delete --min-age <weeks>d` prunes
older tarballs from the remote. Default: 8 weeks on prod, 4 weeks on
staging.

Retention failure is non-fatal — backup itself is the priority; an
operator gets paged later if Box fills up.

## Box setup (one-time, before install)

The systemd service runs as `bloom-deploy`, so the rclone remote must
be configured under that user's home dir.

```bash
# As an admin on the bloom server:
sudo -u bloom-deploy rclone config
```

In the interactive prompts:

1. **n** (new remote)
2. **name**: `box` (must match `BACKUP_RCLONE_REMOTE` in env-defaults)
3. **storage**: search/select `Box`
4. **client_id / client_secret**: leave blank to use rclone's default
   OAuth app (fine for non-commercial / lab use), OR enter values from
   your Box developer console for a dedicated app.
5. **box_sub_type**: `user` (unless you're using a Service Account)
6. **Use auto config**: `Y` if the server has a browser available;
   otherwise `N` and follow the headless instructions rclone prints
   (paste the URL into a browser on your laptop, complete the OAuth,
   paste the token back).
7. Accept the rest of the defaults.

After the config completes, verify:

```bash
sudo -u bloom-deploy rclone listremotes
# Should print: box:
sudo -u bloom-deploy rclone lsd box:
# Should list your Box root folders without errors.
```

Once `box:` resolves, install.sh's preflight check passes and the
backup can run.

## Install

Same pattern as the cert renewal monitor — run manually on the bloom
server.

```bash
# As admin:
sudo bash /data/bloom/prod/scheduled-jobs/weekly-backup/install.sh --env prod

# After install, exercise the pipeline without burning a real backup:
sudo bash /data/bloom/prod/scheduled-jobs/weekly-backup/install.sh --env prod --dry-run
```

The installer:

1. Validates `--env` and sudo
2. Confirms `docker`, `mc`, `rclone`, `gzip` are on PATH for root
3. Confirms `bloom-deploy` has at least one rclone remote configured
4. Creates `/var/lib/bloom-weekly-backup` (mode 0700, owned by
   `bloom-deploy`) for the temporary backup workspace
5. Renders `bloom-weekly-backup-{env}.service` and `.timer` from the
   templates by substituting `__ENV_NAME__`, `__ENV_FILE__`,
   `__DEPLOY_DIR__`
6. Reloads systemd if the unit files changed
7. Enables and starts the timer
8. Verifies the timer appears in `systemctl list-timers`

Per-env naming (`bloom-weekly-backup-staging` vs `-prod`) lets staging
and prod coexist on the same host.

## Manually trigger a backup

```bash
# Force one run now without waiting for Sunday:
sudo systemctl start bloom-weekly-backup-prod.service

# Watch logs:
sudo journalctl -u bloom-weekly-backup-prod.service -f
```

## Restoring from a backup

The tarball contains:

```
bloom-prod-backup-20260628T000000Z.tar.gz
├── postgres-postgres.sql.gz       ← full pg_dump (gzipped)
└── minio/
    ├── images/
    ├── videos/
    ├── scrna/
    └── ...
```

To restore the database:

```bash
# Stream the gzipped dump back into the running container:
gunzip -c postgres-postgres.sql.gz | \
  docker exec -i bloom_v2_prod-db-prod-1 psql -U supabase_admin -d postgres
```

To restore a MinIO bucket:

```bash
mc mirror --overwrite ./minio/images/ local/images/
```

(Or full restore: iterate over every subdirectory of `minio/`.)

**Always restore to a fresh stack, then validate, before pointing
production traffic at it.** Restoring in-place on a live stack is
dangerous and not the intended use of these backups.

## Config surface (env-defaults)

| Var | Default (prod) | Notes |
| --- | --- | --- |
| `BACKUP_BUCKETS` | `bloom-storage,images,...` | Comma-separated list of MinIO buckets to snapshot |
| `BACKUP_MC_ALIAS` | `local` | The `mc` alias for the in-container MinIO (set by `minio/init/create-buckets.sh`) |
| `BACKUP_RCLONE_REMOTE` | `box` | Must match the name used in `rclone config` |
| `BACKUP_RCLONE_DEST_DIR` | `bloom-backups/prod` | Path inside Box where tarballs land |
| `BACKUP_RETENTION_WEEKS` | `8` (prod) / `4` (staging) | How long to keep old tarballs on Box |
| `BACKUP_STATE_DIR` | `/var/lib/bloom-weekly-backup` | Where the backup is assembled before upload |

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Clean backup uploaded to Box |
| 1 | Subprocess failure (`pg_dump` / `mc` / `rclone`) |
| 2 | Configuration error (missing env var, no rclone remote) |
| 3 | Upload succeeded but retention prune failed |

## Operational notes

- The backup uses `docker exec` to invoke `pg_dump` inside the running
  postgres container. If the container is down at the scheduled time,
  the backup fails with exit code 1 — the timer's `Persistent=true`
  will re-run on next boot, but won't catch up missed runs.
- `mc mirror --overwrite` copies the **current** bucket contents at
  the moment the timer fires — it's a snapshot, not an incremental.
- The tarball lives in `/var/lib/bloom-weekly-backup/` only during the
  run; on success it's removed and only the remote (Box) copy
  persists.

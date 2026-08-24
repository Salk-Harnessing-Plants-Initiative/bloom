# Weekly Postgres backup to Box

A per-environment systemd timer dumps the Supabase Postgres database once a
week and pushes it to Box via rclone.

Scope is the **database only**. MinIO object storage is backed up separately as
an object-level rclone sync.

## What a run produces

Two artifacts per run, sharing one UTC timestamp:

| Artifact                           | Contents                                                                                |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| `postgres-<db>-<timestamp>.sql.gz` | The whole database: schema, data, ownership, `GRANT`s                                   |
| `globals-<timestamp>.sql.gz`       | `pg_dumpall --globals-only` — the roles the dump's `OWNER`/`GRANT` statements reference |

Two files rather than one because roles live outside any single database. A
dump taken with `--no-owner --no-privileges` restores into a database whose
tables are owned by whoever ran `psql` and whose grants are gone — the RLS
policies survive, but the roles they name no longer have the access those
policies assume. Bloom manages grants as a tracked capability, so that loss is
not acceptable in a backup. **Restore globals first, then the database.**

## Schedule and retention

|             | staging                                | production                             |
| ----------- | -------------------------------------- | -------------------------------------- |
| Runs        | Sunday 02:00 server time (±15m jitter) | Sunday 02:00 server time (±15m jitter) |
| Retention   | 4 weeks                                | 8 weeks                                |
| Destination | `box:bloom-backups/staging`            | `box:bloom-backups/prod`               |

`Persistent=true` — a run missed because the host was down happens on the next
boot rather than being skipped.

Retention prunes only **after** a verified upload, so a run that cannot produce
a good dump can never age out the good copies already on Box.

## One-time setup

Per environment, on the bloom server.

### 1. Configure the Box remote for the deploy user

The timer runs as the deploy user, so the remote must belong to that user, not
to root. Box authorisation is interactive — the installer cannot do this step.

```bash
sudo -u bloom-deploy rclone config
# n) New remote
# name> box
# Storage> box
# ...complete the browser authorisation...
sudo -u bloom-deploy rclone lsd box:
```

The remote name must match `BACKUP_RCLONE_REMOTE` in the environment's
`.env.<env>` file (default `box`).

### 2. Run the installer

`--deploy-dir` is the deploy tree for that environment, which differs per host —
it is never assumed. It is the directory holding `docker-compose.prod.yml` and
`.env.<env>`.

```bash
sudo bash <deploy-dir>/scheduled-jobs/weekly-backup/install.sh \
  --env staging \
  --deploy-dir <deploy-dir> \
  --dry-run
```

The installer refuses to proceed unless the deploy directory, env file, compose
file and backup script all exist, the deploy user exists and is in the `docker`
group, `docker`/`rclone`/`gzip`/`python3` are on `PATH`, and the deploy user has
an rclone remote. Each of those would otherwise be a silent failure on the first
Sunday run.

`--dry-run` then performs a real dump and verification without uploading. Do
this before enabling production.

Re-running the installer is safe; it converges rather than duplicating units.

## Checking it is working

```bash
systemctl list-timers bloom-weekly-backup-staging.timer
systemctl --failed | grep bloom-weekly-backup    # nothing = healthy
journalctl -u bloom-weekly-backup-staging.service -n 50
sudo -u bloom-deploy rclone lsl box:bloom-backups/staging
```

A failed run leaves its unit in a failed state, so it shows up in
`systemctl --failed` without anyone reading the journal.

Every run logs each artifact's byte size. A sudden shrink is the signal worth
watching — it is how a partial dump announces itself.

### Exit codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | Verified backup uploaded                             |
| 1    | Subprocess failed (docker / pg_dump / gzip / rclone) |
| 2    | Configuration problem, or the stack is not running   |
| 3    | Backup uploaded, but the retention prune failed      |

Exit 3 is deliberately distinct: the backup is safe, only the housekeeping
failed.

## What is verified before an upload counts

The failure this job is built around is not a crash — it is uploading a
0-byte or truncated dump every week and finding out during an outage. Before
anything is uploaded:

- both processes in the `pg_dump | gzip` pipeline must exit 0 — a shell
  pipeline reports only the last, which is how a failed dump wrapped in valid
  gzip passes for a good backup;
- `gzip -t` must pass on each artifact;
- each artifact must clear a minimum-size floor;
- the size is logged.

Any failure ends the run without uploading and without pruning.

## Restore

> Restore into a scratch database first and compare row counts. Do not restore
> over a live database as a first move.

### 1. Fetch the pair you want

```bash
sudo -u bloom-deploy rclone lsl box:bloom-backups/prod
sudo -u bloom-deploy rclone copy \
  box:bloom-backups/prod/globals-<timestamp>.sql.gz /tmp/restore/
sudo -u bloom-deploy rclone copy \
  box:bloom-backups/prod/postgres-postgres-<timestamp>.sql.gz /tmp/restore/
```

Use artifacts with the **same timestamp**. A database dump restored against a
different run's globals can reference a role that dump does not define.

### 2. Globals first

The roles must exist before the database dump's `OWNER` and `GRANT` statements
run, or every one of them fails.

```bash
gunzip -c /tmp/restore/globals-<timestamp>.sql.gz \
  | docker exec -i <db-container> psql -U supabase_admin -d postgres
```

Role definitions are cluster-wide. Restoring globals onto a cluster that already
has these roles reports "role already exists" for each — expected and harmless
when restoring into the same cluster. Into an empty cluster they are created.

### 3. Then the database

```bash
gunzip -c /tmp/restore/postgres-postgres-<timestamp>.sql.gz \
  | docker exec -i <db-container> psql -U supabase_admin -d <target-db>
```

Resolve `<db-container>` the way the backup does, rather than guessing its name:

```bash
cd <deploy-dir>
docker compose -f docker-compose.prod.yml --env-file .env.<env> ps -q db-prod
```

### 4. Confirm the restore

Check row counts on the tables that matter, and confirm the grants survived —
that being the reason both artifacts exist:

```sql
SELECT count(*) FROM cyl_scans;
SELECT count(*) FROM cyl_traits;
SELECT grantee, privilege_type
  FROM information_schema.role_table_grants
 WHERE table_name = 'cyl_scans';
```

Empty grant output means the restore lost the security model — the globals step
was skipped or failed.

### 5. Clean up

```bash
rm -rf /tmp/restore
```

The dump contains `auth.users`. Do not leave it on disk.

## Notes on what this does not do

- **No point-in-time recovery.** Weekly full dumps. Anything written since the
  last Sunday is not covered. WAL archiving is a materially larger change.
- **No encryption beyond Box's own.** The dump holds `auth.users` — email
  addresses and password hashes — and lands on institutional Box under an
  account the lab controls, the same trust boundary the data already sits
  behind. Changing this means an rclone `crypt` remote wrapping the Box remote,
  plus somewhere durable to keep the passphrase; without that, the backups
  become the thing that gets lost.
- **Not installed by the deploy.** The timer is installed by hand, once per
  environment. Re-run the installer after a change to the unit templates.

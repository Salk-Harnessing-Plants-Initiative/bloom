# Weekly Postgres backup to Box

The **Weekly Postgres backup** GitHub Actions workflow dumps the Supabase
Postgres database once a week and pushes it to Box via rclone.

The workflow SSHes to the deploy host and runs
`scheduled-jobs/weekly-backup/backup.py` there, the same way `deploy.yml`
reaches the server. Nothing is dumped onto the runner and no database port is
opened.

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

## Schedule

|             |                                                           |
| ----------- | --------------------------------------------------------- |
| Runs        | Sunday 02:17 UTC, automatically, against **production**   |
| Staging     | On demand only — dispatch the workflow and pick `staging` |
| Destination | `box:bloom-backups/prod` and `box:bloom-backups/staging`  |

Run it by hand any time from the Actions tab: **Weekly Postgres backup → Run
workflow**, choose the environment, optionally tick "dry run" to dump and verify
without uploading.

## Nothing is ever deleted on Box

This job uploads and nothing else. It does not delete, overwrite, or age out
anything in the Box folder. Old backups stay until someone removes them by
hand, deliberately.

Two consequences worth knowing:

- The folder grows by two files a week, forever. Check on it occasionally and
  prune by hand when you want to; if the Box quota ever fills, the upload is
  what starts failing.
- On the server nothing accumulates at all. Each run works inside a temporary
  directory that is removed on every exit path, success or failure, so the dump
  never lingers on disk.

## One-time setup

### 1. Configure the Box remote on the deploy host

The backup runs on the server as the deploy user, so the rclone remote must
belong to that user. Box authorisation is interactive, so this is done by hand,
once per host.

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

### 2. Create the `production-scheduled-backup` GitHub Environment

Settings → Environments → New environment, named exactly
`production-scheduled-backup`. **Leave it with no required reviewers and no wait
timer.**

This exists because a scheduled run routed through `production`'s own approval
gate would sit "Waiting" for an approval nobody gives at 02:00 on a Sunday — the
backup would look configured and silently never run. Manual dispatches still go
through the real `production` environment and its gates.

### 3. Promote the workflow to `main`

Neither `schedule:` nor `workflow_dispatch` fires until the workflow file exists
on the repo's **default branch**. Both triggers are gated on it. Until the
normal staging → main promotion carries this file across, nothing runs and the
workflow does not appear in the Actions tab at all.

### 4. Prove it on staging first

Dispatch the workflow against `staging` with "dry run" ticked. That performs a
real dump and verification without uploading. Then run it for real, and do the
restore rehearsal below before relying on production.

## The weekly check

Open the Actions tab, click the latest **Weekly Postgres backup** run, and read
the summary at the top of the page. Each run writes one, whether it succeeded or
failed. It shows:

- whether the run succeeded, stated plainly;
- this run's artifacts and their **byte sizes**;
- the current contents of the Box folder, so a missing week is visible.

The size line is the one worth a second of attention. A dump that suddenly
shrinks is how a partial backup announces itself, and it is much easier to spot
across a list than inside a log.

A failed run marks the workflow run red in the Actions tab and notifies you
through whatever GitHub notification settings you already have — no separate
alerting to set up.

To check from the server instead:

```bash
sudo -u bloom-deploy rclone lsl box:bloom-backups/prod
```

### Exit codes

| Code | Meaning                                              |
| ---- | ---------------------------------------------------- |
| 0    | Verified backup uploaded                             |
| 1    | Subprocess failed (docker / pg_dump / gzip / rclone) |
| 2    | Configuration problem, or the stack is not running   |

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

Any failure ends the run without uploading anything.

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
- **Nothing starts until the workflow reaches `main`.** Both triggers are gated
  on the default branch, so a backup that "isn't running yet" is usually a
  pending promotion, not a bug.

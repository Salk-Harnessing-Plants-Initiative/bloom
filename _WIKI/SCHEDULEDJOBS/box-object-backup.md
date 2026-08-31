# Box object mirror — storage objects, browsable

Copies every Supabase Storage object to Box **under the path the Storage API
serves it from**, so `images/<experiment>/<plate>/<frame>.png` on Box is a
real `.png` you can preview in the browser.

## Why this job exists

The Storage API keeps all buckets inside one MinIO bucket (`bloom-storage`),
under a tenant prefix (`storage-single-tenant`),
and appends a version UUID to every key:

```
MinIO           bloom-storage/storage-single-tenant/images/exp-42/plate-7/frame_0001.png/0f8b1c2a-…
Storage API     images/exp-42/plate-7/frame_0001.png
```

Copying MinIO straight to Box reproduces the left-hand layout: the last path
segment is a UUID, the file has no extension, and Box shows an unpreviewable
blob inside a folder named `frame_0001.png`. This job reads
`storage.objects` — which holds the logical name **and** its current version
— and copies each object to the right-hand path instead.

It is the bytes half of the backup. The Postgres dump is the metadata half;
neither is a complete restore on its own. See "Restoring" below.

## Prerequisites (one time, on the server)

1. **Box remote for `bloom-deploy`.** Box auth is interactive; nothing
   automates it.
   ```bash
   sudo -u bloom-deploy rclone config    # n) new remote → name: box → Box
   sudo -u bloom-deploy rclone lsd box:  # must list your Box root
   ```
   The token lands in `/home/bloom-deploy/.config/rclone/rclone.conf`. The
   job mounts that file **read-only** into the rclone container, so if the
   token ever expires, refresh it by hand:
   `sudo -u bloom-deploy rclone config reconnect box:`.

2. **Create the destination folder** in Box matching `BACKUP_BOX_ROOT`
   (`Bloom-Backups/prod/storage` by default).

3. `rclone` is **not** needed on the host — the job runs it in a container on
   the deploy's `supanet`, because MinIO's S3 port is never published beyond
   that network.

## Install

```bash
sudo bash /data/bloom/prod/scheduled-jobs/box-object-backup/install.sh \
    --env prod --dry-run
```

This installs `bloom-box-object-backup-prod.{service,timer}` (Saturday
02:00) and, with `--dry-run`, plans a backup without copying anything so you
can see the object count before committing to a transfer.

Installing the timer does **not** kick off the initial seed — see below.

## The seed run

The first run has to move everything, which for prod is on the order of
millions of objects. Box throttles per-user API calls and every file costs at
least one call, so the seed is measured in days, not hours. **Run it by
hand in a detached session**, then let the timer handle the weekly delta:

```bash
sudo -u bloom-deploy tmux new -s box-seed
set -a; source /data/bloom/prod/.env.prod; set +a
python3 /data/bloom/prod/scheduled-jobs/box-object-backup/backup_objects.py \
    --env prod --full --verify 50
```

Start smaller to prove the path end to end first:

```bash
… backup_objects.py --env prod --buckets images --limit 20 --verify 20
```

Then open the Box folder and confirm the images preview.

The run is **resumable**: every successful copy is recorded in the SQLite
ledger at `/var/lib/bloom-box-object-backup/ledger.db`, so an interrupted
seed picks up where it stopped. Re-running costs one query, not one Box call
per already-copied object — the ledger, not the Box listing, is what the plan
is built against.

## Weekly behaviour

Each scheduled run enumerates only objects whose `updated_at` is newer than
the **start** of the last clean run, so the delta query stays small and an
object written mid-run is re-checked next week rather than missed. An object
overwritten in Supabase gets a new version UUID; the ledger notices the
mismatch and re-copies it, overwriting the Box copy.

Deletions do **not** propagate. This is a `copy`, never a `sync` — a
mistaken `DELETE` in Bloom cannot take the Box copy with it.

## What gets skipped, and why

The job refuses to mangle a path to make it fit Box. Objects are skipped —
and logged, with a reason — when the logical name contains a character Box
rejects (`\ : * ? " < > |`), has a path segment ending in a space or period,
holds a control character, or exceeds Box's per-file cap. `tus-files` (the
scratch bucket for in-flight resumable uploads) is excluded by default.

Check for skips after a run:

```bash
journalctl -u bloom-box-object-backup-prod.service | grep skipping
```

## Monitoring

```bash
systemctl list-timers | grep box-object-backup
journalctl -u bloom-box-object-backup-prod.service -n 100
```

Exit codes: `0` clean, `1` some objects failed after retries, `2`
configuration or preflight error, `3` interrupted (progress kept).

Progress lines report objects/second and a projected finish. Failures are
retried with backoff — Box's 429s and 5xx are treated as transient; a 404 on
the MinIO side is not, and is reported.

### Run reports on Box

Every run drops a dated JSON report in `<BACKUP_BOX_ROOT>/_runs/`, named
`2026-08-31T021703Z-prod-run00042.json`. This is the only view of the
backup's history that does not need server access: a missing week shows up as
a gap in a Box folder listing.

That matters because neither of the other two records answers the question on
its own. The mirror holds current state, so a week where nothing changed looks
exactly like a week where nothing ran. The ledger's `runs` table does know the
difference, but it lives in `/var/lib` behind SSH and SQLite.

Each report carries the run's outcome (`ok`, `partial`, `error`), its
duration, the counts (`listed`, `copied`, `failed`, `skipped`,
`already_current`), and the paths of failed objects — capped, with
`failure_count` keeping the true total. Reports are written for failed runs
too.

The upload is best-effort: the objects are already on Box, so a failed report
upload does not fail the run. A copy is always kept on the host under
`<state-dir>/_runs/`, so the record survives either way.

The same history, locally:

```bash
sqlite3 /var/lib/bloom-box-object-backup/ledger.db \
  "SELECT started_at, finished_at, outcome, stats FROM runs ORDER BY id DESC LIMIT 10;"
```

## Restoring

Restoring a single file is a download from Box. Restoring the deploy needs
both halves, in this order:

1. Restore the Postgres dump. That brings back `storage.objects`, including
   each object's `version`.
2. For each row, upload the Box copy at `<bucket_id>/<name>` back to MinIO
   at `<BACKUP_MINIO_BUCKET>/<BACKUP_MINIO_PREFIX>/<bucket_id>/<name>/<version>`
   — with the deployed defaults, that is
   `bloom-storage/storage-single-tenant/<bucket_id>/<name>/<version>`.

   **Rows with a NULL `version` take no suffix**, matching what the job read:
   `…/<bucket_id>/<name>`. Appending a version to those creates an object
   storage-api cannot see.

Step 2 is the mirror of what this job does — the version suffix comes from
the restored row, which is exactly why the dump and the object mirror are
only useful together.

Both path components are configuration, not constants, and the job verifies
them against a real object at startup before copying anything. A restore must
use the same two values the backup ran with; they are recorded in every run
report under `_runs/`.

There is no restore tooling yet. Doing this for 8M rows needs a script, and
writing it is tracked separately — as is a round-trip drill proving one object
survives MinIO → Box → MinIO and is still served by storage-api.

## Configuration

Set in `.env.<env>` (defaults in `.env.prod.defaults` / `.env.staging.defaults`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKUP_MINIO_BUCKET` | `bloom-storage` | The single MinIO bucket storage-api writes into (`STORAGE_S3_BUCKET` in the compose file). **Required** — an empty value makes rclone read each object's own `bucket_id` as a bucket name and every copy 404s. |
| `BACKUP_MINIO_PREFIX` | `storage-single-tenant` | Tenant prefix storage-api files objects under. Not declared anywhere else in the stack — it is storage-api's own default. |
| `BACKUP_BOX_REMOTE` | `box` | Name of the rclone remote |
| `BACKUP_BOX_ROOT` | `Bloom-Backups/BloomV2-Data-Backup/prod/storage` | Folder on Box to mirror into |
| `BACKUP_WORKERS` | `8` | Concurrent copies; lower it if Box throttles hard |
| `BACKUP_BWLIMIT` | *(unset)* | rclone bandwidth cap, e.g. `20M` |
| `BACKUP_STATE_DIR` | `/var/lib/bloom-box-object-backup` | Ledger location |
| `BACKUP_RC_PORT` | `5572` | Loopback port for the rclone daemon |

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB`
come from the same `.env` file; the job passes MinIO's credentials to rclone
inline so they never land in a config file on disk.

`BACKUP_MINIO_BUCKET` and `BACKUP_MINIO_PREFIX` are checked against a real
object before any copying starts: the run stats one object the manifest names
and aborts with the exact path it tried if MinIO does not hold it there. A
wrong value in an env file is as fatal as a wrong constant in the code — what
makes it survivable is failing in seconds rather than after a multi-day seed
that 404s everything and leaves an empty mirror.

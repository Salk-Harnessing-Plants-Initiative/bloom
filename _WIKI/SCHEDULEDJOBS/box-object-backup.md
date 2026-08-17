# Box object mirror — storage objects, browsable

Copies every Supabase Storage object to Box **under the path the Storage API
serves it from**, so `images/<experiment>/<plate>/<frame>.png` on Box is a
real `.png` you can preview in the browser.

## Why this job exists

The Storage API keeps all buckets inside one MinIO bucket, `bloom-storage`,
and appends a version UUID to every key:

```
MinIO           bloom-storage/images/exp-42/plate-7/frame_0001.png/0f8b1c2a-…
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

## Restoring

Restoring a single file is a download from Box. Restoring the deploy needs
both halves, in this order:

1. Restore the Postgres dump. That brings back `storage.objects`, including
   each object's `version`.
2. For each row, upload the Box copy at `<bucket_id>/<name>` back to MinIO
   at `bloom-storage/<bucket_id>/<name>/<version>`.

Step 2 is the mirror of what this job does — the version suffix comes from
the restored row, which is exactly why the dump and the object mirror are
only useful together.

## Configuration

Set in `.env.<env>` (defaults in `.env.prod.defaults` / `.env.staging.defaults`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKUP_BOX_REMOTE` | `box` | Name of the rclone remote |
| `BACKUP_BOX_ROOT` | `Bloom-Backups/prod/storage` | Folder on Box to mirror into |
| `BACKUP_WORKERS` | `8` | Concurrent copies; lower it if Box throttles hard |
| `BACKUP_BWLIMIT` | *(unset)* | rclone bandwidth cap, e.g. `20M` |
| `BACKUP_STATE_DIR` | `/var/lib/bloom-box-object-backup` | Ledger location |
| `BACKUP_RC_PORT` | `5572` | Loopback port for the rclone daemon |

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB`
come from the same `.env` file; the job passes MinIO's credentials to rclone
inline so they never land in a config file on disk.

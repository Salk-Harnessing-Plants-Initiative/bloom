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
   (`Bloom-Backups/BloomV2-Data-Backup/prod/storage` by default).

3. **State directory**, once, as root. The ledger lives here and it is what
   makes the seed resumable:
   ```bash
   sudo install -d -m 0700 -o bloom-deploy -g bloom-deploy \
       /var/lib/bloom-box-object-backup
   ```

4. **`bloom-deploy` must be in the `docker` group** — the job reaches Postgres
   and MinIO through the deploy's containers.
   ```bash
   id -nG bloom-deploy | tr ' ' '\n' | grep -qw docker || \
       sudo usermod -aG docker bloom-deploy
   ```

5. `rclone` is **not** needed on the host — the job runs it in a container on
   the deploy's `supanet`, because MinIO's S3 port is never published beyond
   that network. (A host `rclone` is still handy for step 1 and for spot
   checks.)

## Scheduling

`.github/workflows/box-object-backup.yml` runs it: GitHub Actions triggers on
a schedule, SSHes to the deploy host, and the work happens there — the same
shape as `weekly-backup.yml` and `deploy.yml`. There is nothing to install on
the server beyond the prerequisites above.

Every night at 02:17 UTC, so at most a day's scans exist only in MinIO rather
than up to a week's. It still lands ahead of the Sunday Postgres dump, so every
row in that
dump has bytes already on Box behind it.

Actions was chosen over a systemd timer because a failed run then surfaces
through notifications people already read, whereas `systemctl --failed` only
reports to whoever thinks to look.

**Seed before promoting to `main`.** The first scheduled run with an empty
ledger has no watermark to work from, so it enumerates everything — all 8M
objects — inside a job that GitHub kills at 240 minutes. It would fail every
night until someone intervened. Once a seed is under way the lock makes the
scheduled job stand down, but the lock cannot help if no seed has started.

Safe order: merge to `staging` → deploy → dry run → smoke test → seed by hand
over several nights → then promote to `main`.

**The workflow cannot fire until this file reaches `main`.** GitHub honours
`schedule:` and `workflow_dispatch` only from the default branch, so merging
to `staging` is not enough — the normal staging → main promotion has to carry
it across. Until then, nothing runs on a schedule.

To run it by hand: Actions → *Nightly Box object mirror* → **Run workflow**.
Production is the only target — there is no environment to choose. The two
inputs are `dry_run` and `verify`.

## The seed run

The first run has to move everything, which for prod is on the order of
millions of objects. Box throttles per-user API calls and every file costs at
least one call, so the seed is measured in days, not hours. **Run it by
hand in a detached session**, then let the schedule handle the nightly delta:

The deploy tree is whatever `PROD_DEPLOY_PATH` points at; `$DEPLOY` below
stands in for it.

Export only the variables the job reads. `set -a; source .env.prod` would hand
the process every secret the stack owns, which it has no use for — and a `.env`
file is not a shell script, so sourcing one dies on a password containing a
quote, and runs part of one containing a backtick.

Read line by line and assigned, never expanded. `export $(grep ...)` splits the
values on whitespace and lets the shell see what is in them, which is the same
mistake the scheduled job was rewritten to stop making:

```bash
sudo -i -u bloom-deploy
DEPLOY=/path/to/deploy/tree
while IFS='=' read -r key value; do
    [ -n "$key" ] && export "$key=$value"
done < <(grep -E '^(BACKUP_[A-Z_]+|POSTGRES_(USER|DB)|MINIO_ROOT_[A-Z_]+)=' \
             "$DEPLOY/.env.prod")
```

**Before the `main` promotion, `$DEPLOY` is the staging tree** — that is where
the script lives until prod is redeployed, and the prod tree is reset to `main`
on every deploy. `.env.prod` is not in the staging tree, and the prod one does
not carry the `BACKUP_*` keys yet, so read the credentials from prod and supply
the four backup settings by hand:

```bash
PROD=/path/to/prod/deploy/tree   # $PROD_DEPLOY_PATH
while IFS='=' read -r key value; do
    [ -n "$key" ] && export "$key=$value"
done < <(grep -E '^(POSTGRES_(USER|DB)|MINIO_ROOT_[A-Z_]+)=' "$PROD/.env.prod")

export BACKUP_MINIO_BUCKET=bloom-storage
export BACKUP_MINIO_PREFIX=storage-single-tenant
export BACKUP_BOX_REMOTE=box
export BACKUP_BOX_ROOT=Bloom-Backups/BloomV2-Data-Backup/prod/storage
export BACKUP_WORKERS=8
```

`--env prod` selects the production containers, not a path, so running the
script out of the staging tree still mirrors production.

Prove the path end to end before committing to days of transfer:

```bash
# 1. reads Postgres only, copies nothing
python3 "$DEPLOY/scheduled-jobs/box-object-backup/backup_objects.py" \
    --env prod --dry-run

# 2. first real bytes — preflight checks the MinIO layout, verify checks Box
python3 "$DEPLOY/scheduled-jobs/box-object-backup/backup_objects.py" \
    --env prod --buckets images --limit 20 --verify 20
```

Then open the Box folder and confirm the images preview.

**On a rebuilt host, restore the ledger before step 2.** Step 1 is a dry run
and copies nothing, but step 2 copies twenty objects, and a run that copies
something uploads its ledger. That upload refuses to replace a larger copy, so
what you would actually see is `ledger NOT uploaded: the ledger on Box is AHEAD
of this host` in the log, and **The ledger on Box is newer than this host's** in
the summary — the Box copy is safe, and stays safe precisely because the upload
was refused, but it stops being updated until the local ledger is whole again.
Restore it first and both problems go away. See *If the deploy host itself is
gone* below.

The seed itself runs detached, in nightly chunks so it never runs through
working hours:

```bash
tmux new -s box-seed
python3 "$DEPLOY/scheduled-jobs/box-object-backup/backup_objects.py" \
    --env prod --full --limit 500000 --verify 50
```

`--full` ignores the watermark; `--limit` caps one night's work. A run
stopped by `--limit` is recorded `partial` on purpose, so it never becomes the
watermark and the next night re-enumerates from the start, skipping whatever
the ledger already holds.

While the seed holds the lock, the scheduled workflow stands down and reports
**skipped** rather than succeeded.

The run is **resumable**: every successful copy is recorded in the SQLite
ledger at `/var/lib/bloom-box-object-backup/ledger.db`, so an interrupted
seed picks up where it stopped. Re-running costs one query, not one Box call
per already-copied object — the ledger, not the Box listing, is what the plan
is built against.

## Stopping and resuming

A run can be stopped at any point and started again later. It carries on from
where it stopped — the ledger records every object as it is copied, so nothing
is done twice and nothing is missed.

This is what makes it safe to deploy during the seed. **Stop the backup, confirm
it has stopped, deploy, start the backup again.** No coordination, no waiting
for a multi-hour run to finish.

### Stopping it

Whichever of these you use, the effect is the same:

| where it is running | how to stop it |
|---|---|
| by hand in tmux | `Ctrl-C`, or `kill <pid>` from another shell |
| detached, or you are not attached to it | `kill $(python3 -c 'import json;print(json.load(open("/var/lib/bloom-box-object-backup/backup.lock"))["pid"])')` |
| started by the scheduled workflow | cancel the run in the Actions tab |

The lock file records the pid of whatever is running, so you never have to hunt
for it in `ps`.

Cancelling in the Actions tab stops **only a run that job started**. If a
nightly found the seed already going and stood down, cancelling that nightly
leaves the seed alone — which is what you want, and also means it is not a way
to stop a seed you started by hand. Use `kill` for that.

**Do not use `kill -9`.** That is the one thing it cannot survive tidily: it
skips the cleanup, leaves the rclone container holding the RC port, and the next
run then refuses to start until you remove it (it will tell you the command).

### What it does when asked

- finishes copying the object already in flight, and records it
- starts nothing further
- removes its rclone container, commits the ledger, writes the run report
- exits **3** — "interrupted; progress is in the ledger and the next run resumes"

While it is copying, it stops within seconds — it does not wait out the rest of
the 20,000-object batch it was working through, nor plan another one.

**One exception, worth knowing before you rely on it.** A run begins by asking
Postgres for the object list, and on a full run that query reads every row of
`storage.objects` and can take many minutes. A stop that arrives during it is
not noticed until the query returns; the run then stops immediately, before
copying anything. So a stop is quick during the copying and can be slow at the
very start.

If you are stopping it in order to deploy, confirm it has actually stopped —
see below — rather than assuming. A cancelled workflow that gave up waiting
says so on the run, as a warning reading `pid N has not stopped yet`.

A stopped run is recorded **partial**, never `ok`. That matters: `ok` is what
"everything up to here is backed up" points at, and a stopped run has not
reached the end of the table. Recording it clean would move that marker past
objects it never looked at.

### Confirming it stopped cleanly

```bash
docker ps -a --filter name=bloom-box-backup-rclone     # expect: nothing
sqlite3 /var/lib/bloom-box-object-backup/ledger.db \
  "SELECT started_at, outcome FROM runs ORDER BY id DESC LIMIT 3;"
```

An empty container list means the cleanup ran. A `partial` outcome on the last
row is what a stopped run looks like.

### Resuming

Run the same command again. There is no separate resume step:

```bash
python3 "$DEPLOY/scheduled-jobs/box-object-backup/backup_objects.py" \
    --env prod --full --limit 500000 --verify 50
```

It re-reads the object list, skips everything the ledger already holds, and
carries on. The skipped objects cost one database query between them, not one
Box call each.

## Nightly behaviour

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

**A skipped object is not backed up, and only a rename in Supabase can change
that.** The run says so — **Some images were not backed up because of their
filenames** in the summary — and is recorded `partial`, which holds the
watermark so the object stays enumerated every night until it is renamed. It
used to record `ok`: the watermark moved past the object, its `updated_at`
never changes, so no later incremental run enumerated it again and it was gone
from the mirror for good, with one WARNING line as the only trace.

The report on Box names each one with its reason. Reading the report is the
durable check — a journal rotates and an Actions log expires:

```bash
rclone cat "box:$BACKUP_BOX_ROOT/_runs/<latest>.json" | jq '.stats.skipped, .skips'
```

## Monitoring

The run summary in the Actions tab is the first place to look — it says
whether the night **succeeded**, **failed**, or was **skipped** because the
seed still holds the lock, and carries the run's own closing lines.

```bash
gh run list --workflow box-object-backup.yml --limit 10
gh run view <run-id> --log
```

Exit codes: `0` clean, `1` some objects failed after retries, `2`
configuration or preflight error, `3` interrupted (progress kept), `4`
verification found objects missing or the wrong size on Box, `5` objects were
refused because two names collide on one Box path.

**A run stopped on purpose reports "stopped, progress kept", not FAILED.** Exit
`3` is what the job's own cancellation produces and what the Actions time limit
produces — which, during the seed, is most nights. Everything copied up to that
point is recorded and on Box, and the next run carries on from there. It used
to render as "FAILED — the mirror was not updated this run", which was wrong on
both counts and made the one outcome meaning "this is fine" look like a real
failure.

**The summary reads a status line, not the log's prose.** The job prints
`BOX_BACKUP_STATUS=` and `BOX_BACKUP_FLAGS=` at the end of a run, and the
summary matches them anchored to the start of a line. Before that it searched
the whole log for English phrases — and object names are in that log, so an
image called `box-object-backup: SKIPPED.png` (the colon guarantees it is
refused, hence logged) made a night with thousands of failed copies render as
"skipped, this is expected until the seed ends". Every branch could be forged
that way, by anyone who can upload a file, and by accident too. If you are
reading a job log by hand, those two lines are the fastest summary of what
happened.

### Objects that were refused

Exit `5`, and **OBJECTS NOT BACKED UP** in the summary. Two object names can
differ in the database and still normalize to one path on Box — the same
filename uploaded from machines that spell an accent differently. Box can hold
only one of them, so the job copies the first and refuses the second rather
than overwriting it.

The refused object is **not backed up**, and nothing on this side can fix
that. **Rename the object named at the start of the `skipping` line** — the
refused one. Renaming its twin instead leaves a ledger row still claiming the
path, nothing prunes that row, and the refused object is then refused for ever.

The two names look identical, which is why they collide, so the log escapes
them: one reads `cafe\u0301.png` and the other `caf\xe9.png`. Match the escaped
form against what Supabase shows.

Do **not** delete the ledger row here, even though that is the remedy for a
verification mismatch. The row belongs to the object that won the path; delete
it and the refused object takes the path and overwrites the winner's file on
Box, which nothing then repairs.

Such a run is recorded `partial`, deliberately, so the watermark does not move
past an object that is not on Box. Until the rename, each night re-reads
everything changed since the last *clean* run rather than since last night — a
window that grows until someone acts, and the whole table if no run has ever
been clean. That is the intended price of not losing sight of the object.

`stats.collisions` in the run report is the count. Note they are also included
in `stats.skipped`, so that figure is not only Box-illegal names.

There are none in production today: every name is already in the normalized
form, so nothing can collide. This exists for the day something uploads one
that is not.

### The ledger on Box stopped being updated

Neither of these has an exit code — they cannot fail the run, because the
objects did reach Box and the mirror is fine. What did not reach Box is the
**record of which objects are already mirrored**, the thing that makes a
re-seed unnecessary. Both notices appear beside the night's result rather than
instead of it, so they show up on nights that otherwise succeeded — which is
every night either actually happens.

They are opposites, and so are their remedies. Check which one you have.

**The ledger on Box was NOT updated** — the upload failed, or the local ledger
could not be read. This host has the good copy; Box is behind and falling
further behind every night. Fix the upload.

**The ledger on Box is newer than this host's** — the upload was refused on
purpose, because this host's ledger is smaller. Box has the good copy and this
host has a stub, so this is not the machine that built the mirror. **Restore
from Box; do not upload over it.** Uploading here replaces millions of rows
with this run's and costs a full re-seed.

*If the deploy host itself is gone* below covers both, and how to restore.

Progress lines report objects/second and a projected finish. Failures are
retried with backoff — Box's 429s and 5xx are treated as transient; a 404 on
the MinIO side is not, and is reported.

### Run reports on Box

Two things sit beside the mirror on Box rather than in it: dated run reports
under `_runs/`, and a copy of the ledger under `_state/`. Neither is a
backed-up object, and a restore that walks the mirror should skip both.

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
`already_current`, `verify_checked`, `verify_mismatched`, `verify_unverified`),
and the paths of every object that is **not** on Box, by all three routes:
`failures` (the copy failed), `skips` (refused for its name, with the reason),
and `verify_failures` (the copy reported success and the check found it
absent). All three are capped, with `failure_count` and `skips_truncated`
saying so; the counts in `stats` remain exact. Reports are written for failed
runs too.

Those lists are the point of this file. A count tells you something is
missing; only the list tells you *which*, and the job log that also carries it
is a GitHub Actions log under retention.

`verify_checked` and `verify_mismatched` are what make the report a record of
a *checked* backup rather than an attempted one. `verify_checked: 0` means
nobody looked, which is not the same as looking and finding nothing wrong —
and `verify_unverified` says which of the two it was: zero means verification
was never asked for, non-zero means it was asked and Box did not answer.

### What verification does, and does not, prove

After copying, `--verify N` asks Box directly whether N of the objects this run
copied are present and the expected size. A non-zero mismatch count records the
run `partial`, which holds the watermark.

**Only two answers count against the backup:** Box does not have the object, or
has it at a different size. A stat call that fails outright — a 429, a dropped
connection — is neither. Those are counted as `verify_unverified`, kept out of
`verify_mismatched`, and change no exit code. The distinction matters because a
mismatch is reported as an object that is not on Box, and chasing that on the
strength of one Box hiccup is work done for nothing on evidence that was never
there.

`verify_checked` is what was actually answered, so it can be smaller than the N
requested. **Whenever it is**, the run says **verification did NOT cover its
sample**, the summary carries a notice, and the headline shows the shortfall
beside the count — `50 verified` and `2 verified (48 unanswered)` describe very
different nights and must not look alike.

Any shortfall counts, not only a total blackout: 2 answers out of 50 is still a
night whose headline claims far more than was established. None of it fails the
run — the copies were confirmed as they were made, so an unanswered stat is
evidence of nothing — but a check reporting success on a sample it mostly did
not cover is the one outcome verification exists to rule out.

The stat calls fire straight after a run that may have pushed hundreds of
thousands of objects, which is exactly when Box throttles. If it repeats, the
lever is the workflow's `verify` input — lower it, or move the schedule off
Box's busy hours. It is set on the workflow itself (`verify` under
`workflow_dispatch`, and the value passed on the scheduled path); there is no
`.env` setting for it.

**A mismatched object is NOT re-copied automatically, and the run changes
nothing to compensate.** The ledger still records it as mirrored, so later runs
skip it and the warning does not repeat. The run's job here is to say clearly
that an object is missing and to name it; putting it back is a person's
decision.

That is deliberate. This is a backup, and the job does not remove its own
record of what is on Box — not on a schedule, not unattended, not to fix
itself. A version of this job briefly did, clearing the proved-wrong row so
the next run would re-copy the object. It was removed: an automatic DELETE
against production bookkeeping is not a decision a nightly job gets to make,
and the guard written to make it safe did not hold under `--limit`,
`--buckets` or a stop, which is how the job is actually run.

The failing paths are in the run's log and named in `verify_failures` in the
report under `_runs/` on Box. **Check `verify_mismatched` in the run report
after each backup** — it is the one number that says whether what was copied
is actually there.

**Restoring one is a manual, considered change**, not a routine step, and
there is no copy-paste command here on purpose. The object's row in the ledger
is what makes later runs skip it; changing that is a database edit on the
deploy host and should be done by someone who has read this section, with the
run report open, and with one caveat in mind: **if the same run also reported a
refused name collision, resolve that first.** There the row belongs to the
object that won the Box path, and removing it lets its twin take that path and
overwrite a good backup. The two are entangled; acting on one while the other
stands is how a backup is lost.

The N objects are a uniform sample of the run's **successful** copies, chosen
by a hash of each object's path rather than by arrival order, so the same set
of copies always yields the same sample no matter which worker finished first.

**N defaults to 50 on every scheduled run**, set by the workflow, and is a
`verify` input you can change when running by hand. The command-line default
is `0`, so a manual run without `--verify` checks nothing. That reliably catches a systemic fault — wrong
path, broken auth, nothing landing at all — and is not statistical assurance
about rare corruption: 50 of 8M objects is 0.0006% of the mirror. Whether it
should instead scale with the size of the run is an open question and
deliberately left for review rather than decided here.

Size is also the only property compared. MinIO exposes MD5 and Box exposes
SHA-1, so there is no common checksum, and a file corrupted without changing
length would pass.

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

### If the deploy host itself is gone

The ledger is what makes the mirror resumable, and it lives on that host. A
copy is kept on Box at `<BACKUP_BOX_ROOT>/_state/ledger.db`, uploaded after any
run that copied something — unless the copy already there is larger, which
means the run's own ledger knows less than the copy does. Put it back before running the job on a rebuilt
host:

```bash
sudo -i -u bloom-deploy
export BACKUP_BOX_ROOT=Bloom-Backups/BloomV2-Data-Backup/prod/storage
rclone copyto "box:$BACKUP_BOX_ROOT/_state/ledger.db" \
    /var/lib/bloom-box-object-backup/ledger.db
```

As `bloom-deploy`, because the Box token lives in that user's home. If you
restore it as root, `chown bloom-deploy:bloom-deploy` the file afterwards or
the next run dies with a read-only-database error.

Two things to check before running anything against it:

```bash
# no stale write-ahead log beside the restored file
ls /var/lib/bloom-box-object-backup/ledger.db-wal 2>/dev/null && \
    echo "REMOVE THIS — it belongs to the old file"

# and it is the ledger you expect: ~8.0M rows, not a smoke test's twenty
sqlite3 /var/lib/bloom-box-object-backup/ledger.db \
    "SELECT count(*) FROM copied;"
```

Without it the job starts from an empty ledger, concludes nothing has ever been
copied, and re-transfers all eight million objects — weeks of work, and the Box
API load that comes with it. Listing Box cannot rebuild it: the ledger is keyed
on each object's `version`, and a listing shows only that a path exists.

Normally the copy is one run behind at most, so restoring it may mean a few
objects are copied again. That is harmless — the copy simply overwrites what
is already there.

The copy can also stop being updated, and there are **two opposite reasons**.
Neither fails the run — the objects reach Box either way — so each has its own
summary notice, printed alongside the night's result rather than instead of it.
Read which one you got before acting: the remedies are the reverse of each
other.

**The ledger on Box was NOT updated.** The upload was attempted and failed, or
the local ledger could not be read. This host has the good copy and Box is
behind. Every later night keeps reporting success while it falls further
behind, so act on the night it appears. The job log carries the reason:
`ledger stayed on the host only` for a failed upload, `cannot read` for an
unreadable one. Once it works again, `ledger on Box:` in the next run's log
confirms the copy is current.

**The ledger on Box is newer than this host's — the upload was refused on
purpose.** Box holds the record of what is already mirrored and this host holds
a smaller one, which means this is not the machine that built the mirror: a
rebuilt host, or a wiped state directory. **Restore the Box copy onto this
host, and do not upload over it.** Uploading would replace the record of
millions of objects with this run's and cost a full re-seed — this refusal is
the guard that prevents exactly that, not a fault to be worked around. The job
log reads `ledger NOT uploaded: the ledger on Box is AHEAD of this host`.
Restoring is the section you are reading; do that first, then re-run.

## Configuration

Set in `.env.prod` (defaults in `.env.prod.defaults`). Production is the only
environment mirrored, so `.env.staging.defaults` deliberately carries no
`BACKUP_*` keys and a test enforces that.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BACKUP_MINIO_BUCKET` | `bloom-storage` | The single MinIO bucket storage-api writes into (`STORAGE_S3_BUCKET` in the compose file). **Required** — an empty value makes rclone read each object's own `bucket_id` as a bucket name and every copy 404s. |
| `BACKUP_MINIO_PREFIX` | `storage-single-tenant` | Tenant prefix storage-api files objects under. Config rather than a constant because nothing in the stack declares it — storage-api chooses it. To see the path on a host: `docker exec <minio-container> ls /data/bloom-storage/` |
| `BACKUP_BOX_REMOTE` | `box` | Name of the rclone remote |
| `BACKUP_BOX_ROOT` | `Bloom-Backups/BloomV2-Data-Backup/prod/storage` | Folder on Box to mirror into |
| `BACKUP_WORKERS` | `8` | Concurrent copies; lower it if Box throttles hard |
| `BACKUP_BWLIMIT` | *(unset)* | rclone bandwidth cap, e.g. `20M` |
| `BACKUP_STATE_DIR` | `/var/lib/bloom-box-object-backup` | Ledger location. Not in the env file — a code default. The workflow's cancel step hardcodes this path, so setting it would stop that step finding the run. |
| `BACKUP_RC_PORT` | `5572` | Loopback port for the rclone daemon. Not in the env file — a code default. |

`MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` / `POSTGRES_USER` / `POSTGRES_DB`
come from the same `.env` file; the job passes MinIO's credentials to rclone
inline so they never land in a config file on disk.

`BACKUP_MINIO_BUCKET` and `BACKUP_MINIO_PREFIX` are checked against a real
object before any copying starts: the run stats one object the manifest names
and aborts with the exact path it tried if MinIO does not hold it there. A
wrong value in an env file is as fatal as a wrong constant in the code — what
makes it survivable is failing in seconds rather than after a multi-day seed
that 404s everything and leaves an empty mirror.

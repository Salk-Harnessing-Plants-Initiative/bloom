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
   job's container mounts that exact file **read-only**, so if the token ever
   expires, refresh it by hand:
   `sudo -u bloom-deploy rclone config reconnect box:`.

2. **Create the destination folder** in Box matching `OBJECT_BACKUP_BOX_ROOT`
   (`Bloom-Backups/BloomV2-Data-Backup/prod/storage` by default).

3. **State directory**, once, as `bloom-deploy`. The ledger lives here and it
   is what makes the seed resumable. It sits on `/data`, outside both deploy
   trees, so a deploy that resets a tree cannot touch it:
   ```bash
   mkdir -m 700 /data/bloom/box-object-backup
   ```
   The container mounts it and never creates it: if it is missing, the run
   fails before it starts, rather than Docker creating it owned by root.

4. **`bloom-deploy` must be in the `docker` group** — it starts the job's
   container.
   ```bash
   id -nG bloom-deploy | tr ' ' '\n' | grep -qw docker || \
       sudo usermod -aG docker bloom-deploy
   ```

5. **Nothing else is installed on the host.** The job runs in a container of
   its own, defined in `scheduled-jobs/box-object-backup/compose.yml`, from an
   image pinned by digest that carries Python, `psql` and `rclone`. The
   container joins production's `supanet` to reach Postgres and MinIO, whose
   ports are never published, and runs rclone inside itself, so nothing listens
   on the host. The image is public on GHCR, so the host pulls it without
   logging in. (A host `rclone` is still handy for step 1 and for spot checks.)

## Scheduling

`.github/workflows/box-object-backup.yml` runs it: GitHub Actions triggers on
a schedule, SSHes to the deploy host — the same shape as `deploy.yml` — and
starts the job's container there with `docker compose run`. The container
exists only while the run does; Docker removes it when the job exits. There is
nothing to install on the server beyond the prerequisites above.

Every night at 02:17 UTC, so at most a day's scans exist only in MinIO rather
than up to a week's.

**This is the bytes half of a backup.** A full restore also needs the
`storage.objects` rows, which the weekly Postgres dump carries — see
`_WIKI/SCHEDULEDJOBS/weekly-backup.md`. Both halves exist; neither is a proven
restore until one has been rehearsed.

Actions was chosen over a systemd timer because a failed run then surfaces
through notifications people already read, whereas `systemctl --failed` only
reports to whoever thinks to look.

**Two limits of that, worth knowing before you rely on it.** GitHub sends the
failure notification for a scheduled workflow only to the account that last
edited the cron expression — one inbox, not the team. And **nothing reports a
night that never ran**: every signal this job produces starts with a run that
executed, so a dropped cron, a disabled workflow, or the file going missing
from `main` produces no run, no summary and no mail, while every previous
green tick still stands. The cheap detector is the `_runs/` folder on Box: one
report lands there per night, so a gap in that listing is a gap in the mirror.
Someone should look at it weekly.

**Seed before promoting to `main`.** The first scheduled run with an empty
ledger has no watermark to work from, so it enumerates everything — all 8M
objects — inside a job that GitHub kills at 240 minutes. It would fail every
night until someone intervened. Once a seed is under way the lock makes the
scheduled job stand down, but the lock cannot help if no seed has started.

Safe order: merge to `staging` → deploy → dry run → smoke test → **second
smoke test more than an hour later** → seed by hand over several nights →
promote to `main` → **approve the production deploy** → first scheduled night.

**The production deploy is a step, not a formality.** Promoting to `main` arms
the 02:17 schedule immediately, but the prod tree is only updated inside the
deploy job, which waits for an approval. Leave that approval pending and the
first scheduled night SSHes to a prod tree that has no `compose.yml` for the
job, and fails before anything starts.

**The workflow cannot fire until this file reaches `main`.** GitHub honours
`schedule:` and `workflow_dispatch` only from the default branch, so merging
to `staging` is not enough — the normal staging → main promotion has to carry
it across. Until then, nothing runs on a schedule.

To run it by hand: Actions → *Nightly Box object mirror* → **Run workflow**.
A hand-run goes through the `production` environment, which has required
reviewers and a five-minute wait timer, so it sits in *Waiting* until someone
approves it — the 02:17 schedule does not, and starts immediately.
Production is the only target — there is no environment to choose. The two
inputs are `dry_run` and `verify`.

## The seed run

The first run has to move everything, which for prod is on the order of
millions of objects. Box throttles per-user API calls and every file costs at
least one call, so the seed is measured in days, not hours. **Run it by
hand in a detached session**, then let the schedule handle the nightly delta:

Everything below runs on the host as `bloom-deploy`, from a deploy tree.
`$PROD` is the production tree (`PROD_DEPLOY_PATH`), and `$DEPLOY` is the tree
the job's files come from:

```bash
sudo -i -u bloom-deploy
export PROD=/path/to/prod/deploy/tree   # $PROD_DEPLOY_PATH
export DEPLOY="$PROD"                   # the staging tree before the main promotion
cd "$DEPLOY"
```

**Before the `main` promotion, `$DEPLOY` is the staging tree** — that is where
`compose.yml` lives until prod is redeployed. Nothing else changes: the
credentials still come from production's `.env.prod`, and the compose file
names only production's network, so a run started from the staging tree still
reads production's database and MinIO.

Each run is one container from the job's compose file. Compose reads the three
credentials out of `$PROD/.env.prod` and passes them in. Every other setting
comes from `.env.prod.defaults` in `$DEPLOY`, mounted read-only, so a setting
changed only in the server's `.env.prod` has no effect on the job. To change
one for a single run, pass it after `run` with `-e`, for example
`-e OBJECT_BACKUP_WORKERS=4`.

Prove the path end to end before committing to days of transfer. In a second
shell, `docker stats` shows the container's memory while each one runs; note
the peak, because `mem_limit` in `compose.yml` is set from it:

```bash
# 1. copies nothing. Checks every setting, the rclone config and the Box root
#    BEFORE reading the table, then lists what a real run would do.
docker compose -f scheduled-jobs/box-object-backup/compose.yml \
    --env-file "$PROD/.env.prod" run --rm -T --user "$(id -u):$(id -g)" \
    box-object-backup --env prod --dry-run

# 2. first real bytes — preflight checks the MinIO layout, verify checks Box
docker compose -f scheduled-jobs/box-object-backup/compose.yml \
    --env-file "$PROD/.env.prod" run --rm -T --user "$(id -u):$(id -g)" \
    box-object-backup --env prod --buckets images --limit 20 --verify 20
```

Then open the Box folder and confirm the images preview.

**3. Run step 2 again, more than an hour later, before starting the seed.**
Box access tokens last about an hour. The rclone config is mounted read-only,
so a refresh lives in the daemon's memory and is never written back — which is
fine within one run and unproven across two. If Box rotates the refresh token,
the copy on disk is spent and this second run is where that shows up, in
minutes, instead of on night three of a seed. If it fails to authenticate, run
`rclone config reconnect box:` and start the pair again.

**The ledger remembers where it mirrored to.** The first real run records
`<OBJECT_BACKUP_BOX_REMOTE>:<OBJECT_BACKUP_BOX_ROOT>`, and any later run pointed somewhere
else is refused before it reads a row. That is deliberate: the ledger tracks
which objects are copied, not where, so against a different folder it would
report millions of objects as already backed up while that folder stayed
empty. Three ways out, cheapest first:

- **The rclone remote was recreated under a different name.** The recorded
  value is `<remote>:<root>`, so `box2:...` does not match `box:...` even
  though it is the same Box account and the same folder. Name the remote `box`
  again in `rclone config`, or set `OBJECT_BACKUP_BOX_REMOTE` to whatever it is now
  and move the folder to match. This is the likeliest trip on a rebuilt host.
- **The root was mistyped.** Correct `OBJECT_BACKUP_BOX_ROOT`.
- **The mirror genuinely has to move.** Move the folder on Box and keep the
  recorded value, or point `OBJECT_BACKUP_STATE_DIR` at a new directory and seed the
  new location from scratch — that is a full re-seed, so only do this when the
  destination really is new.

The seed itself runs detached, in nightly chunks so it never runs through
working hours. It is a container named `box-object-backup-seed`, started
without `--rm` so that its log outlives it:

```bash
cd "$DEPLOY"
docker compose -f scheduled-jobs/box-object-backup/compose.yml \
    --env-file "$PROD/.env.prod" run -d --name box-object-backup-seed \
    --user "$(id -u):$(id -g)" \
    box-object-backup --env prod --full --limit 500000 --verify 50

docker logs -f box-object-backup-seed   # follow it; Ctrl-C leaves it running
docker wait box-object-backup-seed      # prints its exit code once it ends
```

Nothing is needed from the shell once it has started: the container carries its
own settings, and closing the session does not stop it.

The name matters. The nightly workflow names its containers after its run,
`box-object-backup-<run id>-<attempt>`, and stops only that name when it is
cancelled, so cancelling a nightly can never stop the seed.

**Seed until a chunk records `ok`.** Every `--limit`-truncated run is recorded
`partial` on purpose, and only an `ok` run sets the watermark. Promote to
`main` after a truncated chunk and the first scheduled night still has no
watermark, so it enumerates all eight million rows inside the 240-minute job.
The *Confirming it stopped cleanly* query below is how you check.

`--full` ignores the watermark; `--limit` caps one night's work. A run
stopped by `--limit` is recorded `partial` on purpose, so it never becomes the
watermark and the next night re-enumerates from the start, skipping whatever
the ledger already holds.

While the seed holds the lock, the scheduled workflow stands down and reports
**skipped** rather than succeeded.

The run is **resumable**: every successful copy is recorded in the SQLite
ledger at `/data/bloom/box-object-backup/ledger.db`, so an interrupted
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

Every run is a container, so stopping one is `docker stop`:

| what is running | how to stop it |
|---|---|
| the seed | `docker stop box-object-backup-seed` |
| a run the scheduled workflow started | cancel it in the Actions tab |
| any other run | `docker ps --filter label=com.docker.compose.project=bloom-box-object-backup` shows its name; `docker stop <name>` |

`docker stop` sends the job SIGTERM, then waits up to the grace period set in
`compose.yml` (five minutes) before killing it. A stop normally takes seconds.

Cancelling in the Actions tab stops **only the container that job started**,
`box-object-backup-<run id>-<attempt>`. If a nightly found the seed already
going and stood down, cancelling that nightly leaves the seed alone — which is
what you want, and also means it is not a way to stop the seed. Use
`docker stop box-object-backup-seed` for that.

**A run that is killed leaves nothing to clean up** — `docker kill`, the grace
period running out, the host rebooting. The rclone daemon lived inside the
container and went with it. Objects that finished copying stay recorded in the
ledger, and the ones in flight are copied again next time. The run is never
recorded `ok`, so the watermark does not move. What is lost is that run's
report.

### What it does when asked

- finishes copying the objects already in flight, and records them
- starts nothing further
- stops its rclone daemon, commits the ledger, writes the run report
- exits **3** — "interrupted; progress is in the ledger and the next run resumes"

While it is copying, it stops within seconds — it does not wait out the rest of
the 20,000-object batch it was working through, nor plan another one.

**One exception, worth knowing before you rely on it.** A run begins by asking
Postgres for the object list, and on a full run that query reads every row of
`storage.objects` and can take many minutes. A stop that arrives during it is
not noticed until the query returns; the run then stops immediately, before
copying anything. If that takes longer than the grace period, Docker kills it,
which costs nothing because it had copied nothing yet.

If you are stopping it in order to deploy, confirm it has actually stopped —
see below — rather than assuming. A cancelled workflow whose `docker stop`
failed says so on the run, as a warning.

A stopped run is recorded **partial**, never `ok`. That matters: `ok` is what
"everything up to here is backed up" points at, and a stopped run has not
reached the end of the table. Recording it clean would move that marker past
objects it never looked at.

### Confirming it stopped cleanly

```bash
docker ps --filter label=com.docker.compose.project=bloom-box-object-backup   # expect: nothing
sqlite3 /data/bloom/box-object-backup/ledger.db \
  "SELECT started_at, outcome FROM runs ORDER BY id DESC LIMIT 3;"
```

An empty list means no run is going. A `partial` outcome on the last row is
what a stopped run looks like. For the seed, `docker wait box-object-backup-seed`
prints `3` after a clean stop.

### Resuming

Run the same command again; there is no separate resume step. The seed's
stopped container keeps its name until it is removed, so remove it first:

```bash
cd "$DEPLOY"
docker rm box-object-backup-seed
docker compose -f scheduled-jobs/box-object-backup/compose.yml \
    --env-file "$PROD/.env.prod" run -d --name box-object-backup-seed \
    --user "$(id -u):$(id -g)" \
    box-object-backup --env prod --full --limit 500000 --verify 50
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
filenames** in the summary — and names each one, with its reason, in the run
report on Box.

**That night is the only notification.** The run is still recorded clean, and
the watermark advances past the object, so no later run enumerates it again.
That is deliberate: nothing on this side can ever fix such a name, so holding
the watermark would freeze it permanently, and every night would then re-read
all eight million rows inside a 240-minute job — the scenario this page warns
about under *Seed before promoting to `main`*. One ordinary filename should not
cost that.

The trade is that the reminder does not repeat. The report under `_runs/` on
Box is the durable record, and it outlives the Actions log. Read it after a
night that reports skips, and rename the objects at the source.

A refused **name collision** is treated the opposite way and still holds the
watermark — that one is rarer, and renaming either of the pair clears it, so
keeping it in view costs a slow night rather than a frozen mirror.

The report on Box names each one with its reason. Reading the report is the
durable check — a journal rotates and an Actions log expires:

```bash
rclone cat "box:$OBJECT_BACKUP_BOX_ROOT/_runs/<latest>.json" | jq '.stats.skipped, .skips'
```

### Rows with no image behind them

Postgres can list an object whose bytes are not in MinIO — usually a delete
that removed the file and left the row behind. Nothing can copy bytes that are
not there, so the job does not treat it as a failed copy: it logs
`source object is gone:`, names it under `source_gone` in the run report, and
lets the watermark move.

That is the same treatment, for the same reason, as a filename Box cannot
store. Counted as a failure it would hold the watermark for good — no run
would ever be recorded clean, so every night would re-read all eight million
rows inside a 240-minute job, and nothing in the job could ever clear it.

Two things must agree before an object is classed this way: the copy error has
to say the key is missing, **and** a direct lookup against MinIO has to confirm
it is absent. A lookup that cannot answer counts as an ordinary failure — "I
could not ask" is not "it is not there", and getting that wrong would move the
watermark past an object that was never copied.

**It is not recorded as backed up.** No ledger row is written, so nothing later
claims it is on Box. Someone has to decide whether the database row should
still exist; this job will not.

## Monitoring

The run summary in the Actions tab is the first place to look — it says
whether the night **succeeded**, **failed**, or was **skipped** because the
seed still holds the lock, and carries the run's own closing lines.

```bash
gh run list --workflow box-object-backup.yml --limit 10
gh run view <run-id> --log
```

Progress lines report objects/second and a projected finish. Failures are
retried with backoff — Box's 429s and 5xx are treated as transient; a 404 on
the MinIO side is not, and is reported.

Exit codes: `0` clean, `1` some objects failed after retries, `2`
configuration or preflight error, or the rclone daemon stopped working, `3`
interrupted (progress kept), `4` verification found objects missing or the
wrong size on Box, `5` objects were refused because two names collide on one
Box path.

**A verification mismatch (exit 4) does NOT hold the watermark.** It used to
claim it did, and that claim was never true beyond one night: the next run finds
the object already current, never re-copies it, so never re-checks it, records
itself clean and advances anyway. Nothing here can put the object back either.
It fails the run loudly instead, and `name_skips`/`verify_failures` in the run
report on Box are the durable record of what is missing.

**A run stopped on purpose reports "stopped, progress kept", not FAILED.** Exit
`3` is what a cancel and the job's own stop script produce. Everything copied up
to that point is recorded and on Box, and the next run carries on from there. It
used to render as "FAILED — the mirror was not updated this run", which was
wrong on both counts and made the one outcome meaning "this is fine" look like
a real failure.

The verdict has two routes home, because the obvious one does not survive a
cancel. Normally the run prints it and the workflow reads it off the log — but
the log travels back over an ssh pipe the workflow holds open, and cancelling
or timing out the job kills that pipe before the last line is printed. So the
run also writes the verdict into its report on the host, *before* printing it,
and the summary falls back to fetching that over a fresh connection. It
accepts only a report belonging to THIS run: every run records the id of the
GitHub job that started it, in the lock it takes and in the report it writes,
and the summary asks the host for the newest report carrying its own id. A
seed started by hand carries no job id at all, so it can never hand over its
verdict to a nightly. If neither route produces one, the step's own outcome
decides.

The cancel step goes by name instead. It stops only the container named for
its own run, `box-object-backup-<run id>-<attempt>`, and only if Docker labels
it as the backup job's. The seed is named `box-object-backup-seed`, so
cancelling a nightly that stood down against the seed cannot stop it — days of
copying, halted silently.

**The summary reads a status line, not the log's prose.** The job prints
`BOX_BACKUP_STATUS=`, `BOX_BACKUP_FLAGS=` and `BOX_BACKUP_STATS=` (the counts,
as JSON) at the end of a run, and the summary matches them anchored to the
start of a line. The page itself is rendered by
`scheduled-jobs/box-object-backup/summary.py`, which the workflow calls with
the captured log; the wording lives there and is tested there. Before that it searched
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

**Case is not part of this check, and Box folds case.** `Plate_A.png` and
`plate_a.png` are two different objects to Postgres and to the ledger, and one
name to Box. Plate and barcode names make that plausible, and what Box does
with the second copy — refuse it, or overwrite the first — has not been
established. Nothing here detects it, so if two objects differ only in case,
treat the pair as unmirrored until you have checked Box by hand.

The two names look identical, which is why they collide, so the log escapes
them: one reads `cafe\u0301.png` and the other `caf\xe9.png`. Match the escaped
form against what Supabase shows.

Do **not** delete the ledger row here. Nothing in this job deletes a ledger
row — it has no way to — and doing it by hand is worse than it looks: the row
belongs to the object that **won** the path, so removing it lets the refused
object take that path and overwrite the winner's file on Box, which nothing
then repairs. The rename is the whole remedy.

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

### Run reports on Box

One thing sits beside the mirror on Box rather than in it: dated run reports
under `_runs/`. They are not backed-up objects, and a restore that walks the
mirror should skip them.

Every run drops a dated JSON report in `<OBJECT_BACKUP_BOX_ROOT>/_runs/`, named
`2026-08-31T021703Z-prod-run00042.json`. This is the only view of the
backup's history that does not need server access: a missing week shows up as
a gap in a Box folder listing.

That matters because neither of the other two records answers the question on
its own. The mirror holds current state, so a week where nothing changed looks
exactly like a week where nothing ran. The ledger's `runs` table does know the
difference, but it lives on the deploy host behind SSH and SQLite.

Each report carries the run's outcome (`ok`, `partial`, `error`), its
duration, the counts (`listed`, `copied`, `failed`, `skipped`,
`already_current`, `verify_checked`, `verify_mismatched`, `verify_unverified`),
and the paths of every object that is **not** on Box, by every route:
`failures` (the copy failed), `skips` (refused for its name, with the reason),
and `verify_failures` (the copy reported success and the check found it
absent), plus `name_skips` and `source_gone`. Each is capped, with
`failure_count`, `failures_truncated`, `skips_truncated`,
`name_skips_truncated` and `source_gone_truncated`
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
copied are present and the expected size. A non-zero mismatch count exits 4 and
does **not** hold the watermark — see *A verification mismatch (exit 4) does
NOT hold the watermark* above. The objects are named under `verify_failures`
in that night's run report, and that night is the only notification.

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
sqlite3 /data/bloom/box-object-backup/ledger.db \
  "SELECT started_at, finished_at, outcome, stats FROM runs ORDER BY id DESC LIMIT 10;"
```

## Restoring

Restoring a single file is a download from Box. Restoring the deploy needs
both halves, in this order:

1. Restore the Postgres dump. That brings back `storage.objects`, including
   each object's `version`.

   The weekly Postgres dump on Box carries those rows — see
   `_WIKI/SCHEDULEDJOBS/weekly-backup.md` for restoring it. Both halves of a
   restore now exist: that dump has the rows, this mirror has the bytes.
2. For each row, upload the Box copy back to MinIO at
   `<OBJECT_BACKUP_MINIO_BUCKET>/<OBJECT_BACKUP_MINIO_PREFIX>/<bucket_id>/<name>/<version>`
   — with the deployed defaults, that is
   `bloom-storage/storage-single-tenant/<bucket_id>/<name>/<version>`.

   **Read the Box copy at the NFC-normalized name, not the raw one.** The job
   writes to `<bucket_id>/` + `unicodedata.normalize("NFC", name)`, because
   that is the only way two spellings of the same accented filename cannot
   silently become two different Box paths. Ask Box for the raw row name and
   an accented filename 404s — which is exactly the set of names the
   collision guard exists for, so the failures land on the rows that were
   hardest to get right in the first place. Write back to MinIO under the
   **raw** name: that is what storage-api serves.

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

The ledger lives only on that host, under `/data/bloom/box-object-backup`; it
is not copied to Box. On a rebuilt host the job starts from an empty ledger,
concludes nothing has been copied, and copies every object again, overwriting
what is already on Box with the same bytes. That takes as long as the original
seed — days — and loses nothing.

Set up the prerequisites again, then seed exactly as on a new host. Listing Box
cannot rebuild the ledger: it is keyed on each object's `version`, and a
listing shows only that a path exists.

## Configuration

The job's settings come from `.env.prod.defaults` in the deploy tree the run
is started from, mounted read-only into its container. **Editing the server's
`.env.prod` has no effect on them**: change the defaults file in the repository,
or pass `-e KEY=value` to a single run. Production is the only environment
mirrored, so `.env.staging.defaults` deliberately carries no `OBJECT_BACKUP_*`
keys and a test enforces that.

| Variable | Default | Meaning |
| --- | --- | --- |
| `OBJECT_BACKUP_MINIO_BUCKET` | `bloom-storage` | The single MinIO bucket storage-api writes into (`STORAGE_S3_BUCKET` in the compose file). **Required** — an empty value makes rclone read each object's own `bucket_id` as a bucket name and every copy 404s. |
| `OBJECT_BACKUP_MINIO_PREFIX` | `storage-single-tenant` | Tenant prefix storage-api files objects under. Config rather than a constant because nothing in the stack declares it — storage-api chooses it. To see the path on a host: `docker exec <minio-container> ls /data/bloom-storage/` |
| `OBJECT_BACKUP_BOX_REMOTE` | `box` | Name of the rclone remote |
| `OBJECT_BACKUP_BOX_ROOT` | `Bloom-Backups/BloomV2-Data-Backup/prod/storage` | Folder on Box to mirror into |
| `OBJECT_BACKUP_WORKERS` | `8` | Concurrent copies; lower it if Box throttles hard |
| `OBJECT_BACKUP_BWLIMIT` | *(unset)* | rclone bandwidth cap, e.g. `20M` |
| `OBJECT_BACKUP_STATE_DIR` | `/data/bloom/box-object-backup` | Ledger location. Not in the env file — a code default. `compose.yml` mounts this exact path and the workflow passes it to the run; if anything sets it to another value the run **refuses to start** (exit 2) rather than let the summary watch an empty directory. The comparison is exact, so a trailing slash counts as different. |
| `OBJECT_BACKUP_RC_PORT` | `5572` | Port for the rclone daemon, on the container's own loopback, so nothing outside the container can reach it. Not in the env file — a code default. |
| `OBJECT_BACKUP_RCLONE_CONFIG` | `/config/rclone/rclone.conf` | Where the job reads the rclone config. Set by `compose.yml`, which mounts the deploy user's config there read-only. |

The three credentials — `POSTGRES_PASSWORD`, `MINIO_ROOT_USER` and
`MINIO_ROOT_PASSWORD` — come from the deploy's `.env.prod`, which compose reads
through `--env-file` and passes into the container: the same values the
database and MinIO get. Without `--env-file`, compose refuses to start the run.
The job hands MinIO's credentials to rclone inside each remote-control call, so
they never land in a config file on disk. `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_USER` and `POSTGRES_DB` come from the defaults file.

`OBJECT_BACKUP_MINIO_BUCKET` and `OBJECT_BACKUP_MINIO_PREFIX` are checked against a real
object before any copying starts: the run stats one object the manifest names
and aborts with the exact path it tried if MinIO does not hold it there. A
wrong value in an env file is as fatal as a wrong constant in the code — what
makes it survivable is failing in seconds rather than after a multi-day seed
that 404s everything and leaves an empty mirror.

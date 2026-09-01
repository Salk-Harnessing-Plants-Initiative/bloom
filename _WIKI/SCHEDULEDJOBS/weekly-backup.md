# Weekly Postgres backup to Box

The **Weekly Postgres backup** GitHub Actions workflow dumps the Supabase
Postgres database once a week and pushes it to Box via rclone.

The workflow SSHes to the deploy host and runs
`scheduled-jobs/weekly-backup/backup.py` there, the same way `deploy.yml`
reaches the server. Nothing is dumped onto the runner and no database port is
opened.

Scope is the **database only**. MinIO object storage will be backed up
separately by an object-level rclone sync — that job is still in progress, so
until it lands, `storage.objects` is restorable as a catalogue of pointers to
files nothing is backing up.

## What a run produces

Two artifacts per run, sharing one UTC timestamp:

| Artifact                           | Contents                                                                                |
| ---------------------------------- | --------------------------------------------------------------------------------------- |
| `postgres-<db>-<timestamp>.sql.gz` | The `postgres` database: schema, data, ownership, `GRANT`s                              |
| `globals-<timestamp>.sql.gz`       | `pg_dumpall --globals-only` — the roles the dump's `OWNER`/`GRANT` statements reference |

The globals file also carries each login role's password hash, so treat it as
credential material, not just a list of names.

Two files rather than one because roles live outside any single database. A
dump taken with `--no-owner --no-privileges` restores into a database whose
tables are owned by whoever ran `psql` and whose grants are gone — the RLS
policies survive, but the roles they name no longer have the access those
policies assume. Bloom manages grants as a tracked capability, so that loss is
not acceptable in a backup.

## Schedule

|             |                                                         |
| ----------- | ------------------------------------------------------- |
| Runs        | Sunday 02:17 UTC, automatically, against **production** |
| Scope       | Production only — staging is not backed up              |
| Destination | `box:bloom-backups/prod/<timestamp>/`                   |

Run it by hand any time from the Actions tab: **Weekly Postgres backup → Run
workflow**, optionally ticking "dry run" to dump and verify without uploading.

There is deliberately no staging target. `docker-compose.prod.yml` pins the
compose project name, so a staging run would read staging's env file — and so
write to staging's Box folder — while still resolving the **production**
container. Rehearse with a dry run against production instead.

## Nothing is ever deleted on Box

This job uploads and nothing else. It does not delete, overwrite, or age out
anything in the Box folder. Old backups stay until someone removes them by
hand, deliberately.

Two consequences worth knowing:

- The folder grows by one dated subfolder a week, forever. Check on it
  occasionally and prune by hand; if the Box quota ever fills, the upload is
  what starts failing.
- Each run's two artifacts go up as a single copy into
  `bloom-backups/prod/<timestamp>/`. A run that dies mid-upload then leaves a
  visibly incomplete folder, rather than a database dump with no globals beside
  it — unusable on its own, but indistinguishable from a good backup in a flat
  listing.
- On the server nothing accumulates. Each run works inside a temporary
  directory removed when the run returns or raises; a cancelled run or a
  `timeout-minutes` kill is caught by a signal handler, and anything a `SIGKILL`
  or a power cut leaves behind is swept by the next run. A leftover dump is
  therefore possible for at most a week, which matters because it is a full
  plaintext copy including `auth.users`.

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

### 4. Prove it with a dry run first

Dispatch the workflow with "dry run" ticked. That runs the real SSH hop, the
real container resolution and a real `pg_dump`, verifies both artifacts, and
stops before uploading — `pg_dump` only reads, so this is safe against
production. Then run it for real and confirm both artifacts land on Box.

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

| Code | Meaning                                                     |
| ---- | ----------------------------------------------------------- |
| 0    | Verified backup uploaded                                    |
| 1    | Subprocess failed (docker / pg_dump / gzip / rclone)        |
| 2    | Configuration problem, or the stack is not running          |
| 3    | An artifact failed verification — missing, short or corrupt |

Code 3 is the one to read closely: the dump ran, but what came out cannot be a
usable backup. Look at the database, not at the config.

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

**Not documented here.** This job's scope is producing verified dumps and
storing them on Box. Recovering from one is separate work and has not been
rehearsed, so there is no procedure on this page to follow.

A trial restore found that a plain `psql` load of these artifacts reports
success while leaving errors behind, and that loading the globals artifact can
overwrite the target cluster's role passwords. Treat a recovery as work to
plan, not to improvise from this page.

To list or fetch what is stored:

```bash
sudo -u bloom-deploy rclone lsl box:bloom-backups/prod
```

The dump contains `auth.users`. Do not leave a copy on disk.

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
- **A production deploy must have run first.** The workflow checks nothing out;
  it runs whatever `scheduled-jobs/weekly-backup/backup.py` the deploy
  directory holds, and reads the `BACKUP_*` keys out of the `.env.prod` that
  each deploy reassembles. Deploys here are manual, so the first Sunday after
  the promotion can arrive before one has happened — that run fails with "No
  such file or directory", which is loud and harmless.
- **pgmq queues are not in the dump.** `pgmq.create()` registers
  `pgmq.q_cyl_pipeline_dispatch` and its archive as extension members, and the
  pinned pgmq never calls `pg_extension_config_dump`, so `pg_dump` emits
  neither their definitions nor their rows. The durable record —
  `cyl_pipeline_runs`, `cyl_pipeline_run_scans` — is captured; what is lost is
  in-flight dispatch messages. Note the dump _does_ contain the wrapper
  functions that call the queue, so after restoring anywhere, run
  `SELECT pgmq.create('cyl_pipeline_dispatch');` or the dispatch path fails at
  runtime with no other warning.
- **The `_supabase` database is not in the dump.** `pg_dump` covers one
  database and the cluster has two; `_supabase` holds Supavisor's pooler tenant
  config, which its own migrations rebuild on boot.

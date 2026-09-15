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

|             |                                                    |
| ----------- | -------------------------------------------------- |
| Runs        | Sat evening at Salk — 02:17 UTC Sun, automatically |
| Scope       | The schedule backs up production                   |
| Destination | `box:bloom-backups/prod/<timestamp>/`              |
| Rehearsal   | Staging, on demand — `box:bloom-backups/staging/`  |

Cron is always UTC and never shifts for daylight saving, so the schedule reads
`Sunday 02:17` while the run actually lands **Saturday 19:17 PDT** in summer and
**Saturday 18:17 PST** in winter. If you are waiting to watch the first one,
watch on Saturday evening.

Run it by hand any time from the Actions tab: **Weekly Postgres backup → Run
workflow**. Pick the stack (`prod` is the default), and optionally tick "dry
run" to dump and verify without uploading.

## Rehearsing on staging

Staging exists as a target so production is never the first real run. A staging
run exercises the whole path — dump, verify, upload to Box, job summary — against
a database nobody depends on, and lands in `box:bloom-backups/staging/`.

**Do this before the first production run**, and again after any change to how
the dump is taken. What it tells you that a dry run cannot: whether the upload
credential works, what a real artifact looks like on Box, and roughly how long
the whole thing takes.

**Dispatch it with the `staging` branch selected, not `main`.** A staging run
resolves to the `staging` GitHub Environment, whose deployment branch policy
admits only the branch of the same name — dispatched from `main` it fails
immediately with "Branch main is not allowed to deploy to staging", having run
no steps. That environment also requires a reviewer, so the run waits for an
approval; for a rehearsal somebody is there to give it. The production paths are
unaffected: the schedule uses `production-scheduled-backup` and a manual
production dispatch uses `production`, neither of which restricts the branch
this way.

What it does **not** tell you is whether production's dump fits on disk: the
working copy is written to the deploy host before upload, and staging's database
is far smaller. The job checks free space itself before every dump and refuses
to start below `BACKUP_MIN_FREE_BYTES` (20 GiB by default), because the working
copy shares a filesystem with the database's own data directory and filling it
would stop Postgres writing. The floor is set at about three times what a
compressed dump of this database currently weighs — 6-7 GB — which leaves room
for it to grow considerably before the floor stops being larger than an
artifact. Revisit it if a dump ever approaches the floor: too high means no
backups at all, too low is no protection. Each run's artifact sizes are in the
job summary, so the number to check against is always the last run's.

By hand on the server, same thing:

```bash
sudo -u bloom-deploy python3 scheduled-jobs/weekly-backup/backup.py \
  --env staging --deploy-dir /path/to/staging/deploy --dry-run
```

### How a run knows which stack it is talking to

Both environments run `docker-compose.prod.yml` on the same host, told apart
only by the compose project name — and that file pins production's. `deploy.yml`
passes `-p bloom_v2_staging` to bring staging up and lets the pinned name stand
for production, so `backup.py` maps each environment to the same project and
passes `-p` when it resolves the database container.

Without that `-p`, compose falls back to the pinned name and a staging run would
read staging's settings — and so staging's Box folder — while dumping the
**production** database, logging "staging" at every line. A test asserts the
mapping still matches what `deploy.yml` actually uses, so renaming a project
there fails here rather than silently pointing this job at the wrong stack.

## The backup and a deploy must not overlap

A dump taken while a deploy is applying migrations captures a half-migrated
schema. Nothing in either workflow's `concurrency:` block prevents that — they
use separate groups on purpose, so that a queued deploy cannot cancel a backup
and a backup cannot hold up a deploy.

What actually prevents the overlap is that **both jobs run on the same single
self-hosted runner** (`[self-hosted, linux, salk-network]`) and therefore queue
behind one another. That is an operational fact, not a declared one: register a
second runner on that label and the protection disappears silently, with
nothing failing to announce it. If a second runner is ever added, give the two
workflows a shared concurrency group, or add a deploy-in-progress check to this
one, in the same change.

The same shared runner is also why the Box credential's blast radius is wider
than this job: the rclone token lives in `~/.config/rclone/rclone.conf` on the
deploy host and is readable by anything that host runs as that user, and any
job scheduled onto the runner — `deploy.yml`,
`refresh-cyl-experiment-trait-counts.yml`, or a future one — reaches the same
host by the same key. This job is not isolated from the runner, and the token is
not isolated from other jobs.

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

The same env file supplies the database password. `db-prod` authenticates every
connection, including one opened from inside the container, so the dump reads
`POSTGRES_PASSWORD` from `.env.<env>` — the value `deploy.yml` writes there from
the environment's `*_POSTGRES_PASSWORD` secret — and hands it to `docker exec`
by environment rather than on the command line, so it stays out of the host's
process list. Nothing extra to configure; a missing value exits 2 before the
dump starts.

`BACKUP_STATE_DIR` is where each run's working copy is written before upload,
and on the deploy host it points into `/data` — `/data/bloom/backup-work/<env>`,
one directory per environment. That is deliberate on three counts: the data
volume has room for a dump where the root filesystem does not, filling the root
filesystem would take docker, sshd and the Actions runner with it, and keeping
the directory outside `/data/bloom/<env>` means a deployment's checkout handling
can never touch a plaintext dump. The script's own fallback if the key is unset
is under the invoking user's home — fine for a laptop, wrong for this host,
which is why both defaults files set it explicitly.

Seven keys are read out of `.env.<env>` and nothing else — `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`, `BACKUP_STATE_DIR`,
`BACKUP_MIN_FREE_BYTES`, `BACKUP_RCLONE_REMOTE` and `BACKUP_RCLONE_DEST_DIR`. The rest of the file stays out of the job's
environment, so nothing in it reaches rclone or any other child process.
`POSTGRES_DB` also has to be a plain database name, because it becomes part of
the artifact's filename.

### 2. Create the working directory on the data volume

`BACKUP_STATE_DIR` is created by hand, once per host — the job will not build
the path it is given, so a typo cannot quietly leave a plaintext dump in a
directory nobody is watching. It fails at exit 2 naming the missing path
instead.

```bash
mkdir -m 700 /data/bloom/backup-work
mkdir -m 700 /data/bloom/backup-work/prod /data/bloom/backup-work/staging
```

As the deploy user, which must own them. Every run re-applies `0700` to the
directory it is given, because it persists between runs and holds a full
plaintext dump — but a POSIX ACL survives a `chmod`, so check `getfacl` on a
host where `/data` grants default ACLs.

Each artifact is created `0600` rather than tightened after the fact, so its
mode does not depend on the host's umask or an inherited default ACL, and the
file is owner-only for the whole time it is being written rather than only once
it is finished. The `0700` directories are what stop other accounts reading it
today; the file mode is what keeps one loosened directory from being enough on
its own.

### 3. Create the `production-scheduled-backup` GitHub Environment

Settings → Environments → New environment, named exactly
`production-scheduled-backup`. **Leave it with no required reviewers and no wait
timer.**

This exists because a scheduled run routed through `production`'s own approval
gate would sit "Waiting" for an approval nobody gives at 02:00 on a Sunday — the
backup would look configured and silently never run. Manual dispatches still go
through the real `production` environment and its gates.

### 4. Promote the workflow to `main`

Neither `schedule:` nor `workflow_dispatch` fires until the workflow file exists
on the repo's **default branch**. Both triggers are gated on it. Until the
normal staging → main promotion carries this file across, nothing runs and the
workflow does not appear in the Actions tab at all.

### 5. Prove it with a dry run first

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

| Code | Meaning                                                               |
| ---- | --------------------------------------------------------------------- |
| 0    | Verified backup uploaded                                              |
| 1    | Subprocess failed (docker / pg_dump / gzip / rclone)                  |
| 2    | Configuration problem, the stack is not running, or `--env` is not a known environment |
| 3    | An artifact is missing, or too small to be a real dump                |
| 4    | The run was terminated by a signal — see below, it is not what a cancelled workflow run produces |

Code 2 also covers a deploy host with too little free space for a dump. That
is checked before the dump starts, so the run costs seconds rather than failing
partway with a half-written artifact.

Code 3 means the dump ran and produced almost nothing. In practice that is a
configuration problem rather than a database one — `POSTGRES_DB` naming the
wrong database, or a container resolved from the wrong stack — so start with
`.env.<env>` and the resolved container named in the log. Nothing checks the
artifact for corruption; that is what checking it on Box is for.

Code 4 is not a failure of anything the job ran. It gets its own code because a
`SystemExit` carrying a message exits 1, which would make a terminated run
indistinguishable from a failed `pg_dump`.

**It is not what cancelling a workflow run produces.** The workflow reaches the
script over `ssh` without a TTY, so cancelling the run — or hitting
`timeout-minutes` — kills the local ssh client and nothing else: sshd does not
signal a non-PTY remote command. The script carries on, finishes its dump, and
**still uploads**. So a red run is not proof that nothing reached Box; check the
folder listing in the job summary, or Box itself, before concluding a week is
missing.

Code 4 comes from a `SIGTERM` or a `SIGHUP` — `kill` on a by-hand run, or the
process group going away. **Ctrl-C is not one of them.** That sends `SIGINT`,
which the script does not handle, so you get Python's own interrupt message and
exit 130. The working directory is still cleaned up either way; only the exit
code differs.

The dump waits at most 60 seconds for any table lock (`--lock-wait-timeout`).
Only DDL conflicts with what `pg_dump` takes, and a deploy's migrations cannot
overlap a backup while both run on the one shared runner — so a run that fails
this way means something else is holding a lock, and a re-run is the answer.
The wait is deliberately short because a queued `pg_dump` blocks every other
reader on that table behind it.

## What is checked before an upload counts

Deliberately cheap. The job runs on the host that serves production, so the
checks it makes are the ones that cost nothing:

- both processes in the `pg_dump | gzip` pipeline must exit 0 — a shell
  pipeline reports only the last, which is how a failed dump wrapped in valid
  gzip passes for a good backup. A `pg_dump` that dies part-way exits non-zero,
  so this is what catches a truncated dump;
- each artifact must exist and clear a minimum-size floor — a `stat`, and what
  stops a 0-byte pair being uploaded;
- the sizes are logged and appear in the run summary.

Any failure ends the run without uploading anything.

**The dump's contents are not inspected here, on purpose.** An earlier version
decompressed each artifact twice — once for a `gzip -t` integrity pass and once
more to count rows and look for the dumper's completion line. On a multi-gigabyte
artifact that is two full decompressions on the machine running Postgres and
every other service, immediately after a dump that has already worked the disk
hard. The cheap checks above already catch a failed or empty dump, and the
remaining case — a dump that exits 0 but is silently wrong — is caught by
checking the artifacts on Box instead, away from the live host.

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

- **A full Box quota, or a partial upload, is not verified against.** The job
  checks what it produced, not what arrived. If Box fills or the transfer dies
  after the retries are exhausted, the run fails and the summary shows the
  dated folder as incomplete or absent — but nothing re-reads the uploaded
  bytes back off Box to confirm them. The weekly glance at the listing is what
  catches it.
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

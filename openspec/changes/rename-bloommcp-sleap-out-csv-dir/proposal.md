## Why

`docker-compose.prod.yml` (the compose file used for **both** staging and prod — there is no
separate staging compose) bind-mounts three directories into the `bloommcp` container:
`SLEAP_OUT_CSV` (`BLOOM_TRAITS_DIR`), `PLOTS_DIR` (`BLOOM_PLOTS_DIR`), `ANALYSIS_OUTPUT`
(`BLOOM_OUTPUT_DIR`). #477 asked us to confirm whether `SLEAP_OUT_CSV`/`ANALYSIS_OUTPUT` are
dead weight and either stop mounting them or rename them for clarity.

**Investigation result: neither is dead weight.** Both back reachable code paths in the
deployed (Supabase-backend) `bloommcp` container today:

- **`BLOOM_TRAITS_DIR` (`SLEAP_OUT_CSV`)** backs live, non-demo paths, not just the demo tools
  the issue named:
  - `SupabaseReader.load_experiment`'s raw-input fallback tier (`data_access/supabase_reader.py`).
    This is **not** merely a cold-start/first-run path: `qc_clean.py` forces `version="raw"` on
    **every** call — "qc_clean is the producer of cleaned data, so it must always clean from the
    raw input, never re-clean a prior cleaned artifact" — so this directory is required for every
    `qc_clean` invocation, for every experiment, indefinitely.
  - `SupabaseReader.raw_source_path`, used by `tools._ports.start_run` to content-hash the raw
    input into `input_sha256` run provenance.
  - A second, **direct** bypass the issue itself pointed at but this investigation initially
    under-chased: `sections/sleap_roots/analysis/qc_inspect.py:503` (`local_src = TRAITS_DIR /
params.experiment`) reads straight off `BLOOM_TRAITS_DIR`, bypassing the `ExperimentReader`
    port entirely — confirmed still present at its post-refactor location.
  - The `phenotyping_segmentation` section's `compute_min`/`compute_median`/`compute_mode` tools,
    which read `.txt` files from `BLOOM_TRAITS_DIR` directly. `server.py`'s docstring calls this
    section "empty scaffold today", but `sections/phenotyping_segmentation/__init__.py` registers
    all four tools and every section is mounted onto the combined `/mcp` surface — the docstring
    is stale, the tools are reachable.
- **`BLOOM_OUTPUT_DIR` (`ANALYSIS_OUTPUT`)**:
  - Same `phenotyping_segmentation` tools write their `results/` output here.
  - `storage_backend._resolve_local_root`'s deprecated bridge fallback for
    `BLOOM_STORAGE_LOCAL_ROOT` when `BLOOM_STORAGE_BACKEND=local` — **dormant** in staging/prod
    today, since neither compose file sets `BLOOM_STORAGE_BACKEND` (default is `supabase`). This
    confirms the dormant half of the issue's premise, but not the live half.
  - **Refuted:** the issue's third cited consumer — "one legacy outlier-comparison plot in
    `viz_tools.py`" — no longer exists. `git log` (verified against full commit hashes) shows
    `viz_tools.py`'s last `ANALYSIS_OUTPUT`-writing plot was dropped in commit `5ce48af`
    (2026-07-20 17:12:59 -0700, "C4 — repoint viz_tools to sleap_roots_analyze, drop 2 plots"),
    and the file itself was deleted entirely in the subsequent "Phase 2 — converge tool
    organization on sections/" refactor (`779c1d6`, 2026-07-20 17:15:52 -0700). Both landed about
    an hour after #477 was filed (2026-07-20T23:12:38Z UTC), so the issue's snapshot was accurate
    at filing time but is stale now.

Per the issue's own decision tree, "if kept: rename for clarity so the env var and folder name
match." Checking all three mounts against their own env vars: `PLOTS_DIR` already matches
`BLOOM_PLOTS_DIR`, and `ANALYSIS_OUTPUT` is a reasonable match for `BLOOM_OUTPUT_DIR`. Only
`SLEAP_OUT_CSV` is actually mismatched — it names a tool + file format instead of purpose, and
doesn't match its own env var (`BLOOM_TRAITS_DIR`) at all.

**One loose end this investigation surfaced but does not resolve:** `supabase_reader.py`'s module
docstring (lines 1-9) says its deprecated raw-read fallback exists "so the follow-up... can remove
it," while its own runtime `_LOCAL_RAW_DEPRECATION` warning text (lines 31-35) says the path is
"promoted, not slated for removal" — the file disagrees with itself about this consumer's intended
lifespan. That reconciliation belongs to #476 (below), not this rename.

**Deployment-sequencing risk:** `bloommcp/data/` is gitignored, so the `deploy.yml` workflow's
`git reset --hard` never touches it — a real production/staging host's already-populated
`bloommcp/data/SLEAP_OUT_CSV/` would NOT be renamed by a normal deploy of this change. Docker
would instead silently auto-create an empty `bloommcp/data/TRAITS_DIR/`, and because `qc_clean`
requires raw input on every call (not just an experiment's first run — see above), this would
break `qc_clean` for the entire deployed system until someone manually migrated the host
directory. This proposal therefore includes a deploy-workflow migration step as in-scope.

**Blended with #474 in the same PR (#495), not a separate one — this is a correction from this
proposal's original draft, not the original plan.** #474 (docker-compose.prod.yml's bind-mount
_permission_ bug) landed a `deploy.yml` preflight (`scripts/ensure_bloommcp_data_dirs.sh`,
originally from #472/#473's dev-only fix) in the exact same two deploy jobs, at the exact same
insertion point, right before this proposal's own migration step needed to land. The two are
genuinely complementary, not just adjacent: the migration step (this proposal) is what makes the
rename safe on an **already-populated** host (preserves existing raw-CSV data by renaming rather
than losing it to Docker's create-fresh-empty-directory default), while #474's preflight is what
makes the **result** of that rename safe on a **genuinely fresh** host (guarantees `TRAITS_DIR`
exists and is writable either way). Composing them requires one thing this proposal's original
draft did not anticipate (because #473 hadn't merged when this draft was written, so
`scripts/ensure_bloommcp_data_dirs.sh` did not exist yet to consider): **ordering**. The migration
step MUST run before #474's preflight step in both `deploy-production` and `deploy-staging` — if
the preflight ran first, its `mkdir -p` would create an empty `TRAITS_DIR` before the migration
step ever saw the host, and the migration's own `[ ! -e bloommcp/data/TRAITS_DIR ]` guard would
then (correctly, per its own logic) skip renaming the real, populated legacy directory, silently
orphaning it — reintroducing the exact failure mode this proposal exists to prevent. Implemented
with that ordering; pinned by
`tests/unit/test_deploy_data_dir_preflight_ordering.py::test_deploy_jobs_provision_data_dirs_before_compose_up`
(shared with #474, since asserting the ordering invariant lives naturally next to the preflight
ordering assertions it composes with). Also required updating
`scripts/ensure_bloommcp_data_dirs.sh` itself (and its test,
`tests/unit/test_bloommcp_data_dirs.py`) to provision `TRAITS_DIR` instead of `SLEAP_OUT_CSV` —
the original draft assumed this script "no longer exists in the repo" and "needs to be written
fresh" (true at draft time, false now that #473 has merged); reusing and updating it instead
avoids introducing a second, divergent preflight implementation.

## What Changes

- **Keep mounting all three directories in `docker-compose.prod.yml`** — none are dead weight;
  removing any would break a reachable code path on the deployed Supabase backend.
- **Rename `bloommcp/data/SLEAP_OUT_CSV` → `bloommcp/data/TRAITS_DIR`** (container path
  `/app/data/SLEAP_OUT_CSV` → `/app/data/TRAITS_DIR`) so the folder name matches its own env var,
  consistent with `PLOTS_DIR`/`BLOOM_PLOTS_DIR` and `ANALYSIS_OUTPUT`/`BLOOM_OUTPUT_DIR`. Updated
  everywhere the name appears: `docker-compose.dev.yml`, `docker-compose.prod.yml`,
  `bloommcp/Dockerfile`, `.gitignore`'s comment, `bloommcp/docs/storage-backends.md`,
  `bloommcp/docs/local-validation.md`, `_WIKI/BLOOMMCP/storage-workflow.md`,
  `_WIKI/BLOOMMCP/README.md`, `DEV_SETUP.md`, `PROD_SETUP.md`, `openspec/project.md`, the comment
  in `bloommcp/src/bloom_mcp/storage/analysis_dir.py`, `bloommcp/scripts/live_plot_tool_smoke.py`,
  and `scripts/ensure_bloommcp_data_dirs.sh` (+ its test) — the last two because #473 merged
  after this proposal's original investigation, adding a script and a smoke-test script that both
  named the directory explicitly.
- **Add an idempotent host-side migration step to `.github/workflows/deploy.yml`**, in both
  `deploy-production` and `deploy-staging` jobs, run before `docker compose ... up` (and, per the
  ordering requirement above, **before** #474's data-dir preflight step): if
  `bloommcp/data/SLEAP_OUT_CSV` exists and `bloommcp/data/TRAITS_DIR` does not, rename it in place;
  otherwise no-op. This is what makes the prod-compose rename safe to actually deploy — see the
  `bloommcp-deployment-data-mounts` capability's "Deploy-Time Directory Rename Is Migrated, Not
  Orphaned" requirement.
- **Add regression tests** (new file `tests/unit/test_bloommcp_data_mount_rename.py`, following
  this repo's existing `tests/unit/test_compose_dev_env_files.py` shape-test convention):
  - Both compose files agree the `TRAITS_DIR` name/path match `BLOOM_TRAITS_DIR` (catches a
    half-done rename).
  - `docker-compose.prod.yml`'s `bloommcp` service still mounts all three directories (encodes
    "Prod/Staging Data Mounts Remain Necessary" as an enforceable CI check, not just a spec
    narrative).
  - A permanent fence asserting the literal string `SLEAP_OUT_CSV` is absent from the 8 renamed
    files (upgrades the one-time manual grep sweep into a standing CI guard).
  - Extended `tests/unit/test_deploy_data_dir_preflight_ordering.py` (shared with #474) with the
    migration-precedes-preflight ordering assertion described above.
- **No env var rename** — `BLOOM_TRAITS_DIR` and `BLOOM_OUTPUT_DIR` keep their current names;
  only the physical directory they point at is renamed. Every consumer resolves its path through
  the env var, never a hardcoded directory name, so no application code changes.
- **Not in scope:** `langchain/SLEAP_OUT_CSV/` (a different, unrelated directory in a different
  service — tracked by #475, already handled on a separate branch); reconciling
  `supabase_reader.py`'s self-contradicting docstring/deprecation-warning text, or retiring the
  `BLOOM_TRAITS_DIR` bypasses themselves (both belong to #476).

## Impact

- Affected specs: **new capability** `bloommcp-deployment-data-mounts` — **ADDED** three
  requirements: directory naming matches its env var; the three prod/staging mounts remain
  necessary (with an enforceable CI-check scenario, and a note pointing at #476 as the trigger to
  re-evaluate this requirement); and deploy-time renames are migrated, not orphaned (including the
  ordering requirement relative to #474's `deploy-health-check` preflight requirement).
- Affected code: `docker-compose.dev.yml`, `docker-compose.prod.yml`, `bloommcp/Dockerfile`,
  `.gitignore`, `bloommcp/docs/storage-backends.md`, `bloommcp/docs/local-validation.md`,
  `_WIKI/BLOOMMCP/storage-workflow.md`, `_WIKI/BLOOMMCP/README.md`, `DEV_SETUP.md`,
  `PROD_SETUP.md`, `openspec/project.md`, `bloommcp/src/bloom_mcp/storage/analysis_dir.py`,
  `bloommcp/scripts/live_plot_tool_smoke.py`, `scripts/ensure_bloommcp_data_dirs.sh`,
  `.github/workflows/deploy.yml` (new migration steps, ordered before #474's preflight steps),
  `tests/unit/test_bloommcp_data_mount_rename.py` (new), `tests/unit/test_bloommcp_data_dirs.py`
  (updated for the renamed directory), `tests/unit/test_deploy_data_dir_preflight_ordering.py`
  (shared with #474, extended with the ordering assertion).
- No consumer code changes and no env var renames — every consumer already resolves its path
  through `BLOOM_TRAITS_DIR`/`BLOOM_OUTPUT_DIR`, never a hardcoded directory name. The existing
  bloommcp test suite (`test_storage_backend.py`, `test_local_mode.py`, `test_package_baseline.py`)
  is expected to pass unchanged — none of it asserts against the literal directory name, which is
  exactly why the new shape/fence tests above are being added instead of relied-upon-by-omission.
- **Historical data note:** manifest.json records written before this change persist the old
  absolute container path in their write-once `source_path` field (see
  `_WIKI/BLOOMMCP/storage-workflow.md`'s example, `"source_path":
"/app/data/SLEAP_OUT_CSV/plant_traits.csv"`). Confirmed inert today — nothing reads this field
  back for verification or display — so this is accepted as permanent, harmless staleness in old
  records, not fixed retroactively.
- **Developer note (not a repo change):** `bloommcp/data/SLEAP_OUT_CSV/` is gitignored, runtime-only
  local state — anyone with a pre-existing local copy should rename it to
  `bloommcp/data/TRAITS_DIR/` (or re-run whatever seeds it) after pulling this change.
- Related: **#476** (open — "finish roadmap Tier 2: retire remaining legacy local
  `BLOOM_TRAITS_DIR` read bypasses," i.e. `supabase_reader.py`'s fallback and `qc_inspect.py`'s
  direct read) is a **sequencing-relevant** sibling: it targets retiring the very two consumer
  paths this proposal cites as justification for keeping `TRAITS_DIR` mounted. If #476 lands
  first, "Prod/Staging Data Mounts Remain Necessary" should be re-evaluated — this proposal's spec
  delta calls that out explicitly rather than leaving it implicit. **#478** (open — move
  `BLOOM_STORAGE_BACKEND` toggle out of tracked `docker-compose.dev.yml`) touches the same region
  of `docker-compose.dev.yml` this change edits; **#479** (open — collapse
  `BLOOM_TRAITS_DIR`/`PLOTS_DIR`/`OUTPUT_DIR` into one `BLOOM_LOCAL_ROOT` var) would make this
  rename short-lived if it lands — neither blocks this change, both are worth the implementer
  checking for merge conflicts against. **#474** (prod bind-mount _permissions_ — blended into
  this same PR, see "Why" above), **#475** (unrelated `langchain/SLEAP_OUT_CSV/` dead-data
  removal, separate service, separate branch), **#472**/**#473** (the dev-side permission fix
  whose script this change also updates for the rename). Also a documentation-only coordination
  note: `openspec/changes/add-ghcr-image-publishing/` (open, unarchived) has stale line-anchor
  references to the old path/line-numbers in its `proposal.md`/`tasks.md` — worth a one-line PR
  heads-up, not a blocking dependency.
- **Branch/PR:** blended into `egao28/bloommcp-prod-staging-data-dir-preflight-474` (PR #495,
  which also implements #474) rather than a separate branch/PR — see "Why" above for the
  sequencing reason. Closes #477.

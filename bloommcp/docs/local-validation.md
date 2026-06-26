# Local validation — `make bloommcp-smoke`

The Supabase-free unit suite (`uv run pytest`) runs the bloom-mcp tools against in-memory
fakes. **`make bloommcp-smoke`** is the complementary *live* check: it drives real tools
end-to-end through the deployed `SupabaseReader` / `SupabaseResultStore` adapters against a
running dev stack, so CI and local pre-merge prove the real write/read path — not just the
fakes. The same target backs the `dev-stack-smoke` CI job, so local and CI never drift.

## Prerequisites

1. The dev stack is **up and migrated** (the `migrate-local` step creates the
   `bloommcp-data` bucket and applies the storage grants the write path needs):
   ```bash
   make dev-up
   make migrate-local
   ```
   See [DEV_SETUP.md](../../DEV_SETUP.md) for first-time setup (WSL2, `.env.dev`, MinIO).
2. `.env.dev` exists and has a non-empty `BLOOM_AGENT_KEY` (written by `make init`). The
   smoke target sources it and never echoes it.
3. `uv` is installed (the target runs the driver via `uv run`).

## Running

From the **repo root**:

```bash
make bloommcp-smoke
```

The target bridges the host↔container gap: `.env.dev` points `SUPABASE_URL` at the
in-container gateway (`http://kong:8000`) and `BLOOM_*_DIR` at `/app` paths, so the target
derives the host gateway from `KONG_HTTP_PORT` (`http://localhost:$KONG_HTTP_PORT`) and seeds
host temp dirs with the test fixtures before launching
[`scripts/live_persistence_smoke.py`](../scripts/live_persistence_smoke.py). It fails fast
with an actionable message if the stack is down, `.env.dev` is missing, or `BLOOM_AGENT_KEY`
is empty.

Every assertion is printed as a named `OK` / `FAIL` line; any failure (workflow error, hash
mismatch, read-after-write miss after a bounded retry, import leak) routes through the
per-check summary and a non-zero exit — never an unlabelled traceback.

## What it validates

The driver first checks the **Tier-0 import-clean guarantee** (`import bloom_mcp` is clean in
a subprocess with the Supabase env scrubbed), then runs two legs through the real ports:

### Leg 1 — clustering (Tier-2 persistence, stochastic)

Drives `run_clustering_workflow("turface.csv", algorithm="kmeans")` (resolves `seed=42`) and
asserts the committed run's manifest is **schema v3** with a real `seed == 42`,
`agent == "bloom_agent"`, a populated `environment`, and matching `output_sha256` /
`output_keys` — and that each recorded `output_sha256` equals the SHA-256 of the bytes
actually stored. A second run advances `latest` by exactly one version without clobbering the
first.

### Leg 2 — `qc_clean` (Tier-3 QC foundation, deterministic)

Seeds the raw `turface_19_raw_data.csv` fixture as `turface_raw.csv`, then runs
`qc_clean(experiment="turface_raw.csv", max_nans_per_trait=0.1)` through the real ports and
asserts:

- the committed run's outputs include **`_cleaned.csv`** and **`cleanup_log.json`**;
- the run's manifest is **`manifest_schema_version == 3`**;
- each recorded `output_sha256` matches the actual stored bytes for **both** artifacts;
- `SupabaseReader().load_experiment("turface_raw.csv", require_clean=True)` then resolves the
  committed **cleaned** version (`source` is `v<N>_cleaned`, **not** `raw`);
- the resolved cleaned frame has **zero NaN cells** in its trait columns
  (`df[trait_cols].isna().sum().sum() == 0`).

This is the `qc_clean` → `pca_analysis(require_clean=True)` composition proven over the real
storage round-trip rather than the in-memory fakes.

> **Note on raw inputs.** The deployed reader currently resolves *raw* experiment inputs from
> the local `BLOOM_TRAITS_DIR`, so the qc_clean leg seeds `turface_raw.csv` there (matching
> the clustering leg's fixture-upload pattern). When raw inputs migrate to the
> `bloommcp_input/` storage prefix, the leg's upload moves to that bucket.

A green run ends with:

```
SMOKE PASSED ✅ — clustering(kmeans) seed-bearing run AND qc_clean (Tier 3) cleaned run
both persist full v3 provenance through the real ports.
```

## Unit tests (no live stack)

The driver's pure decision logic — manifest/provenance assertions, the hash-compare loop,
version-advance detection, the qc_clean persist/read checks, and the summary/exit aggregation
— is factored into importable helpers and unit-tested with **no** Supabase:

```bash
cd bloommcp && uv run pytest tests/scripts/test_live_persistence_smoke_logic.py
```

## Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `Error: dev stack not running` | `make dev-up` (then `make migrate-local`). |
| `Error: BLOOM_AGENT_KEY is empty in .env.dev` | Run `make init` to (re)generate `.env.dev`. |
| `FAIL ... sha256 matches stored bytes` | A real write-path regression — bytes stored differ from the recorded hash. |
| `... read-back attempt N/5 failed` then a `FAIL` | Read-after-write lag exceeded the bounded retry; check `storage` / `db-dev` health (`make dev-logs`). |
| `FAIL qc_clean: require_clean read resolves the cleaned artifact (not raw)` | The reader fell back to the raw input — the `qc` run did not commit or the manifest is unresolvable. |

See also [DEV_SETUP.md](../../DEV_SETUP.md) (host vs container URLs, migrations) and the
`bloommcp-smoke` target in the repo-root [Makefile](../../Makefile).

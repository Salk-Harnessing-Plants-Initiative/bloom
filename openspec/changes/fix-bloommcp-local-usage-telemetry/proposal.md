## Why

[#641](https://github.com/Salk-Harnessing-Plants-Initiative/bloom/issues/641) — running
bloommcp fully local (`BLOOM_STORAGE_BACKEND=local`, no Supabase credentials) still spews a
full `RuntimeError` traceback to the server terminal after **every single request**, even a
fully successful one (200 OK, correct output). Two compounding bugs, both confirmed still
present on `staging`:

1. **Local mode still attempts the network call at all.** `IdentityMiddleware`
   (`bloommcp/src/bloom_mcp/identity.py:251-257`) calls `record_usage_async(...)`
   unconditionally for every qualifying request, with no check for
   `storage_backend.is_local_backend()` — the same accessor `experiment_utils.py` and
   `data_access/local_reader.py` already gate their own local-mode behavior on. In local mode
   this always fails: `record_usage_async` → `_do_record` → `supabase_client.call_rpc(...)` →
   `_require_env()` raises `RuntimeError: SUPABASE_URL and BLOOM_AGENT_KEY required ... but
unset`, since those are never set in local mode by design.
2. **The failure is logged as a full traceback, not a warning.** `_do_record`'s except clause
   (`bloommcp/src/bloom_mcp/usage.py:94-99`) uses `logger.exception(...)`, which always
   includes the full stack trace. This fires on a background thread after every qualifying
   request, so a fully successful analysis run still floods the terminal with what looks like
   a crash — undermining confidence in an otherwise-working local setup, independent of bug 1.

Separately, this bug is directly relevant to the "no connection to the shared server at all" /
"nothing shared with anyone else" framing bloommcp's docs currently use for local/offline mode
(`bloommcp/docs/connecting-claude-code.md:78-81`, `bloommcp/docs/storage-backends.md:130-131`).
Strictly, no _experiment data_ ever left the machine — the RPC failed in `_require_env()`
before any network I/O was attempted — but bug 1 means a network call _was_ attempted (a
real, if immediately-failing, code path) on every request, which an absolute "no
connection"/"nothing reaches the network" reading of that wording doesn't account for. The
docs should make the narrower, accurate claim ("no experiment data leaves your machine")
rather than an absolute no-network-activity one, since that's the guarantee local mode
actually provides — true both before and after bug 1 is fixed.

## What Changes

- Gate the `record_usage_async(...)` call in `IdentityMiddleware.__call__`
  (`identity.py:251-257`) on `not storage_backend.is_local_backend()`, mirroring the existing
  `experiment_utils.py` / `local_reader.py` gating convention. Local mode skips usage
  telemetry entirely — no RPC attempt, no failure, no log line — rather than attempting and
  failing it every request.
- Downgrade `_do_record`'s failure log (`usage.py:94-99`) from `logger.exception` (full
  traceback) to `logger.warning` naming the caught exception's _type_ (`type(exc).__name__`),
  not its message — interpolating the raw exception next to the deliberately-redacted
  identity (`_redact_identity`) would risk reintroducing the clear-text-identity logging that
  redaction exists to prevent, if a future exception's message ever echoed it back (review
  follow-up on PR #659; design.md Decision 7). Usage recording is already documented as
  best-effort/non-blocking ("can never affect the response already in flight"); its failure
  mode shouldn't look like a crash **regardless of backend** — this is not local-mode-specific,
  so it fixes the traceback noise for a transient Supabase-backend failure too, not only the
  (now-eliminated) local-mode case.
- For the same reason, also downgrade `record_usage_async`'s submission-failure except clause
  (`usage.py:124-130`, e.g. the executor rejecting work during shutdown) from
  `logger.exception` to `logger.warning`, with the same type-name-only interpolation. The
  issue's suggested fix names only `_do_record`, but leaving this second, already-adjacent
  `logger.exception` call in the same module untouched would be an inconsistent half-fix of
  the same "best-effort recording shouldn't log like a crash" rationale — both failure paths
  in this module get the same treatment.
- In `IdentityMiddleware.__call__`, import `record_usage_async` inside the
  `not is_local_backend()` branch rather than above it, so local mode doesn't even import the
  `usage` module — matching the "skip it outright" framing literally, not just in effect
  (review follow-up on PR #659).
- Reword the absolute "no connection to the shared server at all" / "nothing shared with
  anyone else" claim in `connecting-claude-code.md`'s fully-local section (lines 78-81, plus
  the same-file signpost sentence at lines 35-37 that also says "fully offline"), the "run
  fully offline" framing in `storage-backends.md`'s opt-in local-backend section (lines
  130-131), and the "(offline)" label in `_WIKI/BLOOMMCP/README.md` (line 82) — all found by
  grepping the repo for the same absolute-claim pattern, not just the two sites in the
  original bug report — to the same, consistent "no experiment data leaves your machine"
  guarantee, so the three documents don't drift out of sync with each other.

**Not in scope:** whether usage telemetry should exist at all, or record anything different,
for the `supabase` backend — that behavior (`add-bloommcp-caller-identity`,
`add-bloommcp-oauth-usage-attribution`) is unchanged here.

## Impact

- Affected specs: `bloommcp-caller-identity` (usage-recording gating + log-level — the
  `bloommcp_usage Records Caller Activity Per Mounted Surface` MODIFIED requirement is pasted
  from `add-bloommcp-oauth-usage-attribution`'s own (unarchived, but shipped — PR #613/#615)
  version, not the older `add-bloommcp-caller-identity` original, so no OAuth-attribution
  content is lost — see design.md Decision 5 for why, and for the resulting archiving-order
  note), `bloommcp-storage-backend` (documentation accuracy for local mode's network claim,
  folded into the existing `Documentation of Output Destinations` requirement as MODIFIED
  rather than a new standalone requirement)
- Affected code: `bloommcp/src/bloom_mcp/identity.py`, `bloommcp/src/bloom_mcp/usage.py`,
  `bloommcp/docs/connecting-claude-code.md`, `bloommcp/docs/storage-backends.md`,
  `_WIKI/BLOOMMCP/README.md`
- Affected tests: `bloommcp/tests/test_identity_middleware.py`, `bloommcp/tests/test_usage.py`,
  a new `tests/unit/test_bloommcp_local_mode_docs.py` (grep-based regression test for the
  doc-wording fix, mirroring `test_bloommcp_data_mount_rename.py`'s existing pattern)

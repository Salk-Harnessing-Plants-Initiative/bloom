## Context

Bug report #641 traced two compounding bugs in `IdentityMiddleware` /
`bloom_mcp.usage`'s best-effort `bloommcp_usage` recording, confirmed still present on
`staging`: local mode attempts (and always fails) a Supabase RPC call on every request, and
the failure is logged with `logger.exception` (full traceback), making a fully successful
local-mode run look like it's crashing on every request.

## Goals / Non-Goals

- Goals: eliminate the attempted-and-failing network call in local mode; stop the
  per-request traceback noise; make the docs' local-mode network claim accurate.
- Non-Goals: changing what usage telemetry records or how for the `supabase` backend;
  adding a new "local/offline" flag distinct from the existing `is_local_backend()`;
  retroactively backfilling or migrating any `bloommcp_usage` rows.

## Decisions

- **Decision 1 — Gate on the existing `storage_backend.is_local_backend()`, not a new flag.**
  The issue's own suggested fix names this exact accessor, it's already the established
  convention for local-mode gating (`experiment_utils.py:51`, `local_reader.py:46`), it's a
  cheap side-effect-free string compare (safe to call per-request), and introducing a second,
  parallel "offline mode" concept would create two flags that could drift out of sync.
- **Decision 2 — Downgrade _both_ `logger.exception` call sites in `usage.py`, not only
  `_do_record`'s.** The issue names `_do_record`'s except clause specifically, but
  `record_usage_async`'s own except clause (submission failure, e.g. the executor rejecting
  work during interpreter shutdown) is the same "best-effort, non-blocking, shouldn't look
  like a crash" recording path, a few lines away in the same module. Fixing one and leaving
  the other would be an inconsistent half-fix of the same underlying complaint. Both become
  `logger.warning`, naming the exception's type (see Decision 7 for why type, not message).
- **Decision 3 — Reword the doc claim to "no experiment data leaves your machine" rather than
  removing the "no network" framing entirely.** The bug being fixed here removes the
  offending network _attempt_ in local mode, so after this change local mode again makes no
  network calls in the qualifying-request path — but the docs' current absolute phrasing
  ("no connection to the shared server at all") is still the wrong claim to make in the
  general case: it describes a stronger guarantee (zero network activity) than what local
  mode is actually designed to promise (no experiment data leaves the machine). The narrower
  claim is what's true regardless of exactly which code paths happen to make network calls
  today or in the future, matching the issue's own suggested rewording.
- **Decision 4 — No test asserts "zero network calls" globally.** Bug 1's regression test
  instead asserts the specific, previously-broken behavior directly: `record_usage_async` is
  not invoked when `is_local_backend()` is true, mirroring the existing
  `test_health_path_is_not_recorded` pattern. A "no network calls at all" test would require
  mocking every possible outbound call site and would not localize a future regression to this
  code path specifically.
- **Decision 5 — The MODIFIED `bloommcp_usage Records Caller Activity Per Mounted Surface`
  requirement is pasted from `add-bloommcp-oauth-usage-attribution`'s version, not
  `add-bloommcp-caller-identity`'s original.** An `openspec-review` pass caught an early draft
  of this proposal pasting the _original_ (pre-OAuth) requirement text as its MODIFIED baseline.
  That text is stale: `add-bloommcp-oauth-usage-attribution` (PR #613/#615, merged and shipped —
  `_oauth_subject_from_scope`, `SupabaseOAuthVerifier`, `ApiKeyVerifier` all exist in
  `identity.py`/`auth.py` today) already modified this same requirement to add the
  identity-source cascade (header → OAuth subject → anonymous) and four scenarios covering it.
  Neither that change nor `add-bloommcp-caller-identity` has been archived yet, so
  `openspec/specs/bloommcp-caller-identity/spec.md` doesn't exist to catch a stale paste — but
  the shipped behavior is real, and archiving this proposal against the stale baseline would
  have silently deleted it from the spec record. This proposal's delta now pastes the
  oauth-usage-attribution version in full and layers only the backend-scoping and log-level
  sentences on top. **Archiving implication:** this change SHOULD be archived no earlier than
  `add-bloommcp-oauth-usage-attribution` (and ideally together, or with an explicit check that
  the oauth change archived first) — archiving this one alone first, against a still-unarchived
  `bloommcp-caller-identity` spec, would be fine (this proposal's own paste already carries the
  oauth content forward), but archiving `add-bloommcp-caller-identity` or
  `add-bloommcp-oauth-usage-attribution` _after_ this one, without re-checking their deltas
  against what this change already established as current, risks the same class of stale-paste
  error recurring in the other direction.
- **Decision 6 — Documentation scope covers every location making the absolute-network claim,
  not just the two the issue cited, and the storage-backend doc delta is MODIFIED, not ADDED.**
  Review found two more locations making the same claim (`connecting-claude-code.md:35-37`'s
  signpost sentence, and `_WIKI/BLOOMMCP/README.md:82`'s "(offline)" label) that the issue's
  own text didn't mention; leaving them unreworded while fixing the other two in the same file
  (`connecting-claude-code.md`) would create an internal inconsistency the next reader or
  editor would trip over. Review also found the original ADDED requirement in
  `bloommcp-storage-backend` overlapped, un-cross-referenced, with the existing
  `Documentation of Output Destinations` requirement's own "Opt-in local backend and its
  caveats are documented" scenario — folded in as MODIFIED instead, adding one new scenario and
  one new sentence to that existing requirement rather than standing up a second "docs about
  the local backend" requirement next to it. A new grep-based regression test
  (`tests/unit/test_bloommcp_local_mode_docs.py`) pins the corrected wording across all three
  files, mirroring the existing `test_bloommcp_data_mount_rename.py::test_no_stale_sleap_out_csv_references`
  pattern (a fixed file list, grepped for banned strings) — chosen over a "zero network claims"
  semantic check because this proposal cannot verify prose meaning, only that the specific
  phrases being retired don't reappear and the replacement phrase is present.
  `storage-backends.md:244-247`'s unrelated "runs fully offline" phrase (describing why
  cross-backend mixing can't be detected, not a user-facing privacy guarantee) gets a light,
  optional reword for the same reason — once :130-131 in the same file is corrected, it would
  become the sole remaining unqualified "fully offline" claim there.
- **Decision 7 — The two new `logger.warning(...)` calls interpolate `type(exc).__name__`,
  not `exc`/`str(exc)`.** Follow-up from `review-pr`'s review of PR #659: the initial
  implementation interpolated the raw caught exception right next to
  `_redact_identity(identity)` — the same log line the redaction exists to protect. Not
  exploitable today (`record_bloommcp_usage` is a plain upsert with no constraint check that
  would echo `p_identity` back), but it was a defense-in-depth regression: a
  future schema constraint, or a `postgrest-py` `APIError` with a `DETAIL` field, could
  reintroduce exactly the clear-text-identity logging `_redact_identity` was built to
  prevent, right next to the line meant to prevent it. Logging only the exception's type name
  keeps the message informative enough to distinguish failure classes (e.g.
  `PostgrestAPIError` vs. `ConnectionError` vs. `RuntimeError`) without ever repeating
  arbitrary exception content verbatim.

## Risks / Trade-offs

- Downgrading `logger.exception` → `logger.warning` for the RPC-failure path loses the
  stack trace for a genuine, non-local-mode Supabase outage. Accepted: usage recording is
  documented as observability, not a delivery guarantee, and the exception's type name (see
  Decision 7) still narrows down the failure class; an operator debugging a real Supabase
  outage has other, louder signals (the actual tool calls failing) to go on.
- The downgrade also applies to every backend, not only `local` — a genuine `supabase`-backend
  outage now logs at `WARNING`, with no traceback, instead of `ERROR`. If any external
  monitoring rule is keyed on `ERROR`-level log lines from this module, it would silently stop
  firing for this failure class. No such alerting was found in this repo as of this proposal;
  worth a quick check before relying on `ERROR`-level alerting for `bloom_mcp.usage` in the
  future.

## Migration Plan

No data migration. Purely a request-handling and logging behavior change plus a doc
wording fix; no schema, API, or manifest format changes.

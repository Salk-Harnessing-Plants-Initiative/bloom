# Design — inline `csv_content` across every experiment-reading tool

## Context

`add-bloommcp-inline-csv-input` proved the ephemeral contract on `qc_clean` and built
`bloom_mcp.tools._inline_input` explicitly as a shared surface. This change is the rollout it
was built for. The interesting decisions are not "how do I parse a CSV" — that is solved and
tested — but what happens at the four places where a consumer tool's existing contract assumes
a registered experiment behind the frame: `require_clean`, plots, version/source pins, and
persistence-derived response fields.

## Decision 1 — Plots are rejected on the inline path, not redirected

**Decision:** `include_plots=True` together with `csv_content` raises
`BloomMCPError(code="invalid_input")`. It is never silently downgraded to `include_plots=False`.

**Why not commit them as run artifacts:** that *is* `ResultStore.create_run` + `commit` — the
one call #582 forbids.

**Why not write them to `PLOTS_DIR`:** this was the tempting option, because `PLOTS_DIR` is not
Storage, not the `ResultStore`, and not the manifest — so a narrow reading of "never persisted"
would permit it, and the five legacy plot tools already write there on every call. It is
rejected on evidence:

- `bloommcp/src/bloom_mcp/server.py` mounts `PLOTS_DIR` as `StaticFiles` at `/plots` **only**
  when `is_local_backend()`, documented in its own docstring as "Unauthenticated beyond
  `IdentityMiddleware`'s optional identity check."
- In **deployed** mode the exposure is worse, and via a service this change does not touch:
  `PLOTS_DIR` is a host bind-mount shared into *both* bloommcp and langchain-agent
  (`docker-compose.prod.yml`), and `langchain/server.py` mounts it as `StaticFiles` at `/plots`
  with **no auth dependency** — unlike every real route in that file, which uses
  `Depends(deps.get_current_user)`. Caddy proxies `/langchain/*` with the prefix preserved, so
  that directory is reachable **unauthenticated from the public ingress**.
- (Correction of record, since an earlier draft asserted otherwise: `BLOOM_PLOTS_URL` does *not*
  serve that directory in deployed mode. Caddy has no `/plots` route, so the configured value
  falls through to bloom-web's catch-all and 404s. Pre-existing and out of scope; noted so the
  wrong claim is not repeated as fact. Follow-up in tasks.md §13.)
- `_viz_shared.save_plot` names files deterministically from the experiment stem. For inline
  content there is no stem, so any scheme would either collide across callers or be derived from
  the content itself.

A caller passing an unregistered CSV is, by the feature's own framing, handling data that was
never meant to live in Bloom. Rendering it into a PNG on a shared, statically served path with a
derivable name is a cross-caller disclosure surface that the registered path does not have (a
registered experiment's plots are already visible to everyone who can see that experiment;
inline content has no such audience). The gap is real but bounded and disclosed.

**Why not base64 in the response:** a four-plot `pca_analysis` response would be several
megabytes of base64 through the MCP transport, into the agent's context. That is a worse failure
than not having plots.

**Consequence, accepted:** an inline `pca_analysis` returns numbers, not pictures. The error
message says so and names the registered-experiment path. This also settles, for now, the
"inline-vs-link threshold for large ephemeral outputs" that #582 flagged as needing a decision:
there is no link channel for ephemeral output, so the answer is "small results inline, no large
outputs at all."

**This is also why the five legacy plot tools get no inline path.** Their entire result *is* a
`PLOTS_DIR` URL. There is nothing left of them once that channel is unavailable.

**`qc_inspect` is the awkward case, and an earlier draft of this change got it wrong.** It has
**no `include_plots` parameter** — its figures are unconditional, written straight into the run's
staging directory, and its result advertises links to them. So there is no flag to reject. Its
params model does not forbid extra fields either, so a test asserting `include_plots=true` is
rejected would fail with "DID NOT RAISE", not pass. The honest resolution is that inline
`qc_inspect` is a **figure-free variant**: it returns the summary, per-trait diagnostics, and
recommendation and renders nothing. That is a genuine reduction in what the tool delivers, so it
is stated in the tool's own docstring and in `csv_content`'s field description — silently
returning two thirds of a tool's output is exactly the "silent downgrade" this decision forbids
everywhere else.

**The load-bearing test is a `savefig` spy, not a directory check.** Asserting "the shared plots
directory is unchanged" cannot fail for any of these tools: they write into `run.staging_dir`,
and only the five legacy plot tools import `_viz_shared.save_plot` at all. The directory
assertion is kept as a backstop; the spy is the guard.

An earlier draft also required that "no `matplotlib` import occurs on the inline path." That is
untestable here — `qc_inspect` and `_viz_shared` import matplotlib at module scope, and the
tools' own docstrings already record that `sleap_roots_analyze` pulls it in transitively. The
requirement has been dropped rather than shipped as an assertion nothing can satisfy.

## Decision 2 — `qc_clean` gains `return_cleaned_csv`, inline-only

**Decision:** `return_cleaned_csv: bool = False`. Valid only with `csv_content`; supplying it
with `experiment` is rejected. When true, `QCCleanResult.cleaned_csv` carries the cleaned table
as CSV text and `cleaned_csv_sha256` its digest.

**The problem it solves:** without it, this change's headline is "six tools now accept inline
CSVs, five of which will reject the CSV you have." Real trait tables have NaNs — that is why
`qc_clean` exists and why every consumer sets `require_clean=True`. The inline path has no way
to get from raw to clean, because `qc_clean`'s inline result is a summary by design.

**Alternatives considered:**

1. *Reject NaN-bearing inline content, tell the caller to clean it themselves.* Rejected: it
   moves QC out of the tested `clean_traits_for_analysis` delegate and into ad-hoc client code.
   The scientific cost (an agent inventing its own dropna policy) is worse than the surface
   cost of returning text.
2. *Server-side ephemeral session cache keyed by `input_sha256`.* Rejected outright: that is
   persistence with extra steps, plus cross-request state in a container that has none today —
   exactly the "no new storage seam" #582 says is unnecessary.
3. *Auto-clean inside each consumer (`auto_clean: bool`).* Rejected: it hides a QC step inside
   an analysis tool, so a `pca_analysis` result would silently reflect thresholds the caller
   never saw. `qc_clean`'s response reports every drop; an inlined auto-clean would not.

**Why this is not persistence:** the text goes into the response and nowhere else. No file, no
object, no manifest, no run. The chaining it enables is entirely the caller's — they hold the
bytes and choose to pass them on. `cleaned_csv_sha256` exists so the caller can prove the two
calls saw the same table; the server keeps no record of either.

**Size:** the serialized cleaned CSV is checked against `MAX_INLINE_CSV_BYTES` (5 MiB) before
being placed in the response. Over it → `BloomMCPError(invalid_input)` naming the size and
suggesting registration. Reusing the input cap avoids inventing a second number and is
conservative: cleaning only ever removes rows and columns, so a cleaned table that exceeds the
cap means the input was near it already.

**Default `false`:** an agent that does not ask for the table does not get a large string in its
context. Opt-in keeps the common case small.

## Decision 3 — `require_clean` becomes "caller-asserted", with the invariant re-checked locally

There is no way to certify inline content as cleaned: certification lives in the manifest, and
there is no manifest. Pretending otherwise (e.g. running the input through `clean_traits_for_analysis`
implicitly) is Decision 2's rejected alternative 3.

So the inline frame is **caller-asserted analysis-ready**, and each consumer re-checks locally
the two properties `require_clean` actually delivered:

**Finiteness — and it is not one policy across tools.** An earlier draft of this change assumed
a uniform "non-finite ⇒ `invalid_input`" rule for every consumer. That is wrong for two of them,
and shipping it would have been a silent behavior change on the registered path:

- `pca_analysis`, `umap_analysis`, `clustering`, `cross_experiment_correlations` are
  all-or-nothing today. They raise `assumption_violated` with "The cleaned experiment carries
  non-finite values" and the remedy "Re-run `qc_clean`". Both are wrong on the inline path — the
  code's own comment says "A mis-reporting reader is the only way this fires," and there is no
  reader — so inline gets `invalid_input`, a message naming the offending columns, and the remedy
  `qc_clean(csv_content=..., return_cleaned_csv=true)`, which Decision 2 makes followable.
  Registered-path text unchanged.
- `descriptive_stats` deliberately does the opposite: it computes finiteness per column and
  routes an offender to `failed_traits` so one bad trait does not block hundreds of healthy ones
  — its module docstring says so explicitly, calling out that it is "NOT `pca_analysis`'s
  all-or-nothing guard". It keeps that policy on both paths and gains no guard.
- `remove_outliers` has **no** finiteness check at all today; its docstring records that
  `require_clean` made the NaN path unreachable. So its inline check is a *new* guard, scoped to
  the inline path — not, as an earlier draft claimed, a re-coding of an existing
  `assumption_violated` error that does not exist.

**Trait-set membership.** `_validate_trait_subset(..., require_certified=True)` does three
things: reject empty lists, reject duplicates, and require membership in `frame.trait_cols`.
All three remain correct for an inline frame — `resolve_columns` populates `trait_cols`
identically. Only the *wording* is false ("not certified-clean traits of `'my.csv'`" names an
experiment that does not exist, and claims a certification that was never made). The fix is a
presentation-only `certified_label` parameter; the accepted column set is byte-identical, which
keeps every existing caller's behavior untouched and is asserted by a test.

## Decision 4 — `cross_experiment_correlations` resolves each side independently; any inline side makes the whole call ephemeral

Each side takes exactly one of `experiment_N` / `csv_content_N`, checked per side. Mixed calls
(`experiment_1` + `csv_content_2`) are **allowed** — "how does my local CSV correlate with
turface_19" is the single most plausible reason to want this tool inline at all, and forbidding
it would be an arbitrary restriction.

**But if either side is inline, nothing is persisted.** The alternative — persist the run
because one side is registered — produces a manifest entry whose composite storage key and
`based_on_version` name one resolvable version and one blob that exists nowhere. That is a
lineage record that lies. Ephemeral-if-any-side-is-ephemeral is the only honest rule.

Consequences, each with a spec scenario:

- The composite-key guards (`_reject_path_unsafe_names`, `_reject_dotted_stem`,
  `_reject_reserved_encoding_characters`) exist to protect a storage key that is never built on
  this path. They still run on any **registered** side (a mixed call's registered side is read
  through the reader exactly as today) and are skipped for inline sides.
- `_reject_self_correlation` **extends** to inline: two inline sides with equal `input_sha256`
  are the same table, and correlating a table with itself is as meaningless as it is for two
  identical experiment names. Equal-by-hash is the right test — byte-identical content is the
  only case we can detect, and it is the case that actually happens (a caller pasting the same
  string twice).
- A **mixed** pair is *not* checked for self-correlation: there is no digest for the registered
  side to compare against. `experiment_1="turface_19"` plus that experiment's exact bytes as
  `csv_content_2` is semantically self-correlation and will be allowed. That is stated in the
  spec and pinned by a test asserting it succeeds, so it reads as a known limit of hash-based
  detection rather than as a bug someone later "fixes" inconsistently.
- Mixed calls are valid in **both** directions, and both are tested. Argument order is documented
  as significant for this tool (it changes the composite storage key), so `csv_content_1` +
  `experiment_2` is not covered by symmetry with its mirror.
- The response carries `input_sha256_1` / `input_sha256_2`, each `None` for a registered side;
  `experiment_N` and `source_N` are `None` for an inline side rather than carrying a placeholder.

## Decision 5 — Reject registered-only companion parameters; never silently drop

`qc_clean` set this precedent for `source_id`/`run_id` and the reasoning transfers unchanged: a
parameter the caller supplied that the tool cannot honor must fail loudly, or the caller believes
a pin took effect that did not. Extended per tool to `version`, `version_1`/`version_2`, and
`user_label` (which names a version directory that will never be created).

**Where the check lives:** in the tool body, not a Pydantic `@model_validator` — for exactly the
reason `qc_clean`'s existing `NOTE` documents and verified empirically there: a validator's
`ValueError` is remapped by the contract layer into `"Input did not match the tool's schema
(<root>: value_error)"`, discarding the author's message. The shared
`reject_registered_only_params` helper keeps the message vocabulary identical across nine tools
while still raising `BloomMCPError` from the body.

## Decision 6 — One shared resolver, so nine tools cannot drift

```python
@dataclass(frozen=True)
class InlineInput:
    frame: ExperimentFrame
    input_sha256: Optional[str]   # None on the registered path
    is_inline: bool
    label: str                    # experiment name, or "csv_content"

def resolve_inline_or_experiment(*, experiment, csv_content, tool, registered_only=(), reader_call=None) -> InlineInput
```

It owns: the exactly-one-of check, `reject_registered_only_params`, the parse (inline) or the
supplied `reader_call` (registered), and `compute_input_sha256`. Everything genuinely per-tool —
`require_clean` error mapping, `version` kwargs, the `source_note`, the fit gate — stays in the
tool, passed in as `reader_call`.

**`qc_clean` is refactored onto it in the same change.** Leaving it on its hand-rolled version
would give us two implementations of "exactly one is required" immediately. Its existing inline
test suite (mutual exclusivity, equivalence oracle, never-persisted spy, `input_sha256`,
log-safety, `source_id`/`run_id` rejection) is the regression proof that the refactor preserves
behavior — those tests are not modified, which is the point.

**`label`** replaces each tool's `params.experiment` in error-message interpolation, reusing
`qc_clean`'s existing `_INLINE_EXPERIMENT_LABEL = "csv_content"` so messages read
`Column override names columns not in 'csv_content'` rather than `... not in 'None'`.

## Decision 7 — The two legacy tools keep their legacy shape (and differ from each other)

Both read through `_ports.load_frame` (the legacy 4-tuple adapter) rather than the
`ExperimentFrame` API. Their inline paths call `parse_inline_csv_frame` and adapt the frame to the
shape each already expects, rather than being rewritten. Rewriting them is a real cleanup, but
mixing it in would make the diff impossible to review against #582's "verify the caveat against
each tool's actual behavior individually."

They differ from each other more than an earlier draft of this document claimed:

- **`summarize_trait` *is* `@as_mcp_tool`-wrapped**, with ordinary Pydantic params and result
  models — identical in shape to the other seven. Its only peculiarity is the legacy read. It
  therefore gets the full treatment: `experiment=None`, `input_sha256`, structured
  `BloomMCPError`s, the log-safety pair. It has **no test module today**, so §10 includes standing
  one up, not just adding cases to an existing file.
- **`load_experiment_data` is the genuine outlier**: a plain function, no decorator, returning a
  formatted *string*, whose Google-style `Args:` docstring **is** the parameter schema the agent
  reads. It reports errors by returning strings (its existing `source_id`+`run_id` conflict does
  exactly this), so its inline errors do too — importing `BloomMCPError` here would be
  inconsistent with the tool's own convention. It has nowhere structured to put `input_sha256`,
  so the formatted output gains an explicit `Input SHA-256: <hex>` line plus a line stating the
  content was not registered.

Because it is unwrapped, an exception inside `load_experiment_data` propagates with **none** of
the contract layer's redaction — there is no `internal_error` envelope for it. That makes it the
one tool where the "never logged" guarantee cannot be tested on an error path the same way, and
where the inline path must guard its own failures explicitly. Stated here rather than left as a
silent gap in an otherwise uniform requirement.

This asymmetry also bounds Decision 6's anti-drift test: the mutual-exclusivity message can only
be byte-identical across tools **modulo the registered parameter's name** (`filename` here, and
per-side names in `cross_experiment_correlations`), and `load_experiment_data` returns rather
than raises. The resolver takes the parameter name so one vocabulary still produces all of them.

## Decision 8 — Widen `RunLinks`, rather than let each tool redeclare its run links

`RunLinks` declares `run_ref: str`, `version_dir: str`, `manifest_path: str` — all required — and
six of the affected result models inherit it, while `QCInspectResult` and `ClusteringResult`
redeclare the same three as required `str`. Every one of the seven also declares `experiment: str`
as a required *output*. An inline call has none of these, and the contract layer validates the
output model, so returning `None` fails until they widen. This was missing from the first draft of
this change entirely.

**Decision:** widen the three run-link fields on `RunLinks` to `Optional[str]`, and widen each
tool's identity output field to match. `outputs` defaults to an empty mapping.

**Why not per-tool overrides:** `bloommcp-tool-contract` has an explicit scenario asserting that
consumer models inherit the run-link fields *without redeclaring them*. Overriding them per tool
would break that requirement in seven places instead of amending it in one.

**Why not a placeholder string:** `run_ref="(none)"` names an object that does not exist and would
flow into any caller that treats a run reference as a lookup key. `None` is the honest value and
is what `qc_clean` already returns.

**The cost, and how it is paid.** Requiring these fields was, incidentally, the only thing
stopping a *persisting* tool from silently forgetting to populate them. Widening removes that net
with no error. So each tool gains a registered-path assertion that its run links come back
non-`None` and `outputs` non-empty — replacing the guarantee rather than losing it. This is a
**BREAKING** output-schema change, visible in `tools/list` for the registered path too, and it is
marked as such in the proposal.

## Decision 9 — Per-tool resource guards, because the byte cap does not bound super-linear work

`MAX_INLINE_CSV_BYTES` and `MAX_INLINE_CSV_COLUMNS` were sized against `qc_clean`, whose cost is
linear in the payload. Seven of the tools this change exposes are not. Measured on this machine
through the real `parse_inline_csv_frame`:

- A 5,242,866-byte payload (14 bytes under the cap) is **accepted in 0.03 s** and yields
  **313,171 rows**.
- `clustering(method="hierarchical")` is cleanly O(n²) in time and resident memory: n=6,000 →
  1.70 s / +809 MiB; n=8,000 → 3.89 s / +1.71 GiB; n=12,000 → 7.24 s / +2.38 GiB. RSS runs ~4-5×
  the condensed matrix. An attacker does not use the maximum — they tune n to just fit: ~40,000
  rows is a ~2 MiB payload, ~6 GiB condensed and ~24 GiB resident. `max_clusters` has a lower
  bound but **no upper bound**, and the silhouette search repeats the quadratic work per k.
- `cross_experiment_correlations` costs a flat ~326 µs per trait pair (measured at 400, 1,600 and
  3,600 pairs) in a nested Python loop, and `trait_columns_N` **defaults to all traits**, so
  omitting it maximizes the work. Two inline sides at the 2,000-column cap ⇒ 4,000,000 pairs ⇒
  **~22 minutes of single-threaded CPU** and 1.4-3.1 GiB of accumulated rows, for a 10 MiB
  request. The largest real fixture in this repo is 187 rows; the byte cap admits 1,600× that.

Nothing mitigates this: no rate limiting is wired, Caddy sets no request-body cap, tools are
registered without a timeout, and no service declares a memory limit — so an OOM is resolved by
the *host* killer, which may select the database rather than bloommcp. Sync tools run in a
40-slot thread pool, so concurrency multiplies all of it.

**Decision:** three guards — a row cap in the shared parser, an inline hierarchical-sample cap
plus a `max_clusters` upper bound in `clustering`, and an inline trait-pair-product cap in
`cross_experiment_correlations` — each firing *before* the expensive call and each leaving the
registered path untouched. The caps are chosen so the widest existing registered oracle still
passes unchanged, which is what keeps the equivalence tests meaningful.

**Why application-level rather than infrastructure-level:** container limits, rate limiting, and
timeouts are all genuinely missing and all affect the registered path too. They are the right fix
and they are not this change — a follow-up in tasks.md §13. What *is* this change's to own is that
it opens nine new doors into the same room.

## Decision 10 — A kill switch, because there is currently no way to turn any of this off

bloommcp has no feature flags. The deploy workflow's rollback fires only when the deploy job
itself fails; a successfully deployed but misbehaving build is reverted only by a new commit
through a full five-image rebuild. This change enables ten tools at once, on a path that accepts
caller-supplied payloads.

**Decision:** `BLOOMMCP_INLINE_CSV_ENABLED` (default enabled), read once inside
`resolve_inline_or_experiment`. One variable, one place, no per-tool wiring; disabled, every
`csv_content` call is rejected with a remedy naming the registered path and every registered path
is untouched.

## Decision 11 — Three sequential PRs, not one

#582 asks for one PR per tool. This change was scoped as one PR for all of them. Grounded in the
actual file sizes (the ten tool modules total ~4,780 lines; their test modules ~10,400), the
realistic diff is **~4,700-5,900 lines across ~27 files — roughly 8× the merged predecessor**.

Size alone would not settle it; this team ships PRs that size. What settles it is *shape*: the
predecessor and #462 are each **one** new thing a reviewer reads once. This is **ten independent
modifications to ten existing, heavily-tested tools**, where the reviewer must separately verify
ten equivalence oracles, ten never-persisted spy pairs, and ten log-safety pairs, and satisfy
themselves that ten registered paths are byte-identical to before. That is ten review contexts.

There is also a rollback argument. Once any consumer depends on the shared resolver, the resolver
commit cannot be reverted alone, and reverting the `qc_clean` refactor alone would leave exactly
the two-implementations drift Decision 6 exists to prevent. **The effective rollback unit is the
PR.** One PR makes that unit ~6,000 lines and ten tools.

**Decision:** three sequential PRs to `staging`, each independently green, revertable, and useful:

1. **Foundation + `qc_clean`** — the shared resolver, the `RunLinks` widening, `certified_label`,
   the `qc_clean` refactor, `return_cleaned_csv`. ~900 lines. Ships the resolver with exactly one
   consumer, whose 1,499-line existing test module is the regression proof. This is the PR where
   the resolver actually gets read.
2. **The five `require_clean` consumers** — `remove_outliers`, `pca_analysis`, `umap_analysis`,
   `clustering`, `descriptive_stats`. ~2,000 lines, and homogeneous: read `pca_analysis` in full,
   read the other four as diffs against that shape.
3. **The odd shapes** — `qc_inspect` (figure suppression), `qc_clean`'s `next_step` lift (which
   depends on 3, so it cannot precede it), `cross_experiment_correlations` (per-side), the two
   legacy readers, the cross-cutting roster tests, and the docs.

Not stacked: land 1, rebase 2 onto `staging`, and so on — a stacked PR against `staging` shows its
parent's commits in its own diff and defeats the split. Only PR 3 carries a closing keyword for
#582; a merged staging PR whose body names a closing keyword auto-closes the issue, which would
otherwise close #582 with two thirds of the rollout unmerged.

## Risks

**Ten tools, one shared seam — regression surface.** Mitigated by the resolver (most logic written
and tested once), by refactoring `qc_clean` onto it under its existing unmodified test suite, by a
per-tool equivalence oracle proving each registered path unchanged, and by Decision 11's split.

**`csv_content` escaping the process — the one property that must not be generalized.**
`contract/wrap.py` does `Provenance.stamp(tool=..., params=data.model_dump(), ...)`, so the raw
text sits in memory for the call's duration on every tool now. Two egress paths matter:

- *Manifest.* `Provenance.to_version_entry` copies `params` verbatim into a `VersionEntry`, whose
  `params: dict` accepts it. The inline path never calls `create_run`/`commit`, so it never gets
  there — but that invariant is now held by convention across ten tools rather than by a type. So
  the tests assert it **positively**: a marker placed in the inline content appears in no record
  the fake store holds. The `create_run` spy is an indirect proxy; this is the actual path, and
  it is what catches a `cross_experiment_correlations` implementation that forgets
  "either side inline ⇒ fully ephemeral".
- *Logs, stdout, stderr.* The existing `qc_clean` pair attaches a handler to the Python loggers,
  which is necessary (`run_input_validation` sets `propagate = False`, so `caplog` is
  structurally blind) but **not sufficient**. The MCP lowlevel dispatcher logs the entire JSON-RPC
  message — `csv_content` included — at `DEBUG`; the effective level is `INFO` today only because
  nothing calls FastMCP's `configure_logging`, which is one incident-response flag away. The
  upstream delegates also call bare `print()` in roughly a dozen places and `warnings.warn` in
  others, both of which bypass `logging` entirely. Destination makes this real rather than
  theoretical: **this repository is public**, and the deploy workflow echoes
  `docker compose logs --tail=200` for every service into the GitHub Actions job log when a deploy
  fails. So the per-tool pair captures stdout and stderr too, places a marker in a **column name**
  as well as a data cell (caller column names reach exception text and thence the contract layer's
  `logger.error(..., exc_info=exc)`), and one test runs at `DEBUG` to pin the dispatcher path.
  Separately, `connecting-claude-code.md` states plainly that raising bloommcp's log level to
  DEBUG writes inline content to container logs.

**Response size.** `return_cleaned_csv` / `return_trimmed_csv` can each return up to 5 MiB. This
will **not** 502 — Caddy imposes no response-size limit and the bloommcp route streams — but 5 MiB
of CSV is on the order of a million tokens and will blow the context of any client that receives
it. Hence opt-in, default off, capped, and documented in the field description.

**No live-smoke coverage would be a real gap.** The repo's convention is one
`tests/smoke/test_<tool>_smoke.py` per tool, run against the real container. This change adds nine
entry points and two large-response fields; the transport is exactly where the body-size and
response-size questions live, and unit tests calling tool functions directly cannot answer them.
tasks.md carries a smoke section rather than dropping the tier.

**Agent confusion about which tools accept inline content.** Mitigated by updating each tool's own
docstring and field descriptions (what `tools/list` shows) rather than relying on the markdown
page — and by updating the LangGraph agent's `CONTEXT_MCP` system prompt, which currently asserts
that experiments are *always* identified by an experiment identifier and would otherwise actively
suppress the new capability on Bloom's own web chat.

**Equivalence oracles prove less than the name suggests.** The fake reader resolves roles through
the same `resolve_columns` the inline parser uses, and for most tools the registered fixture is
`pd.read_csv(<file>)` against the inline `pd.read_csv(StringIO(<same text>))` — so the two frames
are identical by construction. The oracle genuinely proves that a tool's body does not branch on
`frame.source`, which is what it is for. It proves nothing about inline-parse fidelity against a
real Supabase-backed frame (different dtypes, a column rename at pivot time). Stated so the term
is not read as more than it is.

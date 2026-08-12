## Context

`get_download_links` (#599) resolves an already-committed run and freshly re-signs
`output_links` for it — deliberately a caller-opted-in, on-demand read path, distinct from
`get_run`/`list_runs`, which always leave `output_links` empty. `StoredRun.manifest_path` /
`RunLinks.manifest_path` has existed since before #581 as a plain, never-signed storage key
string, and neither #581 nor #599 touched it. #600 asks for the same signed-access treatment
`output_links` already has, applied to the manifest itself.

This branch was originally cut from `egao28/bloommcp-get-download-links-599` (PR #611), before
`get_download_links` existed on `staging`. **Update:** #611 has since merged into `staging`
(2026-08), and this PR's base auto-retargeted to `staging` as a result — the sequencing
constraint below no longer applies, left as a historical record of why the branch was cut where
it was.

**Update (PR #622 review, Decision 5 below):** the original design in this section and in
Decisions 1–4 shipped a signed link (`manifest_url`) to the run's `manifest.json`. Review
found that link fundamentally unscopable to one run (the file is keyed only by
`(experiment, tool_class)`, not `run_ref`) and it was reworked into inline, per-run
`params`/`based_on_version` fields instead. Decisions 1–4 are left below unmodified as the
historical record of what was built and evaluated first; Decision 5 documents the finding and
the rework. Goals/Non-Goals below describe the original, since-reworked design — see Decision 5
for the current shape.

## Goals / Non-Goals

- Goal (original; see Decision 5): a caller who already knows `(experiment, tool_class,
  run_ref)` can get a working, fresh download link for that run's `manifest.json`, the same way
  `get_download_links` already gets one for each output.
- Goal: reuse `create_signed_url` verbatim — no new signing mechanism, no manifest schema
  change, identical framing to #581/#599. (Moot for the manifest itself post-Decision-5, since
  nothing about it is signed anymore; still true of `output_links`, which this change never
  touched.)
- Non-Goal: populating `manifest_url` at `commit()` time (every consumer tool's own immediate
  response) — see Decision 1. (`params`/`based_on_version`, Decision 5's replacement, are
  likewise not populated at `commit()` time, for the same reasoning.)
- Non-Goal: a `sha256`/`size_bytes` pair for the manifest, or a dedicated new tool — see
  proposal.md's Non-Goals.

## Decisions

### Decision 1 — `manifest_url` is populated only by `get_download_links`, not `commit()`

`output_links` is populated at `commit()` time because `build_output_links` signs each output
key strictly *before* `write_manifest` — a signing failure there aborts a run that has not yet
been recorded as `latest`, so `commit()`'s existing all-or-nothing failure handling
(`CommitFailedError`, cleanup of already-uploaded keys) applies to it unmodified.

The manifest itself cannot be signed before it exists: `write_manifest(adir.path, manifest)`
is what creates/overwrites the `manifest.json` object `create_signed_url` would need to target.
Signing it would therefore have to happen *after* `write_manifest` succeeds — at which point
`commit()` has already advanced `latest`; the run is durably committed. A signing failure at
that point is not a commit failure (the run genuinely succeeded) and doesn't fit
`commit()`'s existing try/except/cleanup shape, which currently treats every failure inside the
lock as "roll back, nothing was recorded." Making it fit would mean either:

- Treating a post-write signing failure as if the whole commit failed (misleading — the run
  did commit; a caller retrying per that error would re-upload outputs and allocate a new
  version unnecessarily), or
- Introducing a new partial-success return shape (a committed `StoredRun` with
  `manifest_url: None` plus a surfaced warning) — a real design with its own scenarios, error
  types, and test matrix that this issue's acceptance criteria do not ask for.

Neither is a small, mechanical mirror of `output_links`'s existing pattern the way this issue
is scoped. Resolving `manifest_url` only inside `get_download_links` — a pure read path over
an *already-committed, already-existing* manifest object, with no ordering hazard at all —
avoids this entirely: signing an object that is already known to exist can only succeed or
fail cleanly, with no "did the run commit or not" ambiguity, matching Decision 4's existing
whole-call-fails-on-any-signing-error contract.

A caller who wants the manifest link immediately after a tool call already has that call's own
`run_ref` and can call `get_download_links(experiment, tool_class, run_ref)` in the same
session — one extra call, not a missing capability, and consistent with how `get_download_links`
already documents itself for outputs whose links weren't captured (or have since expired) from
the original response.

### Decision 2 — No key-scope guard for `manifest_path`

`get_download_links`'s existing guard (`CorruptRunLinksError`) exists because each `output_key`
is *read back from the manifest's own persisted content* (`output_keys` in a `VersionEntry`) —
a corrupted or resolution-bug-affected manifest could in principle claim an `output_key`
belonging to a different experiment/run, and the guard catches that before signing or sizing
it. `manifest_path` is different in kind: both adapters compute it deterministically from the
adapter's own identity — `f"{adir.path}manifest.json"` (Supabase) / the equivalent
`f"{prefix}manifest.json"` (Fake) — as a function of `(output_root, experiment, tool_class)`
alone, the same inputs that produced `adir`/`prefix` in the first place. It is never read out
of the manifest's own JSON body the way `output_keys` is, so there is no path by which a
corrupted manifest could make this key point somewhere else. No new guard is needed or added
for it.

### Decision 3 — `FakeResultStore` synthesizes a `fake://` URL, identical style to outputs

Mirroring `output_links`' existing `url_for=lambda key: f"fake://signed/{key}?expires_in=..."`,
`FakeResultStore.get_download_links` synthesizes `manifest_url` the same way, from
`stored.manifest_path`, with no new bookkeeping (unlike `size_bytes`/Decision 6 in #599's own
design.md — there is no size or hash in scope here, so no size-registry equivalent is needed
for the manifest).

### Decision 4 — `manifest_url` exposes the manifest's full content, including `ExperimentBlock.source_path`; no redaction

**Found in PR #612 review, not anticipated at proposal time.** `manifest.json`'s
`ExperimentBlock.source_path` (`manifest/schema.py`) is an absolute path to the source CSV on
the machine that committed the run — before this change, nothing on any `StoredRun`/MCP tool
response ever carried it, so reaching it required direct Supabase Storage/admin access (exactly
the access gap this change's own Why section describes for the manifest as a whole).
`manifest_url` is the first MCP-reachable path to it, since it's a direct signed link to the
existing object's real bytes, not a filtered view.

**Decision: ship as-is, no redaction, disclosed explicitly.** Three reasons:

- `source_path` is a path string, not file content or a credential — it reveals a directory
  layout (e.g. a `TRAITS_DIR` mount point), not experiment data or secrets. `openspec/project.md`
  already classifies bloommcp's data-directory paths as non-secret in every environment
  (see its "Important Constraints" section) — this is the same confidentiality class, not a new
  one.
- Redacting it would mean *rewriting* the manifest content this link serves, which both
  contradicts this proposal's own "no manifest content/schema change" Non-Goal and would require
  a structurally different mechanism (a filtering proxy in front of the object) than every other
  link this feature family (`output_links`, `manifest_url`) returns — a direct signed pointer at
  bytes already sitting in storage, unmodified.
- The same field has already been reachable by anyone with direct Supabase Storage/Studio access
  since the manifest was first introduced (pre-#581) — this change narrows *who* can reach it
  (an MCP caller, not just a storage admin), not *what* is reachable in principle.

Disclosed in `storage-backends.md` rather than left implicit, so a caller can make an informed
call about whether to hand a `manifest_url` to a less-trusted downstream consumer.

### Decision 5 — `manifest_url` (signed link to the shared `manifest.json`) reworked to inline, per-run `params`/`based_on_version`

**Found in PR #622 review (two independent lenses — scientific rigor and security —
converged on the same root cause), not anticipated at proposal time or in the #612
review that produced Decision 4.** `manifest.json` lives at a path keyed only by
`(experiment, tool_class)` — per `manifest/schema.py`'s own docstring, "Every
`(experiment, tool_class)` pair has one `manifest.json` ... listing all its runs."
Neither adapter's `manifest_path` derivation depends on `version_dir`/`run_ref`
(this was true and disclosed since Decision 2 — the point missed until #622 was
that it makes `manifest_url` **itself** unscopable, not just its guard-exemption
reasoning). So `get_download_links(exp, tc, "v1").manifest_url` and
`get_download_links(exp, tc, "v99").manifest_url` were signed links to the
identical object, whose `versions` array lists every run ever committed for that
pair — each with its own `params` (raw tool-call kwargs — column selections,
exclusion thresholds, filenames, potentially unpublished genotype/treatment
identifiers), `source_path`, `source_id`/`source_name`, and `based_on_version`.
Knowing any one run's `run_ref` unlocked every other run's data too — directly
contradicting this tool's own documented invariant ("must be called by name for
one already-known run — not a browsing or discovery feature," `storage-backends.md`).
Decision 4 (below) evaluated and accepted only the `ExperimentBlock.source_path`
angle as low-severity; that acceptance never covered the `versions` array, which
is the bulk of the document and was entirely unfiltered. `VersionEntry.params` in
particular was otherwise unreachable through any existing tool, including
`list_existing_analyses`.

**Decision: drop `manifest_url` (the signed link) entirely. Return the resolved
run's own `params` and `based_on_version` inline in the JSON response instead** —
the "single resolved `VersionEntry`, not the raw manifest object" option from the
#622 review, rather than re-opening the risk acceptance to cover the larger,
now-more-consequential scope (bloommcp began accepting external OAuth-authenticated
MCP clients in the same commit range, #613 — a caller population the original
non-secret-path framing in `openspec/project.md` predates).

Concretely:

- `StoredRun` drops the `manifest_url: Optional[str]` field and gains
  `params: dict` / `based_on_version: str`, both defaulting empty.
- These two fields are populated **only** by `ResultStore.get_run` (and thus by
  `get_download_links`, which calls it internally) — never by `from_version_entry`
  itself, and therefore never by `commit`/`list_runs`. This distinction is
  load-bearing, not cosmetic: `list_runs` backs `list_existing_analyses`, which
  dumps every returned `StoredRun` verbatim via `dataclasses.asdict` for every
  historical run under an experiment. Had `params`/`based_on_version` been added
  to `from_version_entry` itself (the more "obvious" fix, mirroring how
  `source_id`/`source_name` were added there for schema v4), `list_existing_analyses`
  — an **always-included, no-opt-in** discovery tool — would have gained the exact
  same class of cross-run params disclosure this decision exists to close, just
  through a different, arguably worse door (no opt-in call required at all).
  `get_run` is confirmed (by exhaustive grep) to have exactly one caller in this
  codebase — `get_download_links`, in both adapters — so scoping the attachment
  to `get_run` alone is safe and correctly matches the caller-opted-in,
  single-run-at-a-time shape this whole feature family already commits to.
- `SupabaseResultStore.get_run` already resolves exactly one `VersionEntry`
  per call (`adir.get_version(run_ref)`) and re-reads it fresh from storage on
  every call — no scoping change needed there beyond copying two more fields off
  the same `entry` it already has in scope.
- `FakeResultStore` has no equivalent per-call re-read: `list_runs`/`get_run` both
  read from the same in-memory `self._runs` list populated once at `commit()`
  time, so attaching `params`/`based_on_version` directly to the stored object
  (mirroring the Supabase fix) would leak them into `list_runs` too. It instead
  gains a private side table, `self._provenance: dict[(experiment, tool_class,
  run_ref), (params, based_on_version)]`, populated at `commit()` time from the
  same `entry` already computed there, and consulted only by `get_run`. This
  mirrors the existing `_output_sizes` side table exactly (same reason: a field
  `get_download_links` needs to attach per-call, without it living on the shared
  stored object every read path returns).
- No manifest signing, no live storage call, no signed-URL staleness window, and
  no key-scope guard of any kind are needed for these two fields — they are
  plain data already resolved in-memory by the same call that resolves the rest
  of the run, unlike every other field this feature family (`output_links`, the
  former `manifest_url`) that requires a live `create_signed_url`/`get_object_size`
  round-trip.

This also moots several of Decision 4's and the Risks section's concerns below,
rather than requiring a separate fix for each: no manifest is signed or fetched
at all anymore, so there is no `ExperimentBlock.source_path` exposure, no
"manifest can mutate between signing and fetching" race, no missing
manifest-signing-failure log line, and no legacy-manifest-thinness caveat for a
signed link that no longer exists (Decision 4 itself is left below, unmodified,
as a historical record of what was evaluated and why it no longer applies).

This diverges from issue #600's literal acceptance criterion ("a user/agent can
obtain a working download link for a run's `manifest.json`") — that wording
predates discovering the file can't be scoped to one run. `params`/
`based_on_version` fully serve the issue's own stated motivation ("verify a
run's provenance... exact params... everything recorded in the manifest [for
that run]") without the file-level exposure a literal download link would carry.

## Risks / Trade-offs

- **Superseded:** originally depended on #599 (PR #611) merging first. #611 has since merged
  into `staging` (2026-08) and this change's own PR retargeted there automatically — no longer a
  live risk, left as a record of the sequencing this change was built under.
- **Three unarchived sibling changes touching `bloommcp-result-store`.** Like #598 before it,
  this change's spec delta is written against `add-bloommcp-get-download-links`'s own
  still-unarchived "Re-Signing An Already-Committed Run's Download Links" requirement text
  (not yet in `openspec/specs/`) — whoever archives any of `add-bloommcp-signed-url-download`,
  `add-bloommcp-signed-url-key-scoping`, `add-bloommcp-get-download-links`, and this change
  will need to fold all four deltas together, archiving this one last.
- **Superseded by Decision 5 (PR #622):** `manifest_url` made `ExperimentBlock.source_path`
  MCP-reachable for the first time, accepted per Decision 4 as a non-secret path string. This
  is now moot — `manifest_url` (the signed link) no longer exists, so `source_path` is not
  MCP-reachable through this feature at all; Decision 4 is left above unmodified as a record of
  what was evaluated at the time.
- **Superseded by Decision 5 (PR #622):** a legacy (v2) manifest's `manifest_url` would have
  resolved to a schema-thin document (missing v3-only `seed`/`agent`/`environment`/per-artifact
  `output_sha256`/`output_keys`). Moot for the same reason — `params`/`based_on_version` (what
  this feature returns now) were part of the schema since v2, so there is no thinness gap for
  them to have.
- **`params`/`based_on_version` are exposed for exactly the resolved run, never any other.**
  This is the property Decision 5 exists to guarantee, not a residual risk — called out here so
  a future change to `get_run`/`from_version_entry` is reviewed against it: attaching these
  fields inside `from_version_entry` itself (rather than only in `get_run`) would silently
  reintroduce the same class of cross-run disclosure through `list_runs`/`list_existing_analyses`
  this decision was written to close.

## Migration Plan

None. No manifest, `Provenance`, or schema change of any kind; `manifest_url` is resolved live
on every `get_download_links` call, never persisted.

## Open Questions

None.

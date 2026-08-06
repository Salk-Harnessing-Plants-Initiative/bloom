## Context

`get_download_links` (#599) resolves an already-committed run and freshly re-signs
`output_links` for it — deliberately a caller-opted-in, on-demand read path, distinct from
`get_run`/`list_runs`, which always leave `output_links` empty. `StoredRun.manifest_path` /
`RunLinks.manifest_path` has existed since before #581 as a plain, never-signed storage key
string, and neither #581 nor #599 touched it. #600 asks for the same signed-access treatment
`output_links` already has, applied to the manifest itself.

This branch is cut from `egao28/bloommcp-get-download-links-599` (PR #611), not
`origin/staging` — `get_download_links` does not exist on `staging` yet.

## Goals / Non-Goals

- Goal: a caller who already knows `(experiment, tool_class, run_ref)` can get a working,
  fresh download link for that run's `manifest.json`, the same way `get_download_links`
  already gets one for each output.
- Goal: reuse `create_signed_url` verbatim — no new signing mechanism, no manifest schema
  change, identical framing to #581/#599.
- Non-Goal: populating `manifest_url` at `commit()` time (every consumer tool's own immediate
  response) — see Decision 1.
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

## Risks / Trade-offs

- **Depends on #599 (PR #611), not yet merged.** This change cannot land independently;
  sequencing is disclosed in proposal.md. No rebase-conflict surface beyond what #599 already
  discloses against #598/#609, since this change touches the same three files #599 already
  touches, in the same methods, without altering their existing control flow.
- **Three unarchived sibling changes touching `bloommcp-result-store`.** Like #598 before it,
  this change's spec delta is written against `add-bloommcp-get-download-links`'s own
  still-unarchived "Re-Signing An Already-Committed Run's Download Links" requirement text
  (not yet in `openspec/specs/`) — whoever archives any of `add-bloommcp-signed-url-download`,
  `add-bloommcp-signed-url-key-scoping`, `add-bloommcp-get-download-links`, and this change
  will need to fold all four deltas together, archiving this one last.

## Migration Plan

None. No manifest, `Provenance`, or schema change of any kind; `manifest_url` is resolved live
on every `get_download_links` call, never persisted.

## Open Questions

None.

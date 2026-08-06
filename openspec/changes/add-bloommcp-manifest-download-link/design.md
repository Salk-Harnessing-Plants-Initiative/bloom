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
- **`manifest_url` makes `ExperimentBlock.source_path` MCP-reachable for the first time.**
  Accepted per Decision 4 — a non-secret path string, not new-in-kind exposure (already
  reachable via direct storage access), disclosed explicitly in `storage-backends.md` rather
  than silently shipped.
- **A legacy (v2) manifest's `manifest_url` resolves to a schema-thin document.** This
  proposal's own Why section frames manifest access as letting a caller verify "everything
  recorded in the manifest" — for a v2-era run, most v3-only provenance fields (`seed`,
  `agent`, `environment`, per-artifact `output_sha256`/`output_keys`) were never recorded in
  the first place, so the fetched manifest itself will look sparse. `manifest_url` still
  resolves (per Decision elsewhere in this doc — never gated on `output_keys`), and this is
  disclosed in `storage-backends.md`'s legacy-run bullet so a caller isn't surprised by a
  thin document after following a working link.

## Migration Plan

None. No manifest, `Provenance`, or schema change of any kind; `manifest_url` is resolved live
on every `get_download_links` call, never persisted.

## Open Questions

None.

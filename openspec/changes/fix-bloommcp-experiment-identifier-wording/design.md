## Context

Issue #552 asks to reword every LLM-facing "CSV filename" reference to a backend-agnostic
term. This sits inside `bloommcp/docs/data-access-roadmap.md`'s Tier 3 ("LLM-facing
surface + cleanup"), which the roadmap table explicitly lists as **depending on Tier 2**
(`#551`, DB-direct raw-tier rewrite — OPEN, PR #557 not yet merged). Tier 3 in the roadmap
bundles three things: (a) the wording fix, (b) retiring now-dead CSV-from-bucket/local-disk
code, (c) rewriting `storage-backends.md`'s `supabase`-mode description. Issue #552 itself
narrows scope to explicitly separate (a) from (b)/(c): "No functional behavior change —
text/docs/param-naming only — so this can land independently of Tier 2/#546's completion,
**but should be sequenced right after** so the wording matches what's actually true by
then." That sentence draws the exact line this change needs to respect.

## Goals / Non-Goals

- Goals:
  - Every LLM-facing schema description, docstring, and validation message describes the
    `experiment` input as an identifier, not a filename, in wording that is true both
    under the current (pre-Tier-2) filename-shaped identifier and the future
    (post-Tier-2) `str(experiment_id)`-shaped one — so it does not need a second editing
    pass when Tier 2 ships.
  - Cover the full, currently-accurate location list (verified by fresh grep against
    `staging`, not the issue's point-in-time list) — including the two
    `cross_experiment_correlations.py` sites and `list_available_experiments.py`, none of
    which the issue names.
- Non-Goals:
  - `storage-backends.md`'s rewrite (issue's own bullet 3) — see Decisions below.
  - Any of Tier 3's code-retirement scope (dead bucket/local-disk read paths,
    `BLOOM_TRAITS_DIR` boot-validation removal) — #476/#477's job, gated on Tier 2 same as
    this issue's doc bullet.
  - Renaming any `filename`-named param other than `list_existing_analyses.py`'s
    `experiment_filename` (see Decisions).

## Decisions

- **Decision: defer `storage-backends.md`'s rewrite, do not touch it in this change.**
  The issue's proposed fix says to describe `supabase` mode as "DB-direct trait reads, not
  bucket CSVs." Checked current `staging`: Tier 2 (#551) is still OPEN with an unmerged PR
  (#557); the deployed `supabase` backend still reads raw inputs from local
  `BLOOM_TRAITS_DIR` and cleaned outputs from Supabase Storage — exactly what
  `storage-backends.md` says today. Rewriting the doc now to describe DB-direct reads
  would make it describe behavior that doesn't exist yet, which is the same class of
  "text doesn't match reality" bug this issue exists to fix, just introduced in the
  opposite direction on a different file.
  - Alternative considered — write it now, phrased carefully to stay accurate (e.g. "will
    become DB-direct once Tier 2 ships"). Rejected: `storage-backends.md`'s whole purpose
    is describing *current* backend behavior precisely (it already has a "Scope" section
    doing exactly this kind of precision work); a forward-looking caveat inside a
    should-be-current-state doc is exactly the kind of drift the doc's own history (see
    its Reconciliation log) has already had to correct once.
  - Alternative considered — implement it now and re-touch it again when Tier 2 lands.
    Rejected: doubles the editing cost for no benefit over doing it once, correctly,
    when Tier 2 actually merges — which is what issue #552's own sequencing sentence
    already recommends.
- **Decision: rename `list_existing_analyses.py`'s `experiment_filename` param to
  `experiment`, cascading into its JSON response key.** All 9 other tools already use
  `experiment` (or `experiment_1`/`experiment_2`) as the param name — confirmed by
  grepping every `Field(...)`-declared experiment param across `sections/`.
  `list_existing_analyses` is a plain function (not a Pydantic-validated `@as_mcp_tool`),
  so its param name is itself part of what FastMCP advertises to the calling LLM — the
  issue calls this out as "the more load-bearing signal." Renaming it makes this tool
  consistent with every sibling tool's schema, at the cost of also updating the response
  dict's `"experiment_filename"` key (so a downstream caller of the JSON payload sees the
  same rename) and the one test asserting on that key
  (`test_qc_tools_discovery.py:102`).
  - Alternative considered — reword only the docstring, leave the param name. Rejected:
    the issue explicitly flags the param name as more load-bearing than the description
    for an LLM choosing what to pass; leaving it would under-deliver on the issue's own
    stated priority.
- **Decision: home this change in a new `bloommcp-experiment-identifier-wording`
  capability rather than a `MODIFIED` delta against an existing spec.** The change spans
  8 tool-schema files plus 2 validation guards plus 9 docstring-only files — no single
  existing capability (`bloommcp-experiment-read`, `bloommcp-tool-contract`, or any
  per-tool spec like `bloommcp-qc-clean-tool`/`bloommcp-pca-analysis-tool`) owns the
  LLM-facing wording across all of them. `MODIFIED` would require pasting each affected
  tool's full existing requirement text into 8+ separate per-capability spec files (per
  AGENTS.md's rule that `MODIFIED` must carry the complete updated requirement, not a
  partial delta) even though none of those requirements' actual behavior changes — pure
  overhead for a text-only fix. A single new capability stating the wording contract once
  is proportionate to a change that is genuinely orthogonal to any one tool's behavior
  (matching AGENTS.md's "prefer ADDED when the change is orthogonal" guidance).
  - Alternative considered — `MODIFIED` against `bloommcp-experiment-read` only (the
    capability that actually defines what `ExperimentReader.load_experiment(name, ...)`
    accepts), since the wording is ultimately about that contract. Rejected: none of
    `bloommcp-experiment-read`'s existing requirements describe tool-schema description
    text or docstrings at all — they describe the port/adapter contract, not what a
    Pydantic `Field` says to an LLM. Forcing this in would conflate "what the port
    accepts" with "what 9+ unrelated tool files say about it."
  - Precedent check: `retire-bloommcp-traits-dir-bypass` used `MODIFIED` against
    `bloommcp-experiment-read` for a similarly narrow doc-wording fix, but that change
    touched exactly one file (`supabase_reader.py`) already owned by that capability —
    not a comparable multi-file spread.
- **Decision: do not rename the other `filename`-named params** (5 plot tools,
  `load_experiment_data`, the 3 unrelated `phenotyping_segmentation` demo tools). The
  issue's rename ask is scoped to `list_existing_analyses.py` specifically; a bare
  `filename` name (vs. `experiment_filename`) is a smaller, more ambiguous signal, and
  broadening the rename to 8 more call sites is a separate scope decision the issue
  doesn't make. Their `description`/docstring text still gets reworded in this change —
  only the param name is left alone.
- **Decision: standardize on the phrase "experiment identifier" (not "experiment ID" or
  "experiment name").** Matches the issue's own suggested wording and the
  `ExperimentReader.load_experiment(name, ...)` port's actual parameter name (`name`),
  avoiding "ID" (which reads as strictly numeric — wrong for the pre-Tier-2 deployed
  state) and "name" alone (already means something more specific/ambiguous next to
  `list_existing_analyses`' existing `"error": f"Experiment '{x}' not found"` messages).

## Risks / Trade-offs

- **Two locations the issue's own list omits** (`cross_experiment_correlations.py`'s two
  `Field` descriptions, `list_available_experiments.py`'s docstring + response text) are
  in scope here despite not being named in #552. Mitigation: called out explicitly in
  `proposal.md` rather than silently expanded, mirroring the issue's own point about the
  roadmap doc's stale count — the acceptance check in `tasks.md` greps the live tree
  rather than just checking off the issue's original list.
- **`cross_experiment_correlations.py`'s `experiment_1` description also encodes a real,
  currently-enforced constraint** (read `_reject_dotted_stem`, lines 280-300, directly):
  `AnalysisDir` re-applies `Path(...).stem` to whatever composite `experiment=` string
  this tool builds, so a stem (the part before the *last* dot) that itself contains a
  dot gets silently truncated at that last dot — a real storage-key-collision risk, not
  filename pedantry. The current description phrases this as "its filename stem (the
  part before the final extension) must not contain '.'" — filename-specific vocabulary
  (stem/extension) that stops making sense once an identifier is a bare
  `str(experiment_id)` with no extension at all (post Tier 2).

  **Correction (PR #571 review, caught by an independent reviewer testing the two
  conditions in Python):** the first cut of this task claimed the rule reduces exactly to
  "must not contain more than one `.` character" — that is **not** equivalent.
  `pathlib.Path(name).suffix` is only non-empty when the last `.` is strictly interior
  (not the first or last character of the name); when it isn't (a leading dot like
  `.hidden`, or a trailing dot like `a.`), `.stem` falls back to the *whole name*, which
  still contains that one dot, so `_reject_dotted_stem` rejects it — but a
  1-dot-total-only rule would have wrongly said it's allowed. Reviewer's table,
  reproduced and confirmed directly in Python:

  | identifier | total dots | rejected by `_reject_dotted_stem`? | "≤1 dot" rule says? |
  | --- | --- | --- | --- |
  | `.hidden` | 1 | yes | no (wrong) |
  | `a.` | 1 | yes | no (wrong) |
  | `a.b` | 1 | no | no (right) |
  | `a.b.c` | 2 | yes | yes (right) |

  The exact equivalent (verified against every branch of `pathlib`'s `suffix` logic, and
  against `_reject_path_unsafe_names` running before `_reject_dotted_stem` at line
  460-463, so a bare `.`/`..` never reaches this guard to begin with): reject iff the
  identifier contains more than one `.`, **or** contains exactly one `.` that is the
  first or last character. Task 2.4 and the tool's own description now state this as
  "must not contain a '.' except as a single interior extension separator... a leading
  dot, a trailing dot, or more than one '.' anywhere is rejected" — still filename-vocab-free
  and durable through Tier 2 (a numeric post-Tier-2 identifier has zero dots and trivially
  satisfies it), but now actually exact rather than only exact on the common case.
- **Leaving `storage-backends.md` untouched could look like an incomplete fix** relative
  to the issue's literal bullet list. Mitigation: stated explicitly and reasoned through
  in `proposal.md`'s Non-Goals and here, not silently dropped — recommend #552 stay open,
  re-scoped to track only the deferred `storage-backends.md` rewrite (see Open Questions
  below, now resolved rather than left open).
- **The "no behavior change" claim for the two path-traversal guards is a load-bearing
  safety claim, not just a convenience one** — verified it's backed by existing,
  untouched tests rather than resting solely on "the `if` condition isn't edited": both
  `bloommcp/tests/tools/test_viz_tools.py`'s traversal-payload test (around lines
  395-414, covering `../secret.csv`, `..\secret.csv`, an absolute path to a real secret
  file, and `/etc/passwd`) and each tool's own parametrized guard tests (e.g.
  `test_qc_inspect_tool.py`'s traversal cases, `test_qc_shared_validator.py`) assert on
  `BloomMCPError.code == "invalid_input"` / rejection behavior, never on message text —
  so they keep passing unmodified through this change and independently prove the
  accept/reject decision is unchanged, rather than that claim resting on citation alone.
- **Race condition on the deferred `storage-backends.md` follow-up**: PR #557 (Tier 2's
  implementation) is open, approved, and CI-green as of this proposal — i.e. mergeable
  on short notice. If it merges before this change's PR does, `storage-backends.md`
  becomes stale the moment Tier 2 ships, and nothing in this change's own tasks
  re-triggers fixing it on its own. Mitigation: `tasks.md` §6 adds a pre-merge re-check
  of #551/PR#557's live state specifically so this doesn't go unnoticed.

## Migration Plan

None — no schema or data migration; no runtime behavior change (validation guards keep
their exact accept/reject decisions, backed by the existing tests named above; the only
externally-observable change is `list_existing_analyses`'s parameter/response-key name, a
text-level rename with the same JSON shape otherwise).

## Open Questions — resolved

- **Should issue #552 be closed by this change, or kept open/re-scoped?** Resolved:
  **kept open**, re-scoped to track only the deferred `storage-backends.md` rewrite.
  `proposal.md`'s Impact section now explicitly instructs the PR description to say
  `Addresses #552`, not a closing keyword, so merging doesn't auto-close an issue with an
  unmet acceptance bullet. This mirrors `retire-bloommcp-traits-dir-bypass`'s handling of
  #476. Filing a fresh tracking issue for the deferred bullet (vs. just re-scoping #552's
  own description) is left as a human call at PR-review time — this proposal doesn't
  decide that unilaterally.

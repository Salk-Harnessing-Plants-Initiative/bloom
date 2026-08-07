## Why

Every consumer tool (`qc_clean`, `pca_analysis`, `clustering`, `remove_outliers`, …) accepts
only `experiment: str`, resolved server-side through the injected `ExperimentReader` against
Storage/DB. Claude Code, unlike Claude Desktop, has local filesystem access — but there is no
way to hand a tool the bytes of a local CSV directly. Today the only path is: register the
file as an experiment (persistent bucket storage, provenance, content-hash identity) before any
tool can touch it. That is real design and engineering weight for what is often just "QC this
CSV I have right now" — a one-off check on data that was never meant to live in Bloom's
database.

Originally scoped as part of #388's "upload inputs via chat" piece; redrafted here as a leaner
**ephemeral** mode instead of persistent upload+registration, since a typical Claude Code user
does not want to run a full local bloommcp server and does not need repeat/versioned history on
non-DB data. This intentionally trades away persistence, lineage, and re-analysis for a much
smaller, faster-to-ship path.

**Rollout is one tool at a time (per #582), and this change is the first: `qc_clean` only.**
`qc_clean` is the natural entry point — most other tools consume its output — and it is the
right tool to prove the ephemeral contract on before any other tool adopts it: get the shared
parsing helper, the mutual-exclusivity rule, the size guard, and the "never persisted" guarantee
fully tested here, then each subsequent tool (`pca_analysis`, `clustering`, `remove_outliers`,
…) gets its own follow-up change and its own thorough test pass — not one shared assertion
assumed to generalize to tools this change does not touch.

## What Changes

- **ADD** a new capability, `bloommcp-inline-csv-input`: a shared helper (`bloom_mcp.tools
  ._inline_input`) that parses a caller-supplied CSV string directly into an `ExperimentFrame`
  in memory — **never written to Storage, never registered, never persisted** (no
  `ResultStore.create_run`, no manifest entry, no `list_existing_analyses` visibility). It
  resolves column roles (genotype / sample-id / replicate) and trait columns through the same
  `resolve_columns` unit every reader adapter already uses, so an inline frame is
  indistinguishable in shape from an adapter-sourced one. It enforces an explicit size cap
  (`MAX_INLINE_CSV_BYTES`, proposed 5 MiB — see design) so a caller cannot push an unbounded
  payload into the shared container's memory, and computes `input_sha256` over the exact
  caller-supplied bytes so the response can tell the caller what they analyzed even though
  nothing is stored to check it against later. This is the shared surface every subsequent
  per-tool change (out of scope here) will import — built once, not once per tool.
- **MODIFY** `qc_clean` (the sole consumer touched by this change) to accept `csv_content: str`
  as a sibling to `experiment: str` on `QCCleanParams` — **mutually exclusive, exactly one
  required**, enforced as the first check in `qc_clean`'s own body (raising `BloomMCPError`
  directly, the same pattern the tool's existing genotype/sample_id-collision check already
  uses) so a bad call fails before the reader or parser runs. (A Pydantic `model_validator` was
  the first draft; moved to the body during review, since a validator's raised `ValueError` is
  remapped by the contract layer into a generic message that discards the validator's own text —
  see design.md.) When `csv_content`
  is given, `qc_clean` skips the `ExperimentReader` port entirely, cleans the in-memory frame
  exactly as it does an adapter-sourced one (identical role resolution, thresholds, validation,
  no-NaN guarantee), and returns the **same small summary** it always returns (in/out counts,
  resolved roles, validation warnings, NaN summary) — but with `experiment=None`, `source
  ="inline"`, `input_sha256` populated, and no `run_ref`/`manifest_path`/`outputs` (nothing was
  persisted, so there is nothing to link to). The cleaned table itself is **not** returned
  inline — `qc_clean` never inlines the table for the registered-experiment path either, so the
  ephemeral path stays consistent rather than growing a second response shape. The existing
  `qc_inspect` nudge (`next_step`) is suppressed on this path — it names a tool that has no
  `csv_content` support and would otherwise interpolate the caller's absent experiment identity
  into an actionable-looking but wrong message (found during review).
- **NO new tool, no new endpoint** — this is an additive parameter on an existing tool plus one
  new internal helper module. `qc_clean`'s registered name, schema for the `experiment`-only
  call shape, and every existing behavior are unchanged (`experiment` still works exactly as
  before; only its `Field(...)` moves from required to optional-with-validator).
- **DOCSTRINGS:** `qc_clean.py`'s module docstring and the wrapped tool function's own docstring
  both currently describe an unconditional "reads the raw frame via the ExperimentReader port /
  persists a versioned run" flow — this is what Claude Code itself sees via `tools/list`'s
  description, more load-bearing than any markdown page. Both are updated for the two-path
  reality as part of this change, not left stale.
- **DOCS:** add a short section to `bloommcp/docs/connecting-claude-code.md` stating plainly that
  an inline `csv_content` call never touches Bloom's shared Storage/DB (the fact this doc's own
  access-scope thesis makes most relevant to state here) plus a minimal call example, pointing to
  `QCCleanParams.csv_content`'s own field description — the single canonical place the "no
  history" caveat (no versioned run to look up next session, no `based_on_version` chaining into
  a later `pca_analysis`, no `list_existing_analyses` entry) is spelled out in full — rather than
  restating that bullet list a second time in prose that could drift out of sync with it.
- Tests cover: the shared helper's parsing/size/hash unit contract; `qc_clean`'s mutual-
  exclusivity validation; an **equivalence oracle** — the same `turface_19` raw fixture fed as
  `csv_content` text produces the identical cleaned-table shape and role resolution as the
  existing file-based oracle (`turface_19_qc_golden.json`), so ephemeral and persisted paths are
  proven to clean identically, not just independently; the never-persisted guarantee (no
  `ResultStore.create_run` call — a spy/mock assertion, not just "no run appears in the fake
  store"); and the response's `input_sha256` matches an independently computed hash of the
  input string.

## Explicitly Out of Scope (the trade this change makes on purpose)

- **Every other consumer tool.** `pca_analysis`, `clustering`, `remove_outliers`,
  `descriptive_stats`, `cross_experiment_correlations`, `umap_analysis` — each is its own
  follow-up change per #582's rollout plan, importing the shared helper this change ships.
- **No versioned run history for the inline call** — nothing to look up next session.
- **No lineage chaining** — an inline `qc_clean` result cannot be fed as `based_on_version` into
  a later `pca_analysis`; a caller who wants to chain tools needs the persistent path (a
  registered experiment), not this one.
- **Not a replacement for real upload/registration.** If genuinely-repeated analysis on the same
  non-DB data turns out to be wanted, that is its own future issue, not scope creep here.
- **The inline-vs-link threshold decision for large ephemeral outputs** — `qc_clean`'s result is
  already a small summary regardless of input path, so this change does not need it; a future
  tool whose normal result is large (e.g. `pca_analysis`'s plots) will need to decide this in
  its own follow-up change.

## Impact

- **Affected specs:**
  - `bloommcp-inline-csv-input` (**new** capability) — the shared ephemeral-parsing contract.
  - `bloommcp-qc-clean-tool` (**modified**) — adds the `csv_content` alternative-input
    requirement to the existing tool contract.
  - Builds on (does not modify) `bloommcp-tool-contract` and `bloommcp-experiment-read`.
- **Affected code:**
  - new `bloommcp/src/bloom_mcp/tools/_inline_input.py` (`parse_inline_csv_frame`,
    `compute_input_sha256`, `MAX_INLINE_CSV_BYTES`);
  - `bloommcp/src/bloom_mcp/sections/sleap_roots/analysis/qc_clean.py` (`csv_content` param +
    mutual-exclusivity validator + inline branch, no persistence call on that branch, `next_step`
    suppressed on that branch, module + function docstrings updated for the two-path reality);
  - `bloommcp/docs/connecting-claude-code.md` (short section: inline calls never touch shared
    Storage/DB, a minimal example, pointer to `QCCleanParams.csv_content`'s own field description
    for the full "no history" caveat — not a restatement of it);
  - new `bloommcp/tests/tools/test_inline_input.py` (shared-helper unit tests);
  - extend `bloommcp/tests/tools/test_qc_clean_tool.py` (mutual exclusivity, equivalence oracle,
    never-persisted guarantee, `input_sha256`).
- **Dependencies:** none new — reuses `resolve_columns` (`bloom_mcp.data_access.columns`),
  `pandas.read_csv`, and `hashlib` (stdlib).
- **Sequencing:** Base this change on `origin/staging` directly (no dependency on any other
  in-flight change). PR targets `staging`.
- **Implements the `qc_clean` slice of #582.** Parent: #388 ("upload inputs via chat",
  superseded in scope by #582's ephemeral redraft). Sibling follow-ups (not filed yet, tracked
  in tasks.md §5): the same `csv_content` addition for `pca_analysis`, `clustering`,
  `remove_outliers`, and the rest of the consumer-tool roster.

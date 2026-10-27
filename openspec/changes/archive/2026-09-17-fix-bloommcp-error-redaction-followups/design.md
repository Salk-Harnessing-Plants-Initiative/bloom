## Context

This change closes out the three lower-priority, non-blocking follow-ups #660's review
explicitly scoped out of that change (see #664). It touches the same corner of the codebase
#660 did — the 8 write-and-link analysis tools' error handling, and the two
`@as_mcp_tool`-adjacent-but-not-wrapped core functions (`list_existing_analyses.py`,
`get_download_links.py`) #660's own `design.md` Decision 3 declined to touch. Where #660
built out `errors=` declarations inside the contract layer, this change stays entirely
outside it: `list_existing_analyses.py` and `get_download_links.py` are plain functions with
their own inline `try/except Exception` → `safe_error_text(exc)` handling, never going
through `@as_mcp_tool`.

## Goals / Non-Goals

**Goals:**

- Make the redaction of `list_existing_analyses.py`'s per-`tool_class` error entries
  explicit and tested, not an inherited property of `_guarded_manifest_read`'s current
  callers.
- Close the leak-test coverage gap on the 5 tools #660 didn't touch, so all 8 write-and-link
  tools have an equivalent test proving an undeclared delegate exception both maps to
  `internal_error` and leaks nothing.
- Make the aggregated error string's tool label match the name an agent actually invoked.

**Non-Goals:** see `proposal.md`'s Non-Goals section (`CommitFailedError` wording,
`CommitFailedError`'s own tool-name-mismatch, the `TOOL_CLASSES` enumeration gap).

## Decisions

### Decision 1: The tool_class → public-name lookup lives in `list_existing_analyses.py`, keyed by literal strings

Each of the 8 tools defines its own `tool_class` value as a private module-level constant
(e.g. `descriptive_stats.py`'s `_TOOL_CLASS = "stats"`). Importing each analysis-tool module
from `list_existing_analyses.py` just to read that constant would invert the dependency
direction between `sections/core` (foundational, session-bootstrap tools) and
`sections/sleap_roots/analysis` (the granular analysis tools) — `core` tools are meant to be
usable before any analysis tool is even known to exist, not to import from them. Given the
lookup only needs to cover the 5 tool classes `TOOL_CLASSES` already iterates that map to a
current tool (`"qc"`, `"stats"`, `"clustering"`, `"outliers"`, `"correlation"`) plus a
fallback for the 3 legacy/retired entries (`"dimred"`, `"outlier"`, `"viz"`) that map to
nothing, a small literal `dict[str, str]` defined directly in `list_existing_analyses.py`
(reusing the existing `QC_TOOL_CLASS`/`OUTLIERS_TOOL_CLASS` constants it already imports from
`experiment_utils` for two of the five keys) is simpler and has no new import-direction cost.
`.get(tool_class, tool_class)` supplies the fallback.

This lookup is deliberately **not** extended to `"pca"`/`"umap"`/`"qc_inspect"` — those tool
classes aren't in `TOOL_CLASSES` at all today (see #669), so adding their mapping here would
be untestable dead code until #669 lands separately.

### Decision 2: Items 2's new/tightened tests assert existing behavior, not new behavior

For `clustering`, `pca_analysis`, and `umap_analysis`, the undeclared-exception path already
exists and already maps to `internal_error` via the contract layer's `errors=` tuple — it's
simply untested today because the tools' existing "no leak" tests monkeypatch the delegate to
raise an exception type the tool's own `except` clause _does_ catch (translating it to
`assumption_violated` instead). Each new test raises a type outside that tuple:

| Tool            | Existing `except` clause (→ `assumption_violated`) | New test raises   |
| --------------- | -------------------------------------------------- | ----------------- |
| `clustering`    | `(ValueError, RuntimeError)`                       | `KeyError`        |
| `pca_analysis`  | `ValueError`                                       | `RuntimeError`    |
| `umap_analysis` | `(ValueError, KeyError, RuntimeError, TypeError)`  | plain `Exception` |

`cross_experiment_correlations` has no `except` clause around its delegate calls at all — its
existing `test_no_error_leaks_backend_internals` already exercises the undeclared path via a
monkeypatched `RuntimeError`, it just never asserted `code == "internal_error"`; tightening
that one assertion closes the gap with no new test needed.

`descriptive_stats` has neither an `except` clause nor a leak test — its new test is
structurally identical to `qc_inspect`/`qc_clean`/`remove_outliers`'s existing pattern from
#660 (monkeypatch the delegate, e.g. `calculate_trait_statistics`, to raise a
secret-bearing generic exception; assert `code == "internal_error"` and the secret's absence).

None of this requires a production-code change to any of the 5 tools themselves — only to
`list_existing_analyses.py` (items 1 and 3). This is why these tests carry no spec delta of
their own: they close coverage on behavior the `bloommcp-tool-contract` spec's existing
"Internal failure does not leak detail to the agent" requirement already guarantees
generically for every decorated tool.

### Decision 3: A new capability spec, not a delta to `bloommcp-outliers-staleness-audit`

`list_existing_analyses.py`'s per-`tool_class` aggregation loop has no owning capability spec
today — `bloommcp-outliers-staleness-audit` documents the `trim_is_stale` field and its own
`trim_staleness: ...`-prefixed `errors` entry (already redacted, unaffected by this change),
but not the tool_class loop's `errors.append(f"{tool_class}: {exc}")` entries this change
touches; no other spec covers it either. Rather than force-fitting an unrelated behavior
into the staleness-audit capability by name, this change adds a new
`bloommcp-list-existing-analyses-tool` capability (matching the per-tool `bloommcp-<tool>-tool`
naming precedent already archived for sibling tools, e.g. `bloommcp-pca-analysis-tool`,
`bloommcp-qc-clean-tool`) scoped to exactly the two behaviors this change adds: redaction and
public-tool-naming of that loop's error entries — deliberately narrower than its name might
suggest, since `list_existing_analyses`'s registration, caching, and `analyses`/`message`
response fields remain undocumented by any spec, and `trim_is_stale` staleness reporting is
documented separately in `bloommcp-outliers-staleness-audit`. At archive time, this
capability's Purpose line should say so explicitly — reusing `proposal.md`'s Impact-section
wording ("covers only the per-tool_class error-aggregation loop's redaction and tool-naming
contract, which no existing spec owns today") rather than a generic "covers `list_existing_analyses`"
sentence that would overclaim scope.

## Risks / Trade-offs

- **Pre-existing risk, unchanged by this PR:** #660's `design.md` Risks section already
  discloses that surfacing `CommitFailedError`'s `"(transient — retry)"` wording as a
  `tool_error` makes that advice agent-actionable for the first time, with the one
  known edge case (a lost commit ack followed by a literal retry) producing two valid run
  versions neither side flags as related. #664's own comment thread adds a related
  reproducibility wrinkle for the 3 stochastic tools among the 8 (`clustering`,
  `remove_outliers`, `umap_analysis`): each draws a fresh `resolve_seed()` when the caller
  doesn't pin one, so a literal retry without pinning `seed` persists a genuinely different
  computation as that second version, not a replay of the failed attempt — same underlying
  "two versions, neither flagged as related" shape, different cause (non-determinism, not
  timing). Not a correctness or leak issue, not something this change's narrow scope fixes;
  noted here only so it isn't rediscovered from scratch later.
- **Test churn risk:** `test_trim_is_stale_and_an_unrelated_tool_class_error_both_survive_together`
  hard-codes the pre-fix `"qc: "` prefix; this change updates that one assertion to
  `"qc_clean: "` as part of implementing item 3. No other existing test asserts on this
  loop's error-string format (confirmed by search), so this is the only expected-diff test
  update.

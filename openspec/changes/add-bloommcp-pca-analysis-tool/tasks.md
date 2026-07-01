> **TDD note:** the RED steps below (§2–§3) are a _local_ working-tree rhythm — write each test,
> confirm it fails, then make it pass. Do **not** push a RED-only commit: CI gates the PR head, and
> a committed failing/uncollectable test is red. Commit RED+GREEN together. Suggested commits
> (each green after it): `test(#308)` golden per-PC drift key + README provenance → `feat(#308)`
> tool + tests (RED+GREEN in one commit) → optional follow-up `test(#308)` live-smoke leg (after
> #356 merges). The OpenSpec proposal files may ride in the first commit or a `docs(#308)` commit.
>
> **Staging guardrail:** the working tree carries unrelated noise — a stray empty file `n`, a
> mode-bit-only change to `scripts/render_plate_videos.py`, and an **untracked, non-gitignored** > `bloommcp/data/` runtime-output dir. **Stage by explicit path only** (never `git add -A` / `git
add .` / `git add bloommcp/`); run `git status` before each commit and confirm those three are
> absent from the index.
>
> **Composition note:** the unit oracle and `require_clean` tests seed a cleaned version directly
> via `FakeReader.add_cleaned_version` (the reader and store fakes are disjoint — a store commit is
> not visible to the reader), so they do **not** depend on #356. Only the live-smoke leg (§7) needs
> Tier 3 (#356) merged; rebase onto `staging` before adding it. This PR may merge into `staging`
> independently of #356 on the strength of the fakes oracle.

## 1. Pre-work — fixtures + honest golden extension (prerequisite, no new dependency)

- [x] 1.1 Confirm the analyze pin already satisfies the delegate: `bloommcp/pyproject.toml` pins
      `sleap-roots-analyze>=0.1.0a3` (on `staging`), and `wsl -d Ubuntu -- bash -lc 'cd
  ~/repos/bloom/bloommcp && uv run --frozen python -c "from sleap_roots_analyze import
  perform_pca_analysis, PCAResult; PCAResult.from_pca_dict"'` imports clean. **No pin bump or
      `uv.lock` change is part of this tier.**
- [x] 1.2 Reuse the vendored `bloommcp/tests/fixtures/turface_19_pca_golden.json` and the post-QC
      `turface_19_final_data.csv` (both on `staging`). The existing `pca_explained_variance`
      (cumulative `0.9599…`) + `n_pca_components: 3` are the **independent** #120 / PR #146 oracle
      (upstream `viz_pca_metadata.json`). **Add** a per-PC key `"pca_explained_variance_ratio":
  [0.8612933510667774, 0.05820169635401897, 0.040414549139183936]` **honestly labeled as a
      characterization snapshot** — add a sibling `"_pca_evr_source"` string stating it is
      re-derived from `perform_pca_analysis==0.1.0a3` (a per-PC drift gate, NOT independently
      recorded; the upstream viz metadata records only the cumulative value). Document the addition
      in `tests/fixtures/README.md` using the SAME honest framing the file already uses for
      `heritability_mean` / `umap_trustworthiness` (characterization snapshot, not an independent
      oracle). Edit the JSON **preserving LF** (`.gitattributes` pins `*.json text eol=lf`).

## 2. RED — golden PCA through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_pca_analysis_tool.py`. Wire the ports through a fixture
      that calls `bloom_mcp.tools._ports.configure(reader=FakeReader(...), store=FakeResultStore())`
      and **restores the Supabase adapters in a `finally:`** (mirror qc_clean's `injected_ports`
      fixture — `_ports` is global module state; leaking a fake into later test modules is a bug).
      Seed the cleaned version into the **reader** with the real signature
      `reader.add_cleaned_version("turface_19.csv", "v1", final_data, make_latest=True)` (there is
      **no** `trait_cols=` kwarg; the frame's `trait_cols` are auto-derived via `detect_columns`).
- [x] 2.2 Write the **golden-through-the-tool** test FIRST: invoke `pca_analysis(experiment=…,
  trait_columns=golden["trait_cols"], standardize=True, explained_variance_threshold=0.95)`;
      assert `n_components == 3` and `cumulative_variance_ratio[2] ==
  pytest.approx(golden["pca_explained_variance"], abs=1e-6)` (the independent oracle), **and**
      `explained_variance_ratio == pytest.approx(golden["pca_explained_variance_ratio"], abs=1e-6)`
      (read the key added in §1.2 — do not hard-code the literals). Confirm it fails (no tool yet).
      **Do not loosen the tolerance to pass** — debug to green. (`abs=1e-6` matches
      `test_oracle.py`'s `_VAR_TOL`; safe because the solver is deterministic — no randomized path.)
- [x] 2.3 Assert **no silent sample loss**: `result.n_samples` equals the cleaned fixture's row
      count (the certified set), so a future regression that let PCA `dropna()` rows fails here.

## 3. RED — the other four contract patterns (one discrete confirm-RED test each)

- [x] 3.1 **tools/list presence:** a FastMCP `Client` over the server lists `pca_analysis` with a
      schema-bearing input model; assert `run_dimensionality_reduction_workflow` is still listed.
- [x] 3.2 **Delegation pinning:** spy `pca_analysis_tool.perform_pca_analysis`; assert it is called
      **exactly once** with the validated `trait_columns` subset, and monkeypatch
      `bloom_mcp.pca.perform_pca_analysis` to raise — assert it is **never** called. (Covers spec
      "PCA is delegated, not re-implemented".)
- [x] 3.3 **`n_components` override vs threshold:** with `n_components=2` on the golden fixture
      assert `result.n_components == 2` (overrides the threshold's 3) and the spy captured
      `n_components=2`; with `n_components=None` + `explained_variance_threshold=0.95` assert
      `n_components == 3`. Add a boundary case: `n_components=99` (> feature count) → assert the
      delegate clamps and `result.n_components == n_features` (no raise).
- [x] 3.4 **Schema round-trip:** valid request ↔ input schema and result ↔ output schema round-trip
      without loss.
- [x] 3.5 **Invalid input — out-of-range (split):** one assertion each — `explained_variance_
  threshold` out of `[0,1]` → `BloomMCPError` validation code; `n_components < 1` → validation
      code. No run persisted.
- [x] 3.6 **Invalid input — trait columns (split):** (a) a column absent from the frame →
      `invalid_input` whose message contains the column name; (b) a non-numeric metadata column →
      `invalid_input`; (c) a numeric column present in the frame but **outside the certified-clean
      set** (`frame.trait_cols`), including one carrying NaN → `invalid_input`, and assert the
      delegate spy was **not** called (no silent `dropna()`). (Covers the require_clean NaN-safety
      hole.)
- [x] 3.7 **Degenerate fit → structured, not internal:** using the **real** delegate (no mock) on a
      degenerate valid selection (e.g. a single certified trait that is constant), assert the tool
      returns `code == "assumption_violated"` (not `internal_error`), the message does not leak a
      traceback/backend path, and `store.list_runs(...)` is empty.
- [x] 3.8 **Require-clean consumption (property):** with only a **raw** version registered (no
      cleaned) → `BloomMCPError` whose remedy names `qc_clean`, and no run committed; with a cleaned
      version seeded into the reader → the tool resolves it and asserts `frame.source` starts with
      `v` and ends `_cleaned` (the fake emits `v<N>_cleaned`; `legacy_cleaned` is a live-only path).
- [x] 3.9 **Provenance (deterministic):** after a successful call the stamped `Provenance` records
      `seed is None`, the tool name, and the PCA params; and the committed `StoredRun.seed is None`.
- [x] 3.10 **Determinism:** invoke `pca_analysis` twice with identical inputs; assert the two
      results' `explained_variance_ratio` and `cumulative_variance_ratio` are equal within `abs=1e-6`.

## 4. GREEN — implement the tool

- [x] 4.1 Add `bloommcp/src/bloom_mcp/tools/pca_analysis_tool.py`: `PCAAnalysisParams`
      (`experiment`, `trait_columns?`, `standardize=True`, `explained_variance_threshold=0.95` with
      `ge=0, le=1`, `n_components?` with `ge=1`, `user_label?` — **no `seed` field**) and
      `PCAAnalysisResult` (experiment, source, `n_samples`, `n_features`, `n_components`,
      `feature_names`, `explained_variance_ratio`, `cumulative_variance_ratio`, `eigenvalues`,
      `run_ref`, `version_dir`, `manifest_path`, `outputs`).
- [x] 4.2 Tool fn `pca_analysis(params, *, provenance: Provenance) -> PCAAnalysisResult` wrapped by
      `@as_mcp_tool(input_model=…, output_model=…)` (**no `random_state` param** → contract records
      `seed=None`): read via `reader().load_experiment(params.experiment, require_clean=True)`;
      validate that each `trait_columns` entry is in `frame.trait_cols` **and** numeric →
      `invalid_input` naming offenders (default to all of `frame.trait_cols` when omitted); assert
      `frame.df[selected].isna().sum().sum() == 0` (else `assumption_violated`); call
      `perform_pca_analysis(frame.df[selected], standardize=…, explained_variance_threshold=…,
  n_components=…)` (let the delegate use its default `random_state`); wrap via
      `PCAResult.from_pca_dict`.
- [x] 4.3 **Catch-and-remap for specific codes/remedies** (the `errors=` declared path only yields a
      generic `tool_error`): catch `CleanedVersionRequiredError` → `raise BloomMCPError(code=…,
  remedy="run qc_clean first …")`; catch the delegate's `ValueError` → `raise
  BloomMCPError(code="assumption_violated", remedy="select a broader trait subset …")`. Do
      **not** rely on the `errors=` tuple to carry these codes.
- [x] 4.4 `register(mcp)` via `bloom_mcp.contract.register`; register in
      `bloommcp/src/bloom_mcp/server.py` (import + `pca_analysis_tool.register(mcp)`) and add a
      `pca_analysis` entry under the module docstring's "Direct tools (granular)" list.

## 5. GREEN — persist the run with lineage + return links

- [x] 5.1 Persist via `store().create_run(experiment=…, tool_class="pca", provenance=provenance,
  user_label=…)`; set the stamped provenance's `based_on_version = frame.source` (the
      `v<N>_cleaned` label) so the manifest records the cleaned-source lineage. Write `loadings.csv`,
      `scores.csv`, and `pca_result.json` (`PCAResult.to_json`) into `run.staging_dir`, then
      `store().commit(run, {...})`.
- [x] 5.2 Return the variance summary inline + `run_ref` / `version_dir` / `manifest_path` /
      `outputs` (object keys). Add tests: `set(stored.output_keys) ==
  {"loadings.csv","scores.csv","pca_result.json"}`; `stored` records `based_on_version ==
  frame.source`; the result carries **no** score/loadings matrix inline
      (`assert not any(isinstance(v,(list,dict)) and len(str(v))>5000 for v in
  result.model_dump().values())`); a second `pca_analysis` run increments the version (`v1`→`v2`,
      `latest`→`v2`).

## 6. Refactor (optional, deferred — behavior-preserving)

- [ ] 6.1 The `trait_columns` subset validator overlaps with `qc_clean_tool.py` (which is not on
      `staging` until #356). **Do not** extract it in this PR (it would couple this change to #356's
      file and there is nothing to share with on `staging`). File a follow-up after both tools land.

## 7. Live persistence smoke — composition leg (after #356 merges)

- [ ] 7.1 Rebase onto `staging` once Tier 3 (#356) is merged. Extend
      `bloommcp/scripts/live_persistence_smoke.py` with a `pca_analysis` leg: after the existing
      `qc_clean` leg commits a cleaned version, run `pca_analysis` through the real `SupabaseReader`
      / `SupabaseResultStore`, assert `require_clean=True` resolves the `v<N>_cleaned` source, the
      PCA manifest is `manifest_schema_version == 3`, records `based_on_version` = the consumed
      cleaned version, and each `output_sha256` matches the stored bytes.
- [ ] 7.2 Factor the leg's pure decision logic into importable helpers and unit-test them in
      `tests/scripts/` with no live stack. Add the pca note to `bloommcp/docs/local-validation.md`
      (created by #356; edit it only after the rebase).

## 8. Agent-surface validation, docs + verification

- [ ] 8.1 **Validate on Claude Desktop (capable model) and sanity-check Qwen3.5-9B** (a #308
      acceptance criterion — kept as its own checkbox, not folded into pre-merge): confirm
      `pca_analysis` is discoverable, the schema is legible to a small model, a happy-path call on a
      cleaned experiment returns the variance summary + links, and the "run qc_clean first" remedy
      renders sensibly when no cleaned version exists.
- [x] 8.2 Update the `server.py` module docstring (done in 4.4). Do **not** edit
      `bloommcp/docs/roadmap.md` (its tier-number reshape is owned by #339) or `bloommcp/README.md`
      (it lists tools by category — "dimensionality reduction" already covers PCA).
- [x] 8.3 `wsl -d Ubuntu -- bash -lc 'cd ~/repos/bloom && npx --yes -p @fission-ai/openspec openspec
  validate add-bloommcp-pca-analysis-tool --strict'` is clean.
- [x] 8.4 Tests + lint green: `cd bloommcp && uv run --frozen --extra test pytest
  tests/tools/test_pca_analysis_tool.py tests/test_oracle.py` and the full bloommcp suite;
      `uv run black --check .`, `uv run ruff check .`, and `pre-commit run --all-files` (or `/lint`).
- [ ] 8.5 `/pre-merge` → `/pr-description` → PR to `staging` linking #308 (stage by explicit path;
      confirm `n`, `render_plate_videos.py`, `bloommcp/data/` are not staged). Merge order relative
      to #356: this PR may merge first (fakes oracle); the §7 live-smoke leg follows post-#356.

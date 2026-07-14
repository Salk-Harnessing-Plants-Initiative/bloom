> **TDD note:** the RED steps below (§2–§3) are a _local_ working-tree rhythm — write each test,
> confirm it fails, then make it pass. Do **not** push a RED-only commit: CI gates the PR head, and a
> committed failing/uncollectable test is red. Commit RED+GREEN together.
>
> **Commit plan (sibling-matched — `remove_outliers` #378 / `pca_analysis` #308; CI green after each):**
> 1. `chore(#309): gitignore bloommcp runtime data + stray artifact` (§0)
> 2. `docs(#309): openspec proposal — polymorphic clustering tool (Tier 5)` (the change files — **lead
>    with this**, matching all three siblings)
> 3. `chore(#309): add turface_19 clustering golden fixture` (generator + fixture JSON + fixtures/README
>    note, together — §1)
> 4. `feat(#309): add polymorphic clustering MCP tool (Tier 5)` (tool + `server.py` + the `_qc_shared`
>    `require_certified` promotion + **all** RED tests + GREEN impl, in one commit — §2–§5)
> 5. `test(#309): clustering live-smoke leg + local-validation docs` (smoke script + `tests/scripts/` +
>    the stale-enumeration doc updates — §6)
>
> **Staging guardrail:** stage by explicit path only (never `git add -A` / `.` / `bloommcp/`); §0
> gitignores the untracked noise (`=0.1.0a4`, `bloommcp/data/`) so the hazard is structurally removed,
> but still run `git status` before each commit.
>
> **Determinism note:** the north-star test is _same seed → identical `cluster_labels`_ per method
> (element-wise, NOT tolerance) — do not weaken it. The metric snapshot (§1) is a separate,
> honestly-labeled drift gate. Proving the seed genuinely _reaches the fit_ (§3.2), not merely that it is
> recorded (§3.3), is the load-bearing test for this — the first stochastic — tier.

## 0. Prelim — defuse the untracked-noise hazard (standalone `chore:` commit)

- [x] 0.1 Add `bloommcp/data/` and `/=0.1.0a4` to `.gitignore` (both are untracked and **not** currently
      gitignored — a real `git add -A` hazard, and `bloommcp/data/` is not in `bloommcp/.dockerignore`
      either). Commit alone as `chore(#309): gitignore bloommcp runtime data + stray artifact`. No test
      impact.

## 1. Pre-work — characterization fixture via a committed generator + honest framing

- [x] 1.1 Confirm the pin already satisfies the delegates: `bloommcp/pyproject.toml` pins
      `sleap-roots-analyze>=0.1.0a4` and `uv.lock` resolves `0.1.0a4` (verified). `wsl -d Ubuntu -- bash
      -lc 'cd ~/repos/bloom/bloommcp && uv run --frozen python -c "from sleap_roots_analyze import
      perform_kmeans_clustering, perform_gmm_clustering, KMeansResult, GMMResult;
      KMeansResult.from_kmeans_dict; GMMResult.from_gmm_dict"'` imports clean. **No pin bump or `uv.lock`
      change is part of this tier.**
- [x] 1.2 Add a **committed generator** (`bloommcp/scripts/gen_clustering_golden.py`) that emits
      `bloommcp/tests/fixtures/turface_19_clustering_golden.json` from `perform_*==0.1.0a4` on the 8
      recorded `turface_19_pca_golden.json` `trait_cols` over `turface_19_final_data.csv` (153 samples) —
      **no metric literal is hand-authored** (silhouette/DB/CH/sizes AND `bic`/`aic` are all machine-
      captured, so the fixture is regenerable). The JSON records: `trait_cols` (the 8), a `_source`
      string ("characterization snapshot re-derived by `gen_clustering_golden.py` from
      `perform_*==0.1.0a4`; a drift gate, NOT an independently recorded oracle"), and per method the
      pinned params + metrics —
      - kmeans (`n_clusters=3, standardize=true, seed=42`): silhouette `0.4170820373`, davies_bouldin
        `0.7982630887`, calinski_harabasz `200.2918882831`, cluster_sizes `[40, 85, 28]`;
      - gmm (`n_components=3, covariance_type="full", standardize=true, seed=42`): silhouette
        `0.3156523270`, davies_bouldin `1.0737231775`, calinski_harabasz `134.9785471342`, cluster_sizes
        `[32, 38, 83]`, `converged=true`, + the captured `bic` / `aic`.
      Run the generator to produce the file; document it in `tests/fixtures/README.md` with the SAME
      honest framing the PCA golden uses (`_pca_evr_source` / heritability / UMAP). Let **prettier**
      normalize the JSON on commit (safe — tests read values, never bytes) and commit the normalized
      form; `.gitattributes` pins `*.json text eol=lf`.

## 2. RED — determinism + characterization through the tool (north star, write first)

- [x] 2.1 Add `bloommcp/tests/tools/test_clustering_tool.py`. Wire ports via a fixture that calls
      `bloom_mcp.tools._ports.configure(reader=FakeReader(...), store=FakeResultStore())` and **restores
      the Supabase adapters in a `finally:`** (mirror `pca_analysis`' `injected_ports` fixture). Seed the
      cleaned version into the **reader** with `reader.add_cleaned_version("turface_19.csv", "v1",
      final_data, make_latest=True)` (no `trait_cols=` kwarg; auto-derived — yields **12** certified
      traits, a superset of the golden 8).
- [x] 2.2 **Determinism oracle (write FIRST):** for each `method in {"kmeans","gmm"}`, invoke
      `clustering(experiment=…, method=…, trait_columns=golden["trait_cols"], seed=42, …)` **twice** with
      identical inputs and assert the two runs' `cluster_labels` are **element-wise identical** (not a
      tolerance compare) and `cluster_sizes` equal. Confirm it fails (no tool yet). **Do not weaken to a
      tolerance.**
- [x] 2.3 **Characterization snapshot through the tool:** kmeans (`n_clusters=3, seed=42`) →
      `n_clusters==3`, `cluster_sizes==golden.kmeans.cluster_sizes`, and each score `==
      pytest.approx(golden.kmeans.<score>, abs=1e-6)` (read from the §1.2 fixture — do **not** hard-code
      literals). gmm (`n_components=3, covariance_type="full", seed=42`) → the gmm snapshot + `converged
      is True`. Carry a one-line comment justifying `abs=1e-6` (mirror `test_oracle.py`'s `_VAR_TOL`
      rationale: deterministic fit at a fixed seed/sklearn build; a drift-gate tolerance, not a
      cross-platform reproducibility claim). Do not loosen to pass — debug to green.
- [x] 2.4 Assert **no silent sample loss**: `result.n_samples == 153` (the certified row count), so a
      regression that let the delegate `dropna()` rows fails here.
- [x] 2.5 **Default selection uses the full certified set (IM-1):** omit `trait_columns` → assert
      `set(result.feature_names) == set(frame.trait_cols)` and `len(...) == 12` (not the golden 8) **and**
      `n_samples == 153` — so a regression that silently narrows the default (or that the golden 8 ==
      default) is caught.

## 3. RED — the contract patterns + polymorphic dispatch (one discrete confirm-RED test each)

- [x] 3.1 **tools/list presence:** a FastMCP `Client` over the server lists `clustering` with a
      schema-bearing input model; assert `run_clustering_workflow` is still listed.
- [x] 3.2 **Polymorphic delegation pinning + seed-reaches-fit (BL-2):** spy
      `clustering_tool.perform_kmeans_clustering` and `clustering_tool.perform_gmm_clustering`; assert
      `method="kmeans"` calls **only** the kmeans delegate (once) and wraps via
      `KMeansResult.from_kmeans_dict`, and `method="gmm"` calls **only** the gmm delegate (once) and wraps
      via `GMMResult.from_gmm_dict` — a kmeans dict is **never** wrapped by `from_gmm_dict`. **Capture the
      spy's `random_state` kwarg and assert it equals the resolved seed (42)** — so a tool that recorded
      the seed but passed a different/hard-coded `random_state` to the fit fails (proves the seed reaches
      the fit, not just the manifest). Monkeypatch `bloom_mcp.clustering`'s entry points to raise — assert
      they are **never** called.
- [x] 3.3 **Provenance records the resolved seed (the new-for-this-tier path):** after a successful call
      the stamped `Provenance` records `seed == 42` (the resolved value, **not** `None`), the tool name,
      method, and the cluster params; and the committed `StoredRun.seed == 42`. (Contrast: `pca_analysis`
      records `None`.)
- [x] 3.4 **Schema round-trip:** valid request ↔ input schema and result ↔ output schema round-trip
      without loss, for both methods.
- [x] 3.5 **Method-exclusive scalars (IM-2):** a `method="kmeans"` result has `inertia is not None` and
      `bic is None and aic is None and converged is None and covariance_type is None`; a `method="gmm"`
      result has `bic is not None and converged is not None` and `inertia is None`. (The polymorphic-shape
      guarantee the tier exists to prove — the tool never populates both families or neither.)
- [x] 3.6 **Cluster-count override + auto-select:** kmeans `n_clusters=4` → `result.n_clusters==4` and
      the spy captured `n_clusters=4`; kmeans `n_clusters=None` → the delegate auto-selects (assert `2 <=
      n_clusters <= max_clusters`). gmm `n_components=3` → `n_clusters==3`; gmm `n_components=None` on this
      data → BIC collapses to a single component (assert `n_clusters==1`, `silhouette_score==0.0`, **no
      raise**, surfaced in the summary — documents the degenerate auto-select honestly).
- [x] 3.7 **Invalid input — out-of-range / wrong-method control (split):** one assertion each — kmeans
      `n_clusters < 2` → validation code; gmm `n_components < 1` → validation code; **every** gmm-only
      control (`n_components`, `max_components`, `covariance_type`) supplied with `method="kmeans"` →
      `invalid_input` naming it; **every** kmeans-only control (`n_clusters`, `max_clusters`) supplied with
      `method="gmm"` → `invalid_input` naming it. No run persisted. (Review #1: the `max_*` bounds are
      `None`-defaulted + resolved internally so they cannot be silently ignored / mis-recorded in
      provenance; a companion test asserts they are forwarded when set on their own method.)
- [x] 3.8 **Invalid input — trait columns (split, via `_qc_shared` with `require_certified=True`):** (a)
      a column absent from the frame → `invalid_input` naming it; (b) a non-numeric metadata column →
      `invalid_input`; (c) a numeric column present but **outside the certified-clean set**
      (`frame.trait_cols`), incl. one carrying NaN → `invalid_input`, and assert the delegate spy was
      **not** called (no silent `dropna()`); (d) an explicitly empty `trait_columns=[]` → `invalid_input`
      (not "all traits"); (e) duplicate names → `invalid_input` naming the duplicate(s).
- [x] 3.9 **Degenerate fit → structured, not internal:** using the **real** delegate on a degenerate
      valid selection (a single certified constant trait, or `n_clusters > n_samples`), assert the tool
      returns `code == "assumption_violated"` (not `internal_error`), the **message names the degenerate
      condition** (mirror pca's `assert "<col>" in exc.value.message` — remedy is tested, not just the
      code), does not leak a traceback/backend path, and `store.list_runs(...)` is empty.
- [x] 3.10 **Non-finite guard:** a selected certified trait carrying `±inf` (which `dropna()` keeps) →
      `assumption_violated`, no run persisted.
- [x] 3.11 **Require-clean consumption (property):** with only a **raw** version registered → a
      `BloomMCPError` whose remedy names `qc_clean`, and no run committed; with a cleaned version seeded
      into the reader → the tool resolves it and `frame.source` starts `v` and ends `_cleaned`.
- [x] 3.12 **`qc_inspect` non-regression (guards BL-1):** assert `qc_inspect`'s trait-subset validation
      is **byte-identical** after the `_qc_shared` promotion — i.e. its call site uses the default
      (`require_certified=False`), so it still accepts empty/duplicate/non-certified selections it accepts
      today (write against current `qc_inspect` behavior; it must stay green through §4.5).

## 4. GREEN — implement the tool (+ the shared-validator promotion)

- [x] 4.1 Add `bloommcp/src/bloom_mcp/tools/clustering_tool.py`: `ClusteringParams` (`experiment`,
      `method: Literal["kmeans","gmm"]`, `trait_columns?`, `standardize=True`, `seed=42` with `ge=0`,
      kmeans `n_clusters?` with `ge=2` + `max_clusters?` with `ge=2`, gmm `n_components?` with `ge=1` +
      `max_components?` with `ge=1` + `covariance_type: Literal["full","tied","diag","spherical"] | None`).
      **All per-method controls (including the `max_*` bounds and `covariance_type`) are `None`-defaulted
      and resolved to the delegate default internally** (`max_clusters or 10`, `max_components or 5`,
      `covariance_type or "full"`) — this is what lets `_reject_wrong_method_controls` detect every
      cross-method control (review #1) rather than only the originally-`None` ones. Dispatch is an
      `if/else` on `method` (the "dispatch-table" framing collapsed to a two-arm branch). Plus
      `user_label?`, and `ClusteringResult` (experiment, source, method, `n_samples`, `n_features`,
      `n_clusters`, `cluster_sizes`, `silhouette_score`, `davies_bouldin_score`, `calinski_harabasz_score`,
      `feature_names`, method-scalars `inertia?` | `bic?`/`aic?`/`converged?`/`covariance_type?`, `run_ref`,
      `version_dir`, `manifest_path`, `outputs`).
- [x] 4.2 Tool fn `clustering(params, *, random_state: int, provenance: Provenance) -> ClusteringResult`
      wrapped by `@as_mcp_tool(input_model=…, output_model=…)` (**declares `random_state`** → contract
      records the resolved seed): read via `reader().load_experiment(params.experiment,
      require_clean=True)`; validate `trait_columns` via `_qc_shared._validate_trait_subset(...,
      require_certified=True)` (default to all of `frame.trait_cols` when omitted); assert
      `np.isfinite(frame.df[selected].to_numpy()).all()` (else `assumption_violated`); reject wrong-method
      cluster-count controls (`invalid_input`); dispatch via a `{method: (perform_fn, from_dict_fn,
      count_kwarg)}` table, calling `perform_fn(frame.df[selected], …, standardize=…,
      random_state=random_state)` then `from_dict_fn(result_dict, random_state=random_state)`.
- [x] 4.3 **Catch-and-remap for specific codes/remedies:** catch `CleanedVersionRequiredError` → `raise
      BloomMCPError(code=…, remedy="run qc_clean first …")`; catch the delegate's `ValueError` (degenerate
      fit) → `raise BloomMCPError(code="assumption_violated", remedy="pick a broader trait subset or fewer
      clusters …")` naming the offending condition. Do **not** rely on the `errors=` tuple to carry these
      codes.
- [x] 4.4 `register(mcp)` via `bloom_mcp.contract.register`; register in `bloommcp/src/bloom_mcp/server.py`
      (import + `clustering_tool.register(mcp)`) and add a `clustering` entry under the "Direct tools
      (granular)" docstring list **phrased to disambiguate from `run_clustering_workflow`** (e.g. "k-means
      / GMM on a cleaned experiment (require_clean; delegates to perform_kmeans_clustering /
      perform_gmm_clustering)"). Assert `import bloom_mcp` + server boot stay green.
- [x] 4.5 **Shared-validator promotion (BL-1, `require_certified` opt-in):** promote the certified-set /
      empty-list / duplicate-name rejections (today private to `pca_analysis_tool._validate_trait_subset`)
      into `_qc_shared._validate_trait_subset` behind a new **`require_certified: bool = False`** param
      (default = current "in-frame + numeric" behavior). `qc_inspect`'s call site is unchanged (default);
      §3.12 pins it byte-identical. Migrate `pca_analysis_tool` to consume the shared helper with
      `require_certified=True` and **delete its private copy** — but only if the migration is
      behavior-preserving per **pca's full test suite** (§9.4 runs it); if not cleanly preserving, leave
      pca's private copy and file an immediate follow-up (clustering still consumes the shared helper, so
      no fourth copy is added either way).

## 5. GREEN — persist the run with lineage + return links

- [x] 5.1 Persist via `store().create_run(experiment=…, tool_class="clustering", provenance=provenance,
      user_label=…, source_csv=<temp snapshot of frame.df>)`; set the cleaned-source lineage on a **copy**
      of the stamped provenance — `provenance.model_copy(update={"based_on_version": frame.source})` (the
      `pca_analysis` non-proliferating pattern, **not** `remove_outliers`' in-place mutation). Write
      `labels.csv` (frame's `metadata_cols` prepended to the per-sample cluster label, row-aligned) and
      `cluster_result.json` (`result.to_json()`) into `run.staging_dir`, then `store().commit(run, {...})`.
- [x] 5.2 Return the cluster summary inline + `run_ref` / `version_dir` / `manifest_path` / `outputs`. Add
      tests: `set(stored.output_keys) == {"labels.csv","cluster_result.json"}`; `stored` records
      `based_on_version == frame.source`; the contract-held `provenance` object was **not** mutated
      (model_copy isolation — parity with pca); the result carries **no** N-length label vector inline
      (`assert not any(isinstance(v, list) and len(v) > 50 for v in result.model_dump().values())` —
      `cluster_sizes`/`feature_names` are short; the 153-label vector is not inline); a second `clustering`
      run increments the version (`v1`→`v2`, `latest`→`v2`, `v1` still retrievable); `labels.csv` carries
      the identity columns in row-aligned order.

## 6. Live persistence smoke — composition leg + stale-enumeration doc updates (BL-3)

- [x] 6.1 Extend `bloommcp/scripts/live_persistence_smoke.py` with a `clustering` leg: after the existing
      `qc_clean` leg commits a cleaned version, run `clustering(method="kmeans", …, seed=42)` through the
      real `SupabaseReader` / `SupabaseResultStore`; assert `require_clean=True` resolves the `v<N>_cleaned`
      source, the manifest is `manifest_schema_version == 3`, records `based_on_version` = the consumed
      cleaned version and `seed == 42`, and each `output_sha256` matches the stored bytes. (This leg runs in
      the `dev-stack-smoke` PR gate — it must be green.)
- [x] 6.2 Factor the leg's pure decision logic into importable helpers and unit-test them in
      `tests/scripts/` (the existing `test_live_persistence_smoke_logic.py` is the template; helpers must
      not import anything requiring a live stack at collection time, so they run in the fakes suite).
- [x] 6.3 **Update the live-smoke enumeration docs so they don't go stale (the `remove_outliers` sibling
      caught this):** `bloommcp/README.md` (~L33) and `DEV_SETUP.md` (~L225) — reword the "drives
      clustering, `qc_clean`, and `remove_outliers`" sentence to a **non-exhaustive** phrase (e.g. "drives
      the granular bloom-mcp tools end-to-end") so future legs need no churn. `bloommcp/docs/local-
      validation.md` — bump "runs **three** legs" → four (~L45), extend the `SMOKE PASSED` summary block
      (~L100), and add a **"Leg 4 — clustering (granular tool)"** section, **disambiguating** it from the
      existing "Leg 1 — clustering" (retitle that to "Leg 1 — clustering (legacy workflow)") so the file
      does not carry two identically-titled legs.

## 7. Upstream hierarchical gate (deferred fast-follow — file the issue now, no repo change)

- [ ] 7.1 File (or reopen `talmolab/sleap-roots-analyze` #129, closed-but-incomplete) an issue for the
      **hierarchical** blockers: a public labeled-clustering entry point returning a `ClusterResult`
      (labels + sizes + scores, composing `perform_hierarchical_clustering` →
      `calculate_optimal_clusters_hierarchical` → cut-tree → `calculate_cluster_quality_metrics`
      **upstream**), a `from_hierarchical_dict` adapter, and `Optional` `ClusterResult.random_state`
      (hierarchical is deterministic) — targeting `0.1.0a5`. Link it from #309. **This is a pre-merge
      checklist item — a GitHub action, not a commit.**
- [ ] 7.2 (fast-follow, gated) once `0.1.0a5` ships: add `"hierarchical"` to the `method` `Literal` + one
      dispatch-table row (deterministic → the tool records `seed = None` for that branch); extend the
      fixture + tests. **Not part of this PR.**
- [ ] 7.3 File (or fold into the 7.1 issue) the **GMM auto-select BIC/AIC bug** in
      `perform_gmm_clustering` (0.1.0a4): after re-fitting the BIC-selected model it returns the *last
      candidate's* `bic`/`aic` (`bic_scores[-1]`), not the selected model's. `clustering_tool`
      works around it (`_gmm_selected_scores`, reading `bic_scores[n-1]`); **re-verify and drop that
      workaround on the `0.1.0a5` bump.** A GitHub action, not a commit.

## 8. Agent-surface validation, docs + verification

- [ ] 8.1 **Validate on Claude Desktop (capable model) and sanity-check a small model** (a #309
      acceptance criterion): confirm `clustering` is discoverable, the schema is legible to a small model,
      a happy-path kmeans call on a cleaned experiment returns the cluster summary + links, the "run
      qc_clean first" remedy renders when no cleaned version exists, and `method="gmm"` returns the
      gmm-shaped summary.
- [x] 8.2 Update the `server.py` module docstring (done in 4.4). Do **not** edit
      `bloommcp/docs/roadmap.md` (its tier-number reshape is owned by #339) and do **not** add a category
      line to `bloommcp/README.md` (its tool-category sentence already lists "clustering" — per the
      `pca_analysis` precedent; only the smoke-leg sentence in §6.3 changes).
- [x] 8.3 `wsl -d Ubuntu -- bash -lc 'cd ~/repos/bloom && npx --yes -p @fission-ai/openspec openspec
      validate add-bloommcp-clustering-tool --strict'` is clean.
- [x] 8.4 Tests + lint green: `cd bloommcp && uv run --frozen --extra test pytest
      tests/tools/test_clustering_tool.py tests/tools/test_qc_inspect_tool.py
      tests/tools/test_pca_analysis_tool.py` (the last two guard the §4.5 promotion is behavior-preserving)
      **and the full bloommcp suite**; `uv run black --check .`, `uv run ruff check .`, and `pre-commit run
      --all-files` (or `/lint`).
- [x] 8.5 `/pre-merge` → `/pr-description` → PR to `staging` linking #309 (stage by explicit path;
      confirm `=0.1.0a4` and `bloommcp/data/` are gitignored/absent from the index).

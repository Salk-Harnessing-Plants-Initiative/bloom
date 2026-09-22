## 0. Pre-flight — the baseline, recorded before anything changed

- [x] 0.1 **DONE 2026-09-21.** Both comparators run against `runai-busch-lab` minutes apart, verbatim
      below rather than summarised, because a summarised verdict is what made task 9.6 unattributable.

      bloom's, from this worktree at `dfab7c6f`, pin `310aae63`:
      ```
      $ python3 scripts/check_registered_templates.py
      sleap-roots-images-downloader-template     DRIFT
          -    "image": "ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-0614889",
          +    "image": "ghcr.io/salk-harnessing-plants-initiative/bloomctl:sha-28034f6",
      sleap-roots-predictor-template             DRIFT
          +      "name": "SRP_PREDICT_CONTAINER_DIGEST",
          +      "value": "sha256:4d4064c6ac8dadc1bedcba594c74f0d7c4b9d907ee9001999d34e664317a4060"
          -    "image": ".../sleap-roots-predict:sha-e025e309...",
          +    "image": ".../sleap-roots-predict:sha-e025e309...@sha256:4d4064c6...",
      sleap-roots-trait-extractor-template       DRIFT
          +      "name": "SRT_TRAITS_CONTAINER_DIGEST",
          +      "value": "sha256:ab5a1f43a74f2d00e809f2deb0dc886876028cc3028b0fdaf600f408e860f369"
          -    "image": ".../sleap-roots-trait-extractor:sha-689cffb",
          +    "image": ".../sleap-roots-trait-extractor:sha-689cffb@sha256:ab5a1f43...",
      sleap-roots-write-back-template            DRIFT   (same bloomctl bump)
      sleap-roots-exit-gate-template             DRIFT   (same bloomctl bump)
      DRIFT: at least one registered template does not match the pin.
      EXITCODE=1
      ```

      upstream's, from `C:\repos\sleap-roots-pipeline`, **clean working tree, `04c2fc1c` on `main`**
      (recorded because this comparator reads its own working tree, not `origin/main`):
      ```
      $ bash scripts/check_cluster_drift.sh
      IN SYNC         sleap-roots-exit-gate-template
      IN SYNC         sleap-roots-images-downloader-template
      IN SYNC         sleap-roots-predictor-template
      IN SYNC         sleap-roots-trait-extractor-template
      IN SYNC         sleap-roots-write-back-template
      === cluster is IN SYNC with the repo ===
      EXITCODE=0
      ```

      Every bloom diff is an image tag, digest, or digest env var. Structural contract confirmed intact
      by direct inspection: all five inner `template:` names match their `templateRef.template`, and
      `sleap-roots-exit-gate-template` declares exactly `images-downloader-code`, `predictor-code`,
      `trait-extractor-code` — with **no defaults**, which is why contract item 4 below matters.

- [ ] 0.2 Capture one real object as the fixture shape, so the mocked suite inherits reality rather
      than an invented shape: `kubectl get workflowtemplate sleap-roots-exit-gate-template -n
      runai-busch-lab -o yaml`, trimmed, committed under `tests/unit/fixtures/`, with the capture date
      in a comment. The script being replaced derived its normalisation list from real objects
      ("Measured against all five templates, 2026-09-16"); dropping that discipline is how a rewrite
      ships a second wrong invariant.

## 1. Rename and seam first — the tests have nothing to grip without them

- [ ] 1.0 `git mv scripts/check_registered_templates.py scripts/check_template_contract.py`, as a
      distinct commit before any content change so the rewrite's diff stays reviewable. Drift is
      upstream's question and bloom has no standing to assert it; a name that keeps saying otherwise
      keeps inviting the comparison that was wrong. The `DRIFT` banner is purged in 3.10 along with it.
      Nothing automated references the old path (no CI job, no Makefile target), so the only breakage
      is the archived task 1.1 invocation, which 5.2 annotates with the new one.
- [ ] 1.1 Add `--workflow PATH` (defaulting to `services/workflows/vendored/sleap-roots-pipeline.yaml`)
      and factor the body into `check_contract(workflow_path: Path, namespace: str) -> int` that
      returns an exit code instead of calling `sys.exit`. Mirrors
      `check_vendored_workflow_drift.py`'s `check_drift(vendored_path, ref_path) -> int`, which its
      tests call directly with `tmp_path` files. Without this, none of section 2 can address the code
      and task 4.3's negative control cannot run without editing the vendored file.

## 2. RED — tests before the rewrite, all failing for the stated reason

New file `tests/unit/test_check_template_contract.py`. Conventions from
`tests/unit/test_check_vendored_workflow_drift.py`: load via `importlib.util.spec_from_file_location`,
patch **named module functions** (`_probe_cluster`, `_fetch_live`) rather than only `subprocess.run`,
and add one test asserting the real `kubectl` argv so a typo cannot hide behind the mocks — the
pattern `tests/unit/test_check_uv_locks.py` uses for the same reason.

- [ ] 2.1 Fixtures: a vendored `Workflow` builder (templateRefs, per-task `arguments.parameters`,
      `spec.arguments.parameters`, `spec.volumes`) and a `kubectl` stub returning caller-supplied
      `WorkflowTemplate` YAML per object name. **Guard the no-network property in the fixture itself:**
      `monkeypatch.setattr(module.urllib.request, "urlopen", _fail_if_called)` — without it the RED
      runs make five live `raw.githubusercontent.com` calls, since `_fetch_pinned` runs before
      `_fetch_live`.
- [ ] 2.2 **The bloom#879 regression test, and the centrepiece.** A conforming cluster whose templates
      carry different image tags and digests SHALL exit 0. **The fixture must be a faithful copy of the
      pinned upstream spec with only the tags/digests/env vars altered** — captured from the sibling
      checkout at `310aae63`. A hand-built fixture also goes red today, but on `priorityClassName`,
      `retryStrategy`, `timeout` and `resources`, so it would flip red→green without ever
      demonstrating the image-pin defect. Assert the reason, not just the colour.
- [ ] 2.3 Missing `WorkflowTemplate` (genuine `NotFound`) → exit 1, names the object.
- [ ] 2.4 Inner template name mismatch → exit 1, names both. The defect that cannot fail at submit.
- [ ] 2.5 Parameter passed but not declared → exit 1, names it.
- [ ] 2.6 **Required input not supplied** → exit 1, names it. An `inputs.parameters` entry with neither
      `default` nor `value`, supplied by neither the task nor `spec.arguments.parameters`. The direction
      that actually fires; fails at the DAG's only leaf, after write-back has committed.
- [ ] 2.7 A declared input that *does* carry a `default` and is legitimately not passed → exit 0.
      Forward-looking: no upstream input has a default today, so this pins the distinction before it
      matters.
- [ ] 2.8 `volumeMounts[].name` not in `spec.volumes` → exit 1, names the volume.
- [ ] 2.9 `{{workflow.parameters.X}}` not declared in `spec.arguments.parameters` → exit 1, names it.
- [ ] 2.10 Digest env var disagreeing with its own container `image` digest → exit 1, names both.
- [ ] 2.11 Digest env var agreeing with its own image, on an image whose tag matches nothing this repo
      records → exit 0. Pins that self-consistency cannot resurrect bloom#879.
- [ ] 2.12 **The false-positive trap.** A workflow-level global (`scan-ids`) that no inner template
      declares as an `inputs.parameters` entry SHALL exit 0. Folding `spec.arguments.parameters` into
      the per-task "passed" set would exit 1 on today's correct cluster — bloom#879 in a new costume,
      since `images-downloader`'s template declares no `inputs` at all. Derive from the real case.
- [ ] 2.13 The shape 4 of 5 tasks actually have: no `arguments` key at all, resolving to a template
      with no `inputs` key at all → exit 0. Parametrise over `inputs` ∈ {absent, `{}`,
      `{parameters: []}`} × `arguments` ∈ {absent, empty} — that is 80% of production, and naive
      subscripting `KeyError`s on it.
- [ ] 2.14 Unreachable cluster → exit 2, and no template reported missing.
- [ ] 2.15 Per-object `kubectl` failure that is **not** `NotFound` → exit 2, not 1. Separately: stub
      raising `FileNotFoundError` (`kubectl` absent — the likeliest workstation failure, since
      `kubectl` lives in WSL) → exit 2; `subprocess.TimeoutExpired` → exit 2.
- [ ] 2.16 `kubectl` exits 0 with output that is not a `WorkflowTemplate` (proxy error page, truncated
      stream) → exit 2, and does not report the template missing.
- [ ] 2.17 Vendored `Workflow` missing / unparseable YAML / parses to non-mapping (`yaml.safe_load("")`
      → `None`) / lacks entrypoint-and-DAG structure → exit 2 in every case. Uncaught, a `yaml.YAMLError`
      exits 1 and collides with the violation code — the precedent suite records this exact hazard for
      its own script.
- [ ] 2.18 Discovered `(name, template)` set ≠ the committed expected set → exit 2, names both sets.
      Set equality, not a count compared against itself.
- [ ] 2.19 Both a violation and a could-not-check in one run → exit 2, and both appear in the output.
- [ ] 2.20 Two simultaneous violations of *different* kinds (missing object + wrong inner name) are
      both named in one run — pins the collect-don't-short-circuit promise, which a fail-fast
      implementation would otherwise satisfy silently.
- [ ] 2.21 Image references are printed on the **violation** path as well as the success path.
- [ ] 2.22 Exit-code contract: `EXIT_OK == 0`, `EXIT_VIOLATION == 1`, `EXIT_UNAVAILABLE == 2`, all
      distinct, and the violation and could-not-check *messages* differ.
- [ ] 2.23 Duplicate `templateRef`s: two tasks referencing the same `(name, template)` with different
      parameters are both checked — expectations collected per *task*, not keyed by template name,
      which would let the second silently overwrite the first.
- [ ] 2.24 No outbound network request on any path (`urlopen` patched to raise).
- [ ] 2.25 Confirm 2.2–2.24 fail for the intended reason before writing implementation; paste the
      failure summary here. A test that fails for the wrong reason is not RED — see 2.2.

## 3. GREEN — rewrite the comparator

- [ ] 3.1 Resolve the DAG through `spec.entrypoint`, assert exactly one `dag` template and that it is
      the entrypoint. Cite `services/workflows/tests/test_k8s_client.py`'s `_dag_tasks`, which documents
      why indexing `spec.templates[0]` was removed as a bug.
- [ ] 3.2 Extract the contract: per task, `templateRef.name`, `templateRef.template`, and the task's
      own `arguments.parameters`; plus the `Workflow`'s `spec.arguments.parameters` and `spec.volumes`
      as separate workflow-level sets. Keep them separate — conflating them is 2.12.
- [ ] 3.3 Expected-set guard (2.18) before any comparison is believed.
- [ ] 3.4 Reachability probe. Use `kubectl auth can-i get workflowtemplates -n <ns>` or tolerate a
      `Forbidden` on a list — probing with `list` while asserting with `get` would report could-not-check
      on a kubeconfig holding `get` but not `list`.
- [ ] 3.5 Per template: the six assertions, collecting all violations. Classify `kubectl` failures —
      only `NotFound` is a missing object. Pass `timeout=` on every invocation; the current script
      passes none, so a wedged VPN hangs the gate indefinitely.
- [ ] 3.6 Use `(tmpl.get("inputs") or {}).get("parameters") or []`-style access throughout; four of
      five templates have no `inputs` key at all.
- [ ] 3.7 Print image references, digests and digest env vars on every path, affecting no verdict
      except via 2.10. Include the advisory naming srp#72 where a template is tag-pinned with
      `imagePullPolicy: IfNotPresent` (the exit gate), and print the three `bloomctl` references.
- [ ] 3.8 Exit-code precedence: could-not-check dominates a violation (2.19).
- [ ] 3.9 Delete `RAW_URL`, `REF_FILE`, `_fetch_pinned`, `_strip_server_defaults`, `_DEFAULTED_EMPTY`,
      **and the now-unused imports `difflib`, `json`, `urllib.error`, `urllib.request`.** CI's ruff is
      scoped to `scheduled-jobs/` only, so nothing automated will catch leftover dead imports — this
      list is the only guard.
- [ ] 3.10 Rewrite the module docstring: source of truth is the vendored `Workflow`; the six
      assertions; images not compared against any recorded pin and why; `--namespace` mirrors
      `WORKFLOWS_K8S_NAMESPACE`; and point to upstream's `check_cluster_drift.sh` (staleness) and
      `check_manifests.py` (repo-internal image invariants, manual — that repo has no CI) for the
      questions this comparator deliberately does not answer.
- [ ] 3.11 2.2–2.24 green. Run `uv run --extra test pytest tests/unit/test_check_template_contract.py -v`
      and paste the summary. Scoped deliberately: a bare `pytest tests/unit/` cannot be green on this
      workstation — `tests/unit/test_weekly_backup.py` fails collection with
      `AttributeError: module 'os' has no attribute 'geteuid'`, pre-existing and unrelated. Full-suite
      evidence comes from CI.

## 4. Live verification — the proof the crying wolf stopped

- [ ] 4.1 Re-run against `runai-busch-lab`; record the **observed** output verbatim and its exit code.
      Expected 0. Record what was observed even if it disagrees, and leave this unticked with the
      reasoning written out if it does.
- [ ] 4.2 Negative control via the real `kubectl`: point `--workflow` at a copy naming a nonexistent
      `templateRef.name`, confirm exit 1. This is the one assumption unit tests cannot reach — that a
      real `kubectl get` on a nonexistent object exits non-zero *with* a `NotFound` the classifier
      recognises. Use a copy; never edit the vendored file.
- [ ] 4.3 Negative control for a wrong inner template name, same way, exit 1.
- [ ] 4.4 Confirm the captured fixture (0.2) still matches the live object's shape — that real `-o yaml`
      puts `inputs.parameters`, `volumeMounts` and `templates[].name` where the parser looks.

## 5. Correct the records this defect already corrupted

- [ ] 5.1 `openspec/changes/fix-cyl-redelivery-blob-collision/tasks.md` task 9.6. Mark the 2026-09-21
      verification **retracted as unreproducible**, on the self-contradiction (task 9 was confirming the
      `sha-28034f6` bump had landed; this comparator reporting "IN SYNC with the pin" would have meant
      it had not — it cannot evidence both) and on the pin not having moved since #866. Do **not**
      speculate about which comparator ran; the record does not support an attribution, and an earlier
      draft of this proposal wrongly inferred one from the wording. Re-establish the substantive
      conclusion — the cluster does carry the bumped pins — from the dated, comparator-named run in 0.1.
      Show the correction; do not delete the claim. Cross-reference bloom#780, the same recurring
      pattern of a tick recorded without reproducible evidence. PR #871 is merged, so this file is not
      contended.
- [ ] 5.2 Annotate the archived `2026-09-21-vendor-five-task-pipeline-dag` in **three** places, not
      one: `tasks.md` task 1.1, and `proposal.md`'s two claims (the "confirmed in sync with the pin"
      assertion, and "makes tasks.md 1.1's pre-merge gate reproducible instead of prose"). One line
      each, pointing at bloom#879; leave the historical text intact. State in the annotation that the
      **2026-09-16 run recorded there was sound at the time** — nothing changed in the templates between
      the pin and 2026-09-17 — so a later reader does not over-correct a record that was in fact valid.
      Also note that 1.1's "record all three bloomctl image references" instruction remains live, since
      the bloomctl provenance gap is filed rather than closed.

## 6. Validation and merge

- [ ] 6.1 `openspec validate fix-registered-template-contract-check --strict` passes.
- [ ] 6.2 Re-confirm no other unarchived change carries a `### Requirement:` heading matching either of
      this change's two ADDED headings. Verified at authoring: no unarchived change touches
      `cyl-pipeline-dispatch`, and both deltas are ADDED, not MODIFIED, so the archive-ordering hazard
      does not apply. Re-check immediately before merge, since other changes land meanwhile.
- [ ] 6.3 `/review-pr` (5-lens adversarial). Fix what it finds, including in this change's own
      reasoning — the proposal review already corrected three of its author's claims.
- [ ] 6.4 `/pre-merge` green: lint + full suite + OpenSpec validation.
- [ ] 6.5 PR into **`staging`**, bundling proposal + implementation, closing bloom#879.
- [ ] 6.6 File the two follow-up issues named in the proposal's Impact: the `bloomctl` tag-to-commit
      provenance gap, and a standing reference for the `_fetch_live` exit-code conflation so it is not
      buried by #879's auto-close.
- [ ] 6.7 Comment on bloom#879 with the 0.1 evidence, the divergence from its suggested fix, and the
      correction that the srp#78 digests did **not** address srp#72's tag-mutability half. Authorized by
      the user on 2026-09-21 for this specific post; re-confirm before posting anything further.
- [ ] 6.8 Record the observation in `sleap-roots-pipeline` `docs/bloom-integration/roadmap.md` item A4
      — separate PR in the sibling repo, after the live runs are real rather than at merge time.
      Consider srp#58, the origin of upstream's comparator, as the more durable home for the
      two-comparators-answer-different-questions note.

## 1. Confirm scope (no-code due diligence)

- [ ] 1.1 Grep all of `langchain/` for any read of the five files under `SLEAP_OUT_CSV/`
      (`open(`, `read_csv`, `Path(...)/"SLEAP_OUT_CSV"`, the literal filenames). Confirm
      zero hits beyond the two illustrative-string references named below.
- [ ] 1.2 Confirm `bloommcp/src/bloom_mcp/tools/correlation_tools.py`'s `EXPERIMENTS`
      dict and `bloommcp/tests/tools/test_straggler_routing.py` read from
      `bloommcp`'s own `BLOOM_TRAITS_DIR` / `bloommcp/data/SLEAP_OUT_CSV`, not
      `langchain/SLEAP_OUT_CSV/` — no cross-container file dependency.

## 2. Write the regression test first (red)

`langchain/` has no test infrastructure of its own (no `pytest` dependency in
`langchain/pyproject.toml`, no `test_*.py`/`conftest.py` anywhere under it), so the
regression guard lives at the repo-root `tests/integration/` instead.

- [ ] 2.1 Add `tests/integration/test_langchain_packaging.py` asserting:
  - `langchain/SLEAP_OUT_CSV/` does not exist.
  - `langchain/prompts/router.py`'s `ROUTER_FEW_SHOTS` contains none of the deleted
    filenames (`cylinder_alfalfa_gwas_wave2`, `cylinder_amaranth_tis108_exp1`,
    `turface_rice_treatment_exp1`).
  - `langchain/tools/context_tools.py`'s `CONTEXT_MCP` contains none of those same
    deleted filenames.
- [ ] 2.2 Run it now and confirm it fails for the right reason: the directory still
      exists and the stale filenames are still present (red).

## 3. Make it green

- [ ] 3.1 `git rm -r langchain/SLEAP_OUT_CSV/` (all five CSVs).
- [ ] 3.2 `langchain/prompts/router.py:42` — replace
      `cylinder_alfalfa_gwas_wave2.csv` with `cylinder_traits.csv` in the router
      few-shot example.
- [ ] 3.3 `langchain/tools/context_tools.py:87` — replace
      `cylinder_alfalfa_gwas_wave2, turface_rice_treatment_exp1` with
      `cylinder_traits, turface_traits` in the `CONTEXT_MCP` block. Leave the rest of
      `CONTEXT_MCP` (the Phase-1 workflow tool names) untouched — that rewrite belongs
      to #438 (see task 5.1).
- [ ] 3.4 Re-run `tests/integration/test_langchain_packaging.py` — confirm green.

## 4. Verify

- [ ] 4.1 `du -sh langchain/` before/after, confirming the ~6.6MB drop.
- [ ] 4.2 `openspec validate remove-langchain-unused-sleap-out-csv --strict`.
- [ ] 4.3 `uv run --extra test pytest tests/integration/test_langchain_packaging.py -v`
      (root `bloom-tests`). Do not run `cd langchain && uv run pytest` — `langchain/`
      declares no `pytest` dependency, so that command errors rather than validating
      anything.

## 5. Close the #438 scope-gap risk

- [ ] 5.1 #438 (open draft PR) does not currently list `langchain/tools/context_tools.py`
      in its stated scope, even though #475 assumes #438 will eventually rewrite
      `CONTEXT_MCP`'s stale Phase-1 tool-name references. Flag this gap — a PR comment
      on #438, or a short tracking note — so the deferred half of ask #2 has an
      explicit owner instead of falling through the crack between the two changes.

## 6. Wrap up

- [ ] 6.1 Open PR against `staging`; body notes `Closes #475`.
- [ ] 6.2 After deploy, archive this change
      (`openspec archive remove-langchain-unused-sleap-out-csv --yes`).

## 1. Oracle / acceptance tests first (RED)

> All tests run against a **stub tool** + a **stub Pydantic params model** (see §3.0) with
> fakes only — no live Supabase, no live manifest write. Round-trip tests assert **exact**
> equality (`==`); `rtol=1e-6` / exact-int tolerance is reserved for the golden
> *recomputation* oracle in Tiers 3/4, not these serialization tests.

- [x] 1.1 `tests/contract/test_decorator_contract.py` — decorate a stub tool (declared
      Pydantic input/output models) with `@as_mcp_tool`: (a) valid input returns the
      validated output and the tool is discoverable via an in-process
      `fastmcp.Client(mcp).list_tools()` after `register(mcp)`; (b) invalid **input**
      surfaces as `BloomMCPError` and the stub body never ran (call-recording flag);
      (c) invalid **output** surfaces as `BloomMCPError` with an internal-breach `code`,
      never a raw `ValidationError`.
- [x] 1.2 `tests/contract/test_error_envelope.py` — a stub tool that raises a declared
      exception surfaces as `BloomMCPError` (`code` + `message` + `remedy`), never a raw
      traceback.
- [x] 1.3 `tests/contract/test_seed_propagation.py` — (a) with a seed: the fake
      `perform_*` receives it as `random_state=`, `Provenance.seed` equals it, and
      `np.random.get_state()` is byte-identical before/after (global RNG untouched);
      (b) with no seed: a concrete integer is resolved, forwarded as `random_state=`, and
      recorded in `Provenance.seed` (never null).
- [x] 1.4 `tests/contract/test_provenance_roundtrip.py` — a fully-populated contract-time
      `Provenance` (resolved seed, `environment`, resolved feature/column selection in
      `params`) round-trips Pydantic ↔ JSON with **exact** equality; per-artifact
      `output_sha256` / `key` collections are empty at contract time. Use a `hypothesis`
      `@given` strategy varying seed/environment presence and artifact count.
- [x] 1.5 `tests/contract/test_provenance_to_version_entry.py` — `to_version_entry(...)`
      maps a contract-time `Provenance` to a v3 `VersionEntry` with `seed`, `agent`,
      extended `code_versions`, `environment` set; preserves the v2 fields with their
      source values (`id`, `created_at`, `tool`, `params`, `based_on_version`,
      `code_versions`, `outputs`, `user_label`, `version_dir`); per-artifact
      `output_sha256` / `output_keys` left empty (filled by `ResultStore` in Tier 2). No
      `supabase_client` calls (conftest pops Supabase env as a backstop).
- [x] 1.6 `tests/contract/test_code_versions_installed_only.py` — (a) with analyze +
      contracts installed: their versions are recorded and no field equals `"unknown"`;
      (b) with `importlib.metadata.version` monkeypatched to raise `PackageNotFoundError`
      for a tracked name: that key is omitted entirely (not `"unknown"`).
- [x] 1.7 `tests/contract/test_environment_pointer.py` — (a) with `BLOOM_MCP_IMAGE_DIGEST`
      set: the `environment` pointer equals that `sha256:…` digest; (b) with it unset (the
      unit case): the pointer falls back per precedence to a non-empty value, is never
      `"unknown"`, and is not equal to the `code_versions` trace.
- [x] 1.8 `tests/contract/test_schema_v3.py` — (a) `CURRENT_SCHEMA_VERSION == 3` and a
      fresh `Manifest` reports `manifest_schema_version == 3`; (b) a v3 `VersionEntry`
      (retained string `outputs` + `output_sha256`/`output_keys` siblings + `seed`/`agent`/
      `environment`) round-trips `model_dump(mode="json")` ↔ re-validate exactly.
- [x] 1.9 `tests/contract/test_v2_backcompat.py` + **add fixture**
      `tests/fixtures/manifest_v2.json` (a hand-recorded schema-v2 manifest whose
      `VersionEntry` has a populated string-valued `outputs` and only real v2 keys): it
      validates via `Manifest.model_validate(...)` under v3 code, `manifest_schema_version
      == 2`, and the v3 fields are unset — proving the additive bump holds under
      `extra="forbid"`.

## 2. Manifest schema v2 → v3 (additive) (GREEN)

- [x] 2.1 `storage/schema.py`: bump `CURRENT_SCHEMA_VERSION = 3` and update the module
      docstring (schema version 2 → 3). **Retain `outputs: dict[str, str]` unchanged.** Add
      optional sibling collections `output_sha256: dict[str, str] = {}` and
      `output_keys: dict[str, str] = {}` to `VersionEntry`, plus optional `seed`, `agent`,
      `environment`. Keep `extra="forbid"`. (`outputs` is NOT re-typed — that is what keeps
      v2 string-valued `outputs` loadable.)
- [x] 2.2 `storage/schema.py`/`code_versions.py`: make `CodeVersions` fields omit-on-absent
      optional (no `"unknown"` default) for **all** entries; add optional
      `sleap_roots_analyze` / `sleap_roots_contracts`. Rewrite `_version_or_unknown` →
      omit-on-`PackageNotFoundError` resolution.
- [x] 2.3 Confirm `storage/manifest.py` `validate_schema` still accepts v2 and now v3
      (`<=` known); `KNOWN_SCHEMA_VERSION` tracks `CURRENT_SCHEMA_VERSION` automatically.

## 3. Provenance model + mapping (GREEN)

- [x] 3.0 `contract/models.py` (or reuse if #191 lands first): a **stub Tier-1 Pydantic
      params model** carrying `seed: Optional[int]` + the input/output models the decorator
      validates against. #191 (Pydantic input models) is OPEN/unmerged, so Tier 1 defines a
      minimal stub; real tool models arrive with the granular tools (Tiers 3/4).
- [x] 3.1 `contract/provenance.py`: canonical contract-time `Provenance` model (fields per
      the spec; `params` captures resolved feature/column selection + determinism params) +
      `to_version_entry(...)` producing a v3 `VersionEntry` (per-artifact fields left for
      `ResultStore`). Note `input_sha256` belongs on the experiment block, not the entry.
- [x] 3.2 Wire the `environment` pointer resolver: `BLOOM_MCP_IMAGE_DIGEST` →
      `bloom-mcp` version → `uv.lock` hash; document the precedence; field optional in unit
      env. Note `output_sha256` = hex SHA-256 over pre-upload bytes, computed at commit
      (Tier 2); carry forward to Tiers 3/4 the matplotlib-determinism caveat for plots.

## 4. Error type (GREEN)

- [x] 4.1 `contract/errors.py`: `BloomMCPError(code, message, remedy)` with a serializable
      structured form; an exception→error mapping helper distinguishing a user input error
      from an internal output-contract breach.

## 5. The decorator (GREEN)

- [x] 5.1 `contract/wrap.py`: `@as_mcp_tool` — validate Pydantic I/O, map exceptions →
      `BloomMCPError`, resolve + propagate seed → delegate `random_state=`, stamp the
      contract-time `Provenance` once, and attach a `register(mcp)` seam (no live `FastMCP`
      at decoration time; no `server.py` change). **Never** `np.random.seed()`.
- [x] 5.2 `contract/__init__.py`: export `as_mcp_tool`, `Provenance`, `BloomMCPError`.

## 6. Refactor + green gate

- [x] 6.1 Refactor for clarity/dedup; `from __future__ import annotations` + google
      docstrings on every new module.
- [x] 6.2 `cd bloommcp && uv run --extra test pytest` green (full suite, fakes only — no
      live Supabase); `import bloom_mcp` clean with no env set.
- [x] 6.3 `uv run black --check` + `uv run ruff check` clean (repo `/lint` convention).
- [x] 6.4 `openspec validate add-bloommcp-contract-layer --strict` passes.

## 7. Deferred (recorded, not done here)

- [x] 7.1 Consuming `sleap-roots-analyze`'s serializable result dataclasses
      (`PCAResult`/`ClusterResult`, #149/#151 — now **merged**) instead of reinventing a
      JSON projection is deferred to Tiers 3/4: Tier 1 records provenance over a **stub**,
      not a real result type, so there is nothing to consume yet.

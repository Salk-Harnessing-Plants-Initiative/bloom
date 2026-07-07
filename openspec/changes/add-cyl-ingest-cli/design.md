## Context

`bloomctl` (package `bloomctl`, `bloomcli/src/bloomctl/`) is a Click CLI with `login` and
`download`. The A4 pipeline's trait-extractor emits a per-scan `ResultEnvelope`
(`{provenance, traits, blobs}`, `sleap-roots-contracts` `0.1.0a3`) and the
`insert_cyl_result_envelope(envelope jsonb)` RPC (`SECURITY DEFINER`, sole writer of the cyl
trait tables) ingests it. The RPC:

- reads `provenance.{contract_version, scan_key, idempotency_key, inputs.image_ids}` plus
  top-level `traits[]` / `blobs[]`;
- accepts `contract_version` `0.1.0a3` (single leading `v` stripped) — set by PR #399/#393;
- is **first-writer-wins** on `provenance.idempotency_key` (`ON CONFLICT DO NOTHING`) and
  returns `{source_id, scan_id, trait_count, blob_count, was_noop}` — `was_noop=true` on
  re-delivery (it does **not** raise);
- `RAISE EXCEPTION`s on every validation failure (structure, contract version, empty
  idempotency key, scan_key consistency, `image_ids → cyl_images.scan_id` resolution, trait
  grain/name, blob file_size);
- grants `EXECUTE` to `bloom_writer` / `bloom_admin` / `service_role`.

This capability is the **client** of that RPC. It owns reading + validating the envelope,
calling the RPC, and turning the RPC's return/errors into good CLI UX. It owns no server logic.

## Goals / Non-Goals

- **Goals**: a `cyl ingest-result` command that (1) reads an envelope from a path or stdin,
  (2) validates it against `sleap-roots-contracts` before any network call, (3) calls the RPC
  with the original JSON, (4) reports the first-writer-wins no-op as a benign success distinct
  from a real error, (5) maps RPC validation errors to actionable messages, (6) optionally emits
  the RPC result as JSON for A4. Pure helpers separated from Supabase I/O for unit-testability.
- **Non-Goals**: non-interactive/scoped auth (#398); blob byte-upload to MinIO/Box (deferred to
  #407, which will **extend this command** — upload the `.slp` bytes + populate the refs before
  the RPC call); batch/glob ingest (one envelope per invocation, matching A4's per-scan loop);
  ledger/`source_id` recording (A4 Argo template's job — CLI just exposes it via `--json`);
  any RPC/schema change.

## Decisions

- **Command surface: a `cyl` group.** `bloomctl cyl ingest-result <envelope>` rather than a flat
  `bloomctl ingest`. The write path is assay-specific, matches the legacy Node CLI's `bloom cyl`
  framing, and aligns with the `@cli.group(...)` pattern PR #385 introduces for `list`. `login`
  and `download` stay flat (non-breaking); they can migrate later.
- **Validate, then send the original JSON.** Gate with `ResultEnvelope.model_validate(data)`
  (Pydantic v2) for a fast, readable failure; send the **original parsed dict** to the RPC —
  not `model_dump()`. Re-serializing risks the model recomputing/normalizing
  `provenance.idempotency_key` (the model auto-fills it), which is the RPC's first-writer-wins
  identity — a drift there could double-ingest or mask a stale result. The producer's bytes are
  the source of truth.
- **Dependency pin `sleap-roots-contracts>=0.1.0a3`** (no `[pandas]` extra — only the models are
  needed). Open `>=` floor per repo convention; the a3 floor is intentionally stricter than
  bloommcp's `>=0.1.0a1` because the CLI must validate a3 envelopes (an older model could reject
  a3-only provenance fields). Installability of a3 is confirmed during implementation.
- **Idempotent UX via `was_noop`.** Exit 0 for both a fresh insert (`was_noop=false`) and a
  re-delivery (`was_noop=true`), distinguished by message. Non-zero only on validation/auth/RPC
  errors. The RPC's return summary (`source_id, scan_id, trait_count, blob_count, was_noop`) is
  **owned by `cyl-trait-writeback`**; this command treats it as an opaque pass-through and never
  reshapes it. Note the RPC returns a **null `scan_id` on a no-op** re-delivery, so
  `summarize_result` and `--json` must not assume `scan_id` is present on that path.
- **Error mapping is table-driven.** A pure `map_rpc_error(message)` maps each known RPC
  `RAISE EXCEPTION` substring to actionable text and passes unknown messages through verbatim
  (never swallowed). The headline case — `no image_ids…` / `unresolvable image_ids: matched X of
Y…` / `image_ids resolve to N scans, expected exactly 1` — names the offending ids and points
  at the profile/server: the scan's `cyl_images` must already exist on **this** Bloom.
- **`--json` output.** bloomctl's first machine-readable output (existing commands print
  human/`rich` text). Justified by A4's need for `source_id`; `--json` → result object on stdout,
  default → a human summary line via `click.echo`. Naming coordinated with @blm3886 so future
  commands stay consistent.
- **Module structure mirrors `download.py`.** New `ingest.py` splits pure helpers
  (`load_envelope`, `validate_envelope`, `summarize_result`, `map_rpc_error`) from Supabase I/O
  (`call_insert_envelope`) at the `# --- supabase / storage I/O ---` marker. `cli.py` gains a
  `cyl` group + `ingest-result` subcommand and a shared `_authed_client(profile)` helper.
- **Auth reuse + #385 overlap.** `ingest-result` uses the `_authed_client(profile)` helper that PR #385
  also introduces (and points `download` at). To keep this PR self-contained against `staging`
  (which lacks it), this change adds the identical helper; whichever of #385/#397 merges second
  drops its copy on a trivial rebase.

## Risks / Trade-offs

- **`_authed_client` collision with #385** → both PRs add the same helper (see the Decision
  above); trivial rebase to a single definition.
- **Model gate is stricter than the RPC.** `ResultEnvelope` requires ~8 provenance fields the
  RPC ignores (`inputs.images_checksum`, `predict_*`, `traits_*`, `params`), so a hand-crafted
  "RPC-minimal" envelope fails validation. Mitigation: the fixture is real emitter output (full
  provenance); malformed-envelope tests use targeted mutations; the integration envelope is built
  from seeded ids with full provenance (not by mutating the fixture's `image_ids`).
- **Integration test CI-safety.** bloomctl tests run in CI **unfiltered** (`pr-checks.yml` has no
  `-m`), so the integration test's skip is load-bearing. Mitigation: a **module-level** skip guard
  evaluated at collection time, **plus** `-m "not integration"` added to both CI invocations.
- **prettier vs the byte-stable fixture** → prettier would rewrite the committed envelope.
  Mitigation: exclude `bloomcli/tests/fixtures/*.result.json` from the prettier pre-commit hook;
  pre-empt gitleaks false-positives with a fixture-scoped `.gitleaksignore` if needed.
- **New `--json` convention** could diverge from future commands. Mitigation: document it and
  coordinate naming with @blm3886.

## Migration Plan

No schema/RPC migration. Rollout: land command + tests in one PR on `staging`; after merge hand
the working `bloomctl cyl ingest-result` invocation to the A4 write-back step (EPIC
`talmolab/sleap-roots-pipeline#10`). Rollback is code-only (revert the PR); no data effects (the
RPC is idempotent and unchanged).

## Open Questions

- None blocking.

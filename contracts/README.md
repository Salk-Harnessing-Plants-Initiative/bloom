# Pinned `sleap-roots-contracts` (Bloom consumer)

This directory pins the cross-language **result contract** that the sleap-roots pipeline
produces and Bloom consumes (sub-project #1 → #2; change `pin-sleap-roots-contract`, #294).
The contract itself is owned by
[`talmolab/sleap-roots-contracts`](https://github.com/talmolab/sleap-roots-contracts); Bloom is
the consumer and only **pins** a version, codegens TypeScript from it, and checks its DB schema
against it in CI.

## What's here

| Path                                 | What                                                                                                         | Authority                                                 |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------- |
| `schema/result_envelope.schema.json` | Vendored, LF-normalized copy of the pinned JSON Schema                                                       | Faithful copy of the published artifact — **do not edit** |
| `pin.json`                           | The pin manifest: `package`, `version`, full schema `$id`, `source`, file paths                              | The declared pin                                          |
| `generated/result-envelope.ts`       | TypeScript types (`ResultEnvelope`/`Provenance`/`TraitValue`/`BlobRef` + sub-defs) generated from the schema | Emitted by codegen — **do not edit by hand**              |

**Currently pinned: `v0.1.0a1`.** These are the _contract_ types (from the JSON Schema), distinct
from the Supabase `database.types.ts` (generated from the database by `make gen-types`).

> Note on `v0.1.0a1` vs `v0.1.0`: issue #294 named `v0.1.0a0` and preferred a non-prerelease
> `v0.1.0` cut. That release does not exist yet, so we pin the current `v0.1.0a1` (`result_envelope`
> is byte-identical to `a0` except its `$id`). When `v0.1.0` is cut, re-pin per below — it will be a
> zero-TS-diff event (see the `$id` no-op rule).

## Guards (CI)

- **Drift guard** — `npm run contracts:check` (`scripts/contract_types.mjs --check`), in the
  `build-and-audit` job: regenerates the TS from the pinned schema and fails if the committed
  `generated/` types are not byte-identical, and fails if `pin.json` disagrees with the schema
  `$id` (exact `id` + parsed `version`).
- **Negative-path / `$id`-no-op test** — `node --test scripts/contract_types.test.mjs`, same job.
- **Migration-matches-schema** — `tests/integration/test_contract_migration_match.py` (in
  `compose-health-check`): asserts Bloom's applied DB schema agrees with the pinned contract for
  the mappings built today, and the contract-side facts that justify them.

## The `$id`-restamp-is-a-no-op rule

The schema `$id` carries the package version (`…/schema/v0.1.0a1/result_envelope.schema.json`), so a
re-pin re-stamps the `$id` even when the payload is unchanged. The codegen **never emits `$id`** into
the types, so a `$id`-only re-pin regenerates **byte-identical** TS. Treat that as a structural
no-op — not a contract revision. The intended diff on such a re-pin is exactly two things (kept in
lockstep by the pin-consistency check): the schema `$id` and `pin.json` `version`/`id`. A _real_
field change produces a TS diff and fails the drift guard — that is the signal to review.

## Re-pin procedure

1. Replace `schema/result_envelope.schema.json` with the new published schema (keep it LF; it is
   excluded from repo prettier — see below).
2. Update `pin.json` `version` and `id` to the new version.
3. Run `npm run contracts:gen` to regenerate `generated/result-envelope.ts`; commit it.
4. Run `npm run contracts:check` — it passes when `pin.json`, the schema `$id`, and the regenerated
   types all agree. For a `$id`-only bump the types diff is empty; any other diff is a real contract
   change to review.

## Gotchas

- **Never run prettier from inside `contracts/`.** The repo `.prettierignore` excludes
  `contracts/generated/` and `contracts/schema/`, but that ignore is **root-relative** — running
  prettier from a subdirectory bypasses it and would reformat the generated/vendored files,
  breaking the byte-for-byte drift guard.
- `generated/*.ts` is pinned to LF via `.gitattributes` so the guard is reproducible on Windows and
  Linux.

## Consumer hand-offs (recorded for later changes, not enforced here)

The generated types have **no consumer yet** (changes B/C/D/G pending). When the write-back path is
built, the consumer (D/G) MUST, at the write boundary:

- validate `provenance.contract_version` against the pinned `version` (or an explicit compatibility
  set) — the per-row provenance-of-origin anchor;
- validate each `TraitValue.value` is finite-or-null (the contract normalizes NaN/inf → null).

The reproducibility anchors `inputs.images_checksum` / `image_ids` and `params.param_hash` ride
inside the opaque `metadata` jsonb and are not promoted to columns by change A.

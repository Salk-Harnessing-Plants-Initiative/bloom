## MODIFIED Requirements

### Requirement: Documentation of Output Destinations

Documentation SHALL describe where bloommcp analysis outputs actually go by default (Supabase
Storage, backed by MinIO in local dev) and how to reach them (MinIO console, Supabase Studio,
the MCP read tools), and SHALL clarify that `BLOOM_OUTPUT_DIR` and `BLOOM_USE_LOCAL` do **not**
produce local CSVs by default. Documentation SHALL describe the opt-in
`BLOOM_STORAGE_BACKEND=local` backend, the `BLOOM_STORAGE_LOCAL_ROOT` root variable (and its
fallback to `BLOOM_OUTPUT_DIR`), the resulting on-disk layout keyed by storage key, and the
warning that backends MUST NOT be mixed for one experiment. Documentation SHALL additionally
describe: `create_signed_url` and the `output_links` field every consumer-tool result carries
(one signed/served URL, hash, and size per output); the `BLOOM_STORAGE_URL` env var the local
backend uses to construct a served URL (and that this requires an operator-configured HTTP
server for the local storage root — bloommcp does not run one); the `BLOOM_PUBLIC_SUPABASE_URL`
env var used to rewrite a Supabase-backed signed URL off the internal Docker host onto a
publicly reachable base; the chosen signed-URL expiry, named by its code constant rather than
restated as an independent number; and the chosen inline-vs-link size threshold, explicitly
flagged as documentation-only guidance (not enforced in code — no tool changes its response
shape based on it).

**Documentation of the `local` backend's offline/local-only guarantee SHALL state it as "no
experiment data leaves your machine," not as an absolute claim that no network activity of any
kind occurs** (e.g. NOT "no connection to the shared server at all," "nothing reaches the
network," or "run fully offline" used as a standalone guarantee). This distinction matters
because a component outside the storage backend itself (bloommcp's usage-telemetry recording,
`bloommcp-caller-identity` capability) is a separate code path that could independently attempt
outbound activity regardless of which storage backend is selected; the storage backend's own
guarantee is specifically about where experiment input/output data is written and read, not a
claim about every code path in the process. Every location making this guarantee — in
`bloommcp/docs/connecting-claude-code.md`, `bloommcp/docs/storage-backends.md`, and
`_WIKI/BLOOMMCP/README.md` — SHALL use the same, narrower phrasing, so the guarantee doesn't
drift out of sync across documents.

#### Scenario: Default destination is documented

- **WHEN** a developer reads the storage docs
- **THEN** they learn outputs go to Supabase Storage by default (MinIO-backed in dev), how to
  reach them, and that `BLOOM_OUTPUT_DIR` / `BLOOM_USE_LOCAL` do not by themselves write local CSVs

#### Scenario: Opt-in local backend and its caveats are documented

- **WHEN** a developer wants real CSV/JSON/PNG files on disk
- **THEN** the docs show setting `BLOOM_STORAGE_BACKEND=local` (and optionally
  `BLOOM_STORAGE_LOCAL_ROOT`), describe the on-disk layout keyed by storage key, and warn not
  to mix backends for one experiment (no cross-store view; version ids can collide)

#### Scenario: Signed-URL download and its env vars are documented

- **WHEN** a developer reads the storage docs after this change
- **THEN** they learn that every consumer-tool result carries an `output_links` entry per
  output (URL, `sha256`, `size_bytes`); that `BLOOM_STORAGE_URL` configures the local backend's
  served-URL base and requires a separately-run HTTP server for that root; that
  `BLOOM_PUBLIC_SUPABASE_URL` rewrites a Supabase signed URL off the internal Docker host for
  prod/staging; and the code constant naming the signed-URL expiry (rather than a bare number
  restated in prose)

#### Scenario: The inline-vs-link threshold is documented as guidance, not enforced behavior

- **WHEN** a developer reads the storage docs' description of the inline-vs-link size threshold
- **THEN** the documented number is explicitly labeled as documentation-only guidance for a
  caller applying it themselves, not a behavior any bloommcp tool implements or enforces

#### Scenario: Local-mode docs describe the data-locality guarantee, not a zero-network-activity claim

- **WHEN** a developer reads any of the local-backend guarantee statements in
  `connecting-claude-code.md`, `storage-backends.md`, or `_WIKI/BLOOMMCP/README.md`
- **THEN** the guarantee is stated as "no experiment data leaves your machine" (or equivalent),
  not as "no connection to the shared server at all," "nothing reaches the network," "fully
  offline" used as a standalone guarantee, or other absolute no-network-activity phrasing
- **AND** the same phrasing is used consistently across all three documents

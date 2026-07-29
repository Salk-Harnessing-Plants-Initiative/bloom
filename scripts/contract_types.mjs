// Contract types drift guard for the pinned sleap-roots-contracts schema
// (pin-sleap-roots-contract / #294).
//
// Pure functions (no file I/O, no process.exit) + a thin CLI shim. The shim is
// the ONLY place that reads files and exits, so scripts/contract_types.test.mjs
// can import and drive the pure functions with in-memory inputs.
//
//   node scripts/contract_types.mjs --write   # regenerate contracts/generated/
//   node scripts/contract_types.mjs --check    # fail (exit 1) on any drift
//
// json-schema-to-typescript's compile() is async, so generateTypes/checkDrift
// are async. The codegen never emits the schema $id, so a re-pin that only
// re-stamps $id regenerates byte-identical TS (a structural no-op); a real
// field change produces a different file and fails the guard.

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import json2ts from 'json-schema-to-typescript'

const { compile } = json2ts

// Version-free banner: a re-pin that only re-stamps $id must regenerate this
// file unchanged, so nothing version-specific may appear in the output.
const BANNER = [
  '/* eslint-disable */',
  '/**',
  ' * AUTO-GENERATED — DO NOT EDIT.',
  ' *',
  ' * TypeScript types for the sleap-roots-contracts ResultEnvelope, generated from',
  ' * contracts/schema/result_envelope.schema.json (the pinned JSON Schema) by',
  ' * json-schema-to-typescript. Regenerate with `npm run contracts:gen`; the',
  ' * byte-for-byte drift guard `npm run contracts:check` fails CI if this file and',
  ' * the pinned schema disagree.',
  ' */',
].join('\n')

// `$refOptions.resolve.http: false` hard-fails (rather than fetching) if a future
// re-pinned schema ever carried a remote http(s) `$ref` — codegen stays offline and
// deterministic. The current schema uses only intra-document `#/$defs/...` refs.
const JSON2TS_OPTIONS = {
  bannerComment: BANNER,
  format: true,
  $refOptions: { resolve: { http: false } },
}

// Anchored on the version-stamped path segment, e.g.
// https://…/schema/v0.1.0a1/result_envelope.schema.json -> "v0.1.0a1".
const ID_VERSION_RE = /\/schema\/(v[^/]+)\/result_envelope\.schema\.json$/

const normalizeEol = (s) => s.replace(/\r\n/g, '\n')

/** Generate the contract TS from a parsed JSON Schema object. Async: compile() returns a Promise. */
export async function generateTypes(schema) {
  return normalizeEol(await compile(schema, 'ResultEnvelope', JSON2TS_OPTIONS))
}

/** Assert the pin manifest agrees with the schema's $id (exact id + parsed version). */
export function checkPinConsistency(pin, schema) {
  const id = schema && schema.$id
  if (typeof id !== 'string' || id.length === 0) {
    return { ok: false, error: 'schema $id is missing' }
  }
  if (!pin || pin.id !== id) {
    return { ok: false, error: `pin.json id (${pin && pin.id}) != schema $id (${id})` }
  }
  const m = ID_VERSION_RE.exec(id)
  if (!m) {
    return { ok: false, error: `schema $id has no parseable version segment: ${id}` }
  }
  if (pin.version !== m[1]) {
    return { ok: false, error: `pin.json version (${pin.version}) != $id version (${m[1]})` }
  }
  return { ok: true }
}

/** Regenerate from the schema and compare (EOL-normalized) to the committed types. */
export async function checkDrift({ schema, pin, committedTs }) {
  const pinResult = checkPinConsistency(pin, schema)
  if (!pinResult.ok) return { ok: false, diff: pinResult.error }
  const generated = await generateTypes(schema)
  const committed = normalizeEol(committedTs)
  if (generated === committed) return { ok: true }
  const g = generated.split('\n')
  const c = committed.split('\n')
  let i = 0
  while (i < g.length && i < c.length && g[i] === c[i]) i++
  const diff =
    `committed contract types differ from regenerating the pinned schema ` +
    `(first difference at line ${i + 1}):\n` +
    `  committed:   ${JSON.stringify(c[i] ?? '<EOF>')}\n` +
    `  regenerated: ${JSON.stringify(g[i] ?? '<EOF>')}\n` +
    'Run `npm run contracts:gen` and commit, or revert the unintended change.'
  return { ok: false, diff }
}

// ---- CLI shim: the only place with file I/O and process.exit ----

function isMain() {
  return Boolean(process.argv[1]) && import.meta.url === pathToFileURL(process.argv[1]).href
}

if (isMain()) {
  const root = join(dirname(fileURLToPath(import.meta.url)), '..')
  const schemaPath = join(root, 'contracts', 'schema', 'result_envelope.schema.json')
  const pinPath = join(root, 'contracts', 'pin.json')
  const generatedPath = join(root, 'contracts', 'generated', 'result-envelope.ts')
  const mode = process.argv[2]

  const readText = (p, label) => {
    try {
      return readFileSync(p, 'utf8')
    } catch (err) {
      console.error(`error: cannot read ${label} at ${p}: ${err.message}`)
      process.exit(1)
    }
  }
  const readJson = (p, label) => {
    const text = readText(p, label)
    try {
      return JSON.parse(text)
    } catch (err) {
      console.error(`error: cannot parse ${label} at ${p}: ${err.message}`)
      process.exit(1)
    }
  }

  if (mode === '--write') {
    const schema = readJson(schemaPath, 'schema')
    const pin = readJson(pinPath, 'pin.json')
    const pinResult = checkPinConsistency(pin, schema)
    if (!pinResult.ok) {
      console.error(`pin-consistency failed: ${pinResult.error}`)
      process.exit(1)
    }
    writeFileSync(generatedPath, await generateTypes(schema), 'utf8')
    console.log(`wrote ${generatedPath}`)
    process.exit(0)
  } else if (mode === '--check') {
    const schema = readJson(schemaPath, 'schema')
    const pin = readJson(pinPath, 'pin.json')
    const committedTs = readText(generatedPath, 'generated types')
    const pinResult = checkPinConsistency(pin, schema)
    if (!pinResult.ok) {
      console.error(`pin-consistency failed: ${pinResult.error}`)
      process.exit(1)
    }
    const drift = await checkDrift({ schema, pin, committedTs })
    if (!drift.ok) {
      console.error(drift.diff)
      process.exit(1)
    }
    console.log('contract types OK: pinned schema and committed types agree.')
    process.exit(0)
  } else {
    console.error('usage: node scripts/contract_types.mjs --write|--check')
    process.exit(2)
  }
}

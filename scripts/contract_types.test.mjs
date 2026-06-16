// Negative-path + $id-no-op tests for the contract drift guard
// (pin-sleap-roots-contract / #294). Built-in `node --test` runner — no config.
//
//   node --test scripts/contract_types.test.mjs
//
// Imports the PURE functions from contract_types.mjs (the CLI shim never runs on
// import) and drives them with in-memory mutations of the real vendored schema.
// The POSITIVE paths (everything agrees) are covered by the live `npm run
// contracts:check` against the committed file; this file carries the cases that
// can't be exercised by the real, always-consistent artifacts.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { generateTypes, checkPinConsistency, checkDrift } from './contract_types.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const schema = JSON.parse(
  readFileSync(join(root, 'contracts/schema/result_envelope.schema.json'), 'utf8')
)
const pin = JSON.parse(readFileSync(join(root, 'contracts/pin.json'), 'utf8'))
const clone = (o) => JSON.parse(JSON.stringify(o))

test('pin-consistency: matching pin and schema is ok', () => {
  assert.equal(checkPinConsistency(pin, schema).ok, true)
})

test('pin-consistency: version mismatch is rejected', () => {
  assert.equal(checkPinConsistency({ ...pin, version: 'v9.9.9' }, schema).ok, false)
})

test('pin-consistency: id mismatch is rejected', () => {
  assert.equal(
    checkPinConsistency({ ...pin, id: 'https://example.com/wrong.json' }, schema).ok,
    false
  )
})

test('pin-consistency: missing $id is rejected', () => {
  const noId = clone(schema)
  delete noId.$id
  assert.equal(checkPinConsistency(pin, noId).ok, false)
})

test('pin-consistency: unparseable $id (no version segment) is rejected', () => {
  const weird = clone(schema)
  weird.$id = 'https://example.com/no-version-here.json'
  // Matching pin.id exactly, so only the version-parse path can reject it.
  assert.equal(checkPinConsistency({ ...pin, id: weird.$id }, weird).ok, false)
})

test('drift: a hand-edited committed file is detected (checkDrift fail branch)', async () => {
  const good = await generateTypes(schema)
  const tampered = good.replace('export interface ResultEnvelope', 'export interface Tampered')
  const res = await checkDrift({ schema, pin, committedTs: tampered })
  assert.equal(res.ok, false)
  assert.match(res.diff, /differ/)
})

test('drift: identical committed types pass', async () => {
  const good = await generateTypes(schema)
  assert.equal((await checkDrift({ schema, pin, committedTs: good })).ok, true)
})

test('$id-only re-pin regenerates byte-identical types (structural no-op)', async () => {
  const before = await generateTypes(schema)
  const restamped = clone(schema)
  restamped.$id = restamped.$id.replace('/v0.1.0a1/', '/v0.1.0/')
  assert.equal(await generateTypes(restamped), before)
})

test('a real field change produces different types (guard would fail)', async () => {
  const before = await generateTypes(schema)
  const mutated = clone(schema)
  // Flip idempotency_key from string to integer — a genuine contract change.
  mutated.$defs.Provenance.properties.idempotency_key = {
    type: 'integer',
    default: 0,
    title: 'Idempotency Key',
  }
  assert.notEqual(await generateTypes(mutated), before)
})

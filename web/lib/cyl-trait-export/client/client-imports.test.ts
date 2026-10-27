/**
 * Static guard for design D8: the dialog's browser code takes values from
 * `lib/cyl-trait-export/` only through `stem.ts`, `limits.ts` and `client/`. The rest
 * of that directory runs on the server (`jobs.ts` and `selection.ts` import
 * `node:crypto`; `request.ts` imports `next/server`), so `import type` is the only way
 * to name it.
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'

import { describe, expect, it } from 'vitest'

const WEB = resolve(__dirname, '../../..')
const LIB = join(WEB, 'lib', 'cyl-trait-export')
const ALLOWED = new Set(['stem', 'limits'])

/** The browser files the guard covers: helpers, components and the scan page's button. */
function browserFiles(): string[] {
  const dirs = [join(LIB, 'client'), join(WEB, 'components', 'cyl-trait-export')]
  const files = dirs
    .filter((d) => existsSync(d))
    .flatMap((d) => readdirSync(d).map((f) => join(d, f)))
    .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
  const scanButton = join(
    WEB,
    'app',
    'app',
    'phenotypes',
    '[speciesId]',
    '[experimentId]',
    '[waveId]',
    '[accessionId]',
    '[scanId]',
    'ScanTraitExportButton.tsx'
  )
  if (existsSync(scanButton)) files.push(scanButton)
  return files
}

/** Specifiers imported for their values: everything except `import type` and all-type braces. */
function valueImports(source: string): string[] {
  const out: string[] = []
  const stmt = /(?:^|\n)\s*(import|export)\s+(type\s+)?([^'";]*?)\s*from\s*['"]([^'"]+)['"]/g
  for (const m of source.matchAll(stmt)) {
    if (m[2]) continue
    const braces = /^\{([^}]*)\}$/.exec(m[3].trim())
    if (braces) {
      const names = braces[1]
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean)
      if (names.length > 0 && names.every((n) => n.startsWith('type '))) continue
    }
    out.push(m[4])
  }
  for (const m of source.matchAll(/(?:^|\n)\s*import\s*['"]([^'"]+)['"]/g)) out.push(m[1])
  for (const m of source.matchAll(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g)) out.push(m[1])
  return out
}

/** A specifier's target under `lib/cyl-trait-export/`, relative to it, or null. */
function libTarget(file: string, spec: string): string | null {
  let abs: string
  if (spec.startsWith('@/')) abs = join(WEB, spec.slice(2))
  else if (spec.startsWith('.')) abs = resolve(dirname(file), spec)
  else return null
  const rel = relative(LIB, abs)
  if (rel.startsWith('..') || rel === '') return null
  return rel.split(sep).join('/')
}

function violations(file: string, source: string): string[] {
  return valueImports(source).filter((spec) => {
    const target = libTarget(file, spec)
    if (target === null) return false
    return !(ALLOWED.has(target) || target.startsWith('client/'))
  })
}

describe('trait export browser imports (characterization)', () => {
  it('finds the helper files it guards', () => {
    expect(browserFiles().some((f) => f.endsWith(`client${sep}poll.ts`))).toBe(true)
  })

  it('value-imports nothing from lib/cyl-trait-export but stem, limits and client/', () => {
    const found = browserFiles().flatMap((f) =>
      violations(f, readFileSync(f, 'utf-8')).map((spec) => `${relative(WEB, f)}: ${spec}`)
    )
    expect(found).toEqual([])
  })

  it('tells a value import from a type import', () => {
    const f = join(LIB, 'client', 'x.ts')
    const src = [
      "import type { JobView } from '../jobs'",
      "import { type RecipeRow } from '../recipes'",
      "import { keySegment } from '../stem'",
      "import { BATCH_SCANS } from '@/lib/cyl-trait-export/limits'",
      "import { pollDelay } from './poll'",
      "import { reserveJob } from '../jobs'",
      "import { type RecipeRow, mergeRecipeListings } from '@/lib/cyl-trait-export/recipes'",
      "export { parseSelection } from '../request'",
      "import '../state'",
    ].join('\n')
    expect(violations(f, src)).toEqual([
      '../jobs',
      '@/lib/cyl-trait-export/recipes',
      '../request',
      '../state',
    ])
  })
})

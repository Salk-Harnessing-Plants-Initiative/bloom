/** The export stem (design D6; spec "Legacy stem and source ids"). */

import { describe, expect, it } from 'vitest'

import { buildStem, keySegment, slugify } from './stem'

const K = '1bad3d73baf3961fad971247006876e3ae551c74ca494ae1039ee267d09e25fa'
const AT = new Date('2026-10-02T12:00:00.000Z')

describe('buildStem', () => {
  it('matches the spec example for a filtered legacy export', () => {
    expect(
      buildStem({
        experimentName: 'Diversity Screen',
        experimentId: 1,
        selection: { experiment: 1, wave: 3, age: 0 },
        recipeKey: 'legacy:12345',
        generatedAt: AT,
      })
    ).toBe('diversity-screen_wave3_day0_legacy-12345_20261002')
  })

  it('uses the first 8 characters of a pipeline key, and no segments for a whole experiment', () => {
    expect(
      buildStem({
        experimentName: 'Fixture Diversity Screen',
        experimentId: 1,
        selection: { experiment: 1 },
        recipeKey: K,
        generatedAt: AT,
      })
    ).toBe('fixture-diversity-screen_1bad3d73_20261002')
  })

  it('names a scan export by its scan', () => {
    expect(
      buildStem({
        experimentName: 'Fixture Diversity Screen',
        experimentId: 1,
        selection: { scan: 100 },
        recipeKey: 'unattributed',
        generatedAt: AT,
      })
    ).toBe('fixture-diversity-screen_scan100_unattributed_20261002')
  })

  it('takes the UTC date, not the local one', () => {
    const lateEvening = new Date('2026-10-01T23:30:00-08:00')
    expect(
      buildStem({
        experimentName: 'X',
        experimentId: 1,
        selection: { experiment: 1 },
        recipeKey: K,
        generatedAt: lateEvening,
      }).endsWith('_20261002')
    ).toBe(true)
  })

  it('always yields a header-safe name', () => {
    for (const name of ['Ångström & Co. / 2026', '"; rm -rf', '  ', 'Émile—Zola']) {
      expect(
        buildStem({
          experimentName: name,
          experimentId: 7,
          selection: { experiment: 7, wave: 0 },
          recipeKey: 'legacy:3',
          generatedAt: AT,
        })
      ).toMatch(/^[a-z0-9_-]+$/)
    }
  })
})

describe('slugify', () => {
  it.each([
    ['Diversity Screen', 1, 'diversity-screen'],
    ['FN2023_Round3', 1, 'fn2023-round3'],
    ['---!!!---', 42, 'experiment-42'],
    ['', 5, 'experiment-5'],
  ])('slugify(%j)', (name, id, out) => {
    expect(slugify(name, id)).toBe(out)
  })

  it('cuts to 60 characters and leaves no trailing hyphen', () => {
    const slug = slugify('a'.repeat(59) + ' ' + 'b'.repeat(140), 1)
    expect(slug.length).toBeLessThanOrEqual(60)
    expect(slug).toBe('a'.repeat(59))
  })
})

describe('keySegment', () => {
  it.each([
    [K, '1bad3d73'],
    ['legacy:9', 'legacy-9'],
    ['legacy:12345', 'legacy-12345'],
    ['unattributed', 'unattributed'],
  ])('keySegment(%s)', (key, out) => {
    expect(keySegment(key)).toBe(out)
  })
})

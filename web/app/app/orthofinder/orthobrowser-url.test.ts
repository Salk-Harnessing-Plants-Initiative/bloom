import { describe, expect, it } from 'vitest'

import { ORTHOBROWSER_URL, orthobrowserUrl, searchedGene } from './orthobrowser-url'

describe('orthobrowserUrl', () => {
  it('opens the OrthoBrowser on the gene, lowercased as its search keys are', () => {
    expect(orthobrowserUrl('MYB41')).toBe(`${ORTHOBROWSER_URL}?search=myb41`)
  })

  it("encodes the search so it can't add other parameters", () => {
    expect(orthobrowserUrl('a&select=x')).toBe(`${ORTHOBROWSER_URL}?search=a%26select%3Dx`)
  })

  it('uses the first value when ?search= is repeated', () => {
    expect(orthobrowserUrl(['myb41', 'pin1'])).toBe(`${ORTHOBROWSER_URL}?search=myb41`)
  })

  it.each([undefined, '', '   ', 'x'.repeat(201)])('opens the plain browser for %j', (search) => {
    expect(orthobrowserUrl(search)).toBe(ORTHOBROWSER_URL)
  })
})

describe('searchedGene', () => {
  it('keeps the gene as typed, for looking it up in Bloom', () => {
    expect(searchedGene(' AT1G01010 ')).toBe('AT1G01010')
    expect(searchedGene(['MYB41', 'pin1'])).toBe('MYB41')
  })

  it('is null for no search or one too long to be a gene', () => {
    expect(searchedGene(undefined)).toBeNull()
    expect(searchedGene('  ')).toBeNull()
    expect(searchedGene('x'.repeat(201))).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'

import { ORTHOBROWSER_URL, orthobrowserUrl } from './orthobrowser-url'

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

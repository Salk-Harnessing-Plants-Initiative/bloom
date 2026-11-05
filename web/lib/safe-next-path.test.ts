/** Unit tests for the post-login return path check. */

import { describe, it, expect } from 'vitest'
import { safeNextPath } from './safe-next-path'

describe('safeNextPath', () => {
  it('accepts a same-site absolute path', () => {
    expect(safeNextPath('/app/experiments?id=abc')).toBe('/app/experiments?id=abc')
  })

  it('rejects a protocol-relative URL', () => {
    expect(safeNextPath('//evil.example.com')).toBeNull()
  })

  it('rejects an absolute URL', () => {
    expect(safeNextPath('https://evil.example.com/x')).toBeNull()
  })

  it('rejects a backslash-obfuscated path', () => {
    expect(safeNextPath('/\\evil.example.com')).toBeNull()
    expect(safeNextPath('/\\\\evil.example.com')).toBeNull()
  })

  it('rejects a tab hidden between the two slashes of a protocol-relative URL', () => {
    // The bypass the //-prefix + backslash-only checks missed: a tab is
    // invisible to a plain string comparison, but the WHATWG URL parser
    // strips ASCII tab/CR/LF as its first step, so every browser normalizes
    // this to //evil.example.com before navigating.
    expect(safeNextPath('/\t/evil.example.com')).toBeNull()
  })

  it('rejects a newline or carriage return hidden the same way', () => {
    expect(safeNextPath('/\n/evil.example.com')).toBeNull()
    expect(safeNextPath('/\r/evil.example.com')).toBeNull()
    expect(safeNextPath('/\r\n/evil.example.com')).toBeNull()
  })

  it('rejects a javascript: URI', () => {
    expect(safeNextPath('javascript:alert(1)')).toBeNull()
  })

  it('rejects empty and missing values', () => {
    expect(safeNextPath('')).toBeNull()
    expect(safeNextPath(null)).toBeNull()
    expect(safeNextPath(undefined)).toBeNull()
  })

  it('returns the resolved path unchanged for an ordinary query string and hash', () => {
    expect(safeNextPath('/app/experiments?id=abc#section')).toBe('/app/experiments?id=abc#section')
  })
})

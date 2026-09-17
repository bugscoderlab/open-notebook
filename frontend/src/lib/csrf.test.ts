import { describe, it, expect } from 'vitest'
import { CSRF_COOKIE_NAME, CSRF_HEADER_NAME, getCsrfToken, readCookie } from './csrf'

describe('readCookie', () => {
  it('returns the value for an exact name match', () => {
    expect(readCookie('a=1; open_notebook_csrf=tok; b=2', 'open_notebook_csrf')).toBe('tok')
  })

  it('returns null when the cookie is absent', () => {
    expect(readCookie('a=1; b=2', 'open_notebook_csrf')).toBeNull()
  })

  it('returns null for an empty cookie string', () => {
    expect(readCookie('', 'open_notebook_csrf')).toBeNull()
  })

  it('does not match a suffix of a longer cookie name', () => {
    expect(readCookie('xsrf=evil; other=1', 'csrf')).toBeNull()
  })

  it('handles a cookie appearing as the first pair', () => {
    expect(readCookie('open_notebook_csrf=abc; x=1', 'open_notebook_csrf')).toBe('abc')
  })
})

describe('getCsrfToken', () => {
  it('reads the open_notebook_csrf cookie from an explicit string', () => {
    expect(getCsrfToken(`session=secret; ${CSRF_COOKIE_NAME}=tok`)).toBe('tok')
  })

  it('returns null when the CSRF cookie is missing', () => {
    expect(getCsrfToken('session=secret')).toBeNull()
  })

  it('exposes the header name the backend expects', () => {
    expect(CSRF_HEADER_NAME).toBe('x-csrf-token')
  })
})

// CSRF double-submit plumbing (ADR-010).
//
// The backend sets a readable `open_notebook_csrf` cookie at login; mutating
// requests must echo its value back in the `x-csrf-token` header so the
// server can compare cookie vs header. The cookie is deliberately NOT
// HttpOnly — reading it here is the point.

export const CSRF_COOKIE_NAME = 'open_notebook_csrf'
export const CSRF_HEADER_NAME = 'x-csrf-token'

/** Read one cookie by exact name from a `document.cookie`-style string. */
export function readCookie(cookieString: string, name: string): string | null {
  if (!cookieString) {
    return null
  }
  for (const pair of cookieString.split(';')) {
    const [rawKey, ...rest] = pair.trim().split('=')
    if (rawKey === name && rest.length > 0) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return null
}

/** The CSRF token to echo on mutating requests, or null before login. */
export function getCsrfToken(
  cookieString: string | undefined = typeof document !== 'undefined'
    ? document.cookie
    : '',
): string | null {
  return readCookie(cookieString, CSRF_COOKIE_NAME)
}

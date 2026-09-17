import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AxiosRequestConfig } from 'axios'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
}))

import { getApiUrl } from '@/lib/config'
import {
  apiClient,
  isApiSameOrigin,
  resolveApiBaseUrl,
  setUnauthorizedHandler,
} from './client'

describe('isApiSameOrigin', () => {
  it('is true for same hostname regardless of port', () => {
    expect(isApiSameOrigin('http://localhost:5055', 'http://localhost:3000')).toBe(true)
  })

  it('is false for different hostnames', () => {
    expect(isApiSameOrigin('https://api.example.com', 'https://notebook.example.com')).toBe(false)
  })

  it('is false when either side is missing or unparseable', () => {
    expect(isApiSameOrigin('', 'http://localhost:3000')).toBe(false)
    expect(isApiSameOrigin('http://localhost:5055', undefined)).toBe(false)
    expect(isApiSameOrigin('not a url', 'http://localhost:3000')).toBe(false)
    expect(isApiSameOrigin('http://localhost:5055', 'not a url')).toBe(false)
  })
})

describe('resolveApiBaseUrl (cookie-session routing)', () => {
  it('routes same-host APIs through the same-origin proxy (dev :3000 → :5055)', () => {
    // The API answers on the same hostname as the page, different port.
    // Credentialed cross-origin requests fail under wildcard CORS, so the
    // browser must use the Next.js rewrites proxy instead.
    expect(resolveApiBaseUrl('http://localhost:5055', 'http://localhost:3000')).toBe('/api')
  })

  it('treats same host + same port as same-origin too', () => {
    expect(resolveApiBaseUrl('http://localhost:3000', 'http://localhost:3000')).toBe('/api')
  })

  it('matches on hostname, not port — Docker single-container via IP', () => {
    expect(resolveApiBaseUrl('http://192.168.38.43:5055', 'http://192.168.38.43:3000')).toBe('/api')
  })

  it('keeps the absolute URL for a genuinely cross-host API', () => {
    expect(resolveApiBaseUrl('https://api.example.com', 'https://notebook.example.com')).toBe(
      'https://api.example.com/api',
    )
  })

  it('uses the relative proxy when no API URL is configured', () => {
    expect(resolveApiBaseUrl('', 'http://localhost:3000')).toBe('/api')
  })

  it('falls back to absolute for unparseable API URLs', () => {
    expect(resolveApiBaseUrl('not a url', 'http://localhost:3000')).toBe('not a url/api')
  })

  it('falls back to absolute when no page origin is available (SSR)', () => {
    expect(resolveApiBaseUrl('http://localhost:5055', undefined)).toBe('http://localhost:5055/api')
  })

  it('falls back to absolute when the page origin is unparseable', () => {
    expect(resolveApiBaseUrl('http://localhost:5055', 'not a url')).toBe('http://localhost:5055/api')
  })
})

/** Run one request with a stubbed adapter, returning the config it saw. */
async function captureRequest(config: AxiosRequestConfig): Promise<AxiosRequestConfig> {
  const seen: AxiosRequestConfig[] = []
  apiClient.defaults.adapter = vi.fn(async (cfg) => {
    seen.push(cfg)
    return {
      data: {},
      status: 200,
      statusText: 'OK',
      headers: {},
      config: cfg,
    }
  })
  await apiClient.request(config)
  return seen[0]!
}

const originalAdapter = apiClient.defaults.adapter

// jsdom keeps cookies across tests unless expired explicitly.
function clearCookies() {
  for (const name of ['open_notebook_csrf', 'session']) {
    document.cookie = `${name}=; Max-Age=0`
  }
}

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter
  clearCookies()
})

describe('apiClient cookie-session plumbing', () => {
  it('sends credentials so the session cookie travels with requests', async () => {
    expect(apiClient.defaults.withCredentials).toBe(true)
  })

  it('interceptor: same-host API URLs resolve to the same-origin proxy base', async () => {
    vi.mocked(getApiUrl).mockResolvedValueOnce('http://localhost:5055')
    const sent = await captureRequest({ method: 'GET', url: '/auth/status' })
    expect(sent.baseURL).toBe('/api') // jsdom page origin is http://localhost:3000
  })

  it('interceptor: cross-host API URLs keep the absolute base', async () => {
    vi.mocked(getApiUrl).mockResolvedValueOnce('http://api.test')
    const sent = await captureRequest({ method: 'GET', url: '/auth/status' })
    expect(sent.baseURL).toBe('http://api.test/api')
  })

  it('never sets an Authorization header', async () => {
    document.cookie = 'open_notebook_csrf=tok'
    const sent = await captureRequest({ method: 'GET', url: '/notebooks' })
    expect(sent.headers?.Authorization).toBeUndefined()
  })

  it('attaches the CSRF header on mutations when the cookie is present', async () => {
    document.cookie = 'session=opaque'
    document.cookie = 'open_notebook_csrf=tok'
    const sent = await captureRequest({ method: 'POST', url: '/auth/logout', data: null })
    expect(sent.headers?.['x-csrf-token']).toBe('tok')
  })

  it('does not attach the CSRF header on reads', async () => {
    document.cookie = 'open_notebook_csrf=tok'
    const sent = await captureRequest({ method: 'GET', url: '/auth/me' })
    expect(sent.headers?.['x-csrf-token']).toBeUndefined()
  })

  it('omits the CSRF header when the cookie is absent', async () => {
    const sent = await captureRequest({ method: 'POST', url: '/notebooks' })
    expect(sent.headers?.['x-csrf-token']).toBeUndefined()
  })

  it('PUT, PATCH and DELETE count as mutations', async () => {
    document.cookie = 'open_notebook_csrf=tok'
    for (const method of ['put', 'patch', 'delete'] as const) {
      const sent = await captureRequest({ method, url: '/x' })
      expect(sent.headers?.['x-csrf-token']).toBe('tok')
    }
  })

  it('a 401 notifies the unauthorized handler and still rejects', async () => {
    // Custom axios adapters must reject non-2xx themselves (axios core does
    // not re-validate adapter responses).
    apiClient.defaults.adapter = vi.fn(async (cfg) => {
      const error = new (await import('axios')).AxiosError(
        'Request failed with status code 401',
        '401',
        cfg,
        undefined,
        { status: 401, statusText: 'Unauthorized', headers: {}, config: cfg, data: {} },
      )
      throw error
    })
    const handler = vi.fn()
    setUnauthorizedHandler(handler)

    await expect(apiClient.get('/auth/me')).rejects.toMatchObject({
      response: { status: 401 },
    })
    expect(handler).toHaveBeenCalledTimes(1)
    setUnauthorizedHandler(null)
  })
})

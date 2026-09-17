import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AxiosRequestConfig } from 'axios'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://api.test'),
}))

import { apiClient, setUnauthorizedHandler } from './client'

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

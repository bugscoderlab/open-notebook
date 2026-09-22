import { describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiClientError } from '../src/api-client.js'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('ApiClient', () => {
  it('sends the internal token and returns message results', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(200, { reply: 'answer', suggestions: ['next?'], conversation_reset: false }),
    )
    const client = new ApiClient('http://api:5055', 'tok', fetchMock as unknown as typeof fetch)

    const result = await client.message('telegram', '42', 'hello')

    expect(result.reply).toBe('answer')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api:5055/api/integrations/message')
    expect((init.headers as Record<string, string>).Authorization).toBe('Internal tok')
    expect(JSON.parse(init.body as string)).toEqual({
      platform: 'telegram',
      external_id: '42',
      text: 'hello',
    })
  })

  it('status passes on ok and throws on bad token', async () => {
    const ok = new ApiClient('http://api:5055', 'tok', vi.fn().mockResolvedValue(jsonResponse(200, { ok: true })) as unknown as typeof fetch)
    await expect(ok.checkStatus()).resolves.toBeUndefined()

    const bad = new ApiClient('http://api:5055', 'tok', vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'Invalid internal token' })) as unknown as typeof fetch)
    await expect(bad.checkStatus()).rejects.toMatchObject({
      status: 401,
      message: 'Invalid internal token',
    })
  })

  it('surfaces the API detail on message errors', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(404, { detail: 'No integration link found' }))
    const client = new ApiClient('http://api:5055', 'tok', fetchMock as unknown as typeof fetch)

    const error = await client.message('telegram', '42', 'hi').catch((e) => e as ApiClientError)
    expect(error.status).toBe(404)
    expect(error.message).toBe('No integration link found')
  })

  it('claim returns the linked email', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { success: true, message: 'Linked', email: 'a@b.c' }))
    const client = new ApiClient('http://api:5055', 'tok', fetchMock as unknown as typeof fetch)

    const result = await client.claim('telegram', '42', '123456')
    expect(result.email).toBe('a@b.c')
  })

  it('pushWhatsappPairing PUTs the status for the web UI', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: 'pairing', qr: 'qr-1', updated_at: null }))
    const client = new ApiClient('http://api:5055', 'tok', fetchMock as unknown as typeof fetch)

    await client.pushWhatsappPairing('pairing', 'qr-1')

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('http://api:5055/api/integrations/whatsapp/pairing')
    expect(init.method).toBe('PUT')
    expect(JSON.parse(init.body as string)).toEqual({ status: 'pairing', qr: 'qr-1' })
  })

  it('pushWhatsappPairing includes the identity on connect', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { status: 'connected' }))
    const client = new ApiClient('http://api:5055', 'tok', fetchMock as unknown as typeof fetch)

    await client.pushWhatsappPairing('connected', undefined, '6012@s.whatsapp.net')

    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(JSON.parse(init.body as string)).toEqual({
      status: 'connected',
      identity: '6012@s.whatsapp.net',
    })
  })
})

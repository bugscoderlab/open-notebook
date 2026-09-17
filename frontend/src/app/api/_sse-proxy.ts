import type { NextRequest } from 'next/server'

const INTERNAL_API_URL = process.env.INTERNAL_API_URL || 'http://localhost:5055'

export async function sseProxy(req: NextRequest, upstreamPath: string) {
  const body = await req.text()
  const auth = req.headers.get('authorization')
  const cookie = req.headers.get('cookie')
  const csrf = req.headers.get('x-csrf-token')

  const upstream = await fetch(`${INTERNAL_API_URL}${upstreamPath}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(auth ? { Authorization: auth } : {}),
      // Auth is a cookie session (ADR-010): without the session cookie the
      // backend's get_current_user rejects the call with 401.
      ...(cookie ? { Cookie: cookie } : {}),
      ...(csrf ? { 'x-csrf-token': csrf } : {}),
      Accept: 'text/event-stream',
    },
    body,
    cache: 'no-store',
  })

  if (!upstream.ok || !upstream.body) {
    return new Response(upstream.body, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
    })
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}

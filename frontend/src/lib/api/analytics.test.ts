import { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'
import { extractAnalyticsErrorDetail } from './analytics'

function axiosErrorWith(status: number, data: unknown): AxiosError {
  return new AxiosError(
    'Request failed',
    String(status),
    undefined,
    undefined,
    { status, statusText: '', headers: {}, config: {} as never, data } as never
  )
}

describe('extractAnalyticsErrorDetail', () => {
  it('returns the backend detail message from a 4xx refusal', () => {
    const error = axiosErrorWith(400, {
      detail:
        "That question needs a customer name, e.g. 'What is the average transaction value for Sarah Lim?'",
    })
    expect(extractAnalyticsErrorDetail(error)).toBe(
      "That question needs a customer name, e.g. 'What is the average transaction value for Sarah Lim?'"
    )
  })

  it('returns null for a 5xx even with a populated detail (generic fallback applies)', () => {
    const error = axiosErrorWith(500, { detail: 'Internal provisioning error' })
    expect(extractAnalyticsErrorDetail(error)).toBeNull()
  })

  it('returns null for a response without a detail field', () => {
    const error = axiosErrorWith(502, { message: 'upstream broke' })
    expect(extractAnalyticsErrorDetail(error)).toBeNull()
  })

  it('returns null for network errors (no response at all)', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK')
    expect(extractAnalyticsErrorDetail(error)).toBeNull()
  })

  it('returns null for non-axios errors', () => {
    expect(extractAnalyticsErrorDetail(new Error('boom'))).toBeNull()
    expect(extractAnalyticsErrorDetail('boom')).toBeNull()
    expect(extractAnalyticsErrorDetail(null)).toBeNull()
  })

  it('ignores non-string detail values', () => {
    const error = axiosErrorWith(400, { detail: { nested: 'object' } })
    expect(extractAnalyticsErrorDetail(error)).toBeNull()
  })
})

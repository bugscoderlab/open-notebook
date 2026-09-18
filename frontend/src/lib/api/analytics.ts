import { isAxiosError } from 'axios'
import apiClient from './client'
import {
  AnalyticsAnswer,
  AnalyticsAskRequest,
  AnalyticsDataset,
  AnalyticsQueryRecord,
} from '@/lib/types/analytics'

/**
 * User-facing message from a failed analytics ask, when the backend sent one.
 *
 * The API answers 4xx refusals with `{ "detail": "…" }` carrying actionable
 * guidance ("That question needs a customer name, e.g. …"); surfacing it
 * turns refusals into useful direction instead of a generic failure
 * (#21). Only 4xx responses are surfaced — 5xx detail strings are
 * operator-facing, not user guidance, and network errors carry no response
 * at all; both fall back to the generic message.
 */
export function extractAnalyticsErrorDetail(error: unknown): string | null {
  if (!isAxiosError(error)) return null
  const status = error.response?.status
  if (status === undefined || status >= 500) return null
  const detail = (error.response?.data as { detail?: unknown } | undefined)
    ?.detail
  return typeof detail === 'string' && detail.trim() ? detail : null
}

export const analyticsApi = {
  datasets: async () => {
    const response = await apiClient.get<AnalyticsDataset[]>('/analytics/datasets')
    return response.data
  },

  ask: async (data: AnalyticsAskRequest) => {
    // Denied answers carry only {status, answer_text} — axios still parses
    // them into the partial shape; the hook normalizes to AnalyticsAnswer.
    const response = await apiClient.post<Partial<AnalyticsAnswer>>(
      '/analytics/ask',
      data
    )
    return response.data as AnalyticsAnswer
  },

  query: async (queryId: string) => {
    const response = await apiClient.get<AnalyticsQueryRecord>(
      `/analytics/queries/${queryId}`
    )
    return response.data
  },
}

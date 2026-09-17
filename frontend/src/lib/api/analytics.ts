import apiClient from './client'
import {
  AnalyticsAnswer,
  AnalyticsAskRequest,
  AnalyticsDataset,
  AnalyticsQueryRecord,
} from '@/lib/types/analytics'

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

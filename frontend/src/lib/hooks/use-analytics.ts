import { useMutation, useQuery } from '@tanstack/react-query'
import { analyticsApi } from '@/lib/api/analytics'
import { AnalyticsAskRequest } from '@/lib/types/analytics'

/**
 * Analytics mode data layer (issue #10/T9). Backend-enforced permissions:
 * the datasets list only contains datasets the caller may query, and asking
 * about anything else returns the zero-leakage denied shape.
 */

export const ANALYTICS_QUERY_KEYS = {
  datasets: ['analytics', 'datasets'] as const,
}

export function useAnalyticsDatasets() {
  return useQuery({
    queryKey: ANALYTICS_QUERY_KEYS.datasets,
    queryFn: () => analyticsApi.datasets(),
    retry: false,
  })
}

export function useAnalyticsAsk() {
  return useMutation({
    mutationFn: (data: AnalyticsAskRequest) => analyticsApi.ask(data),
    // No automatic retry (repo rule) and no toast: denied/no_data are
    // legitimate answer states rendered in the answer surface.
    retry: false,
  })
}

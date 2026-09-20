import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'

export type IntegrationPlatform = 'telegram' | 'whatsapp'

export interface IntegrationLink {
  id: string
  platform: IntegrationPlatform
  identity: string
  linked_at: string
}

export interface LinkingCode {
  platform: IntegrationPlatform
  code: string
  expires_at: string
}

export function useIntegrationLinks() {
  return useQuery({
    queryKey: QUERY_KEYS.integrationLinks,
    queryFn: async () => (await apiClient.get<IntegrationLink[]>('/integrations/links')).data,
  })
}

export function useCreateIntegrationLink() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: async (platform: IntegrationPlatform) =>
      (await apiClient.post<LinkingCode>('/integrations/link', { platform })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.integrationLinks })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), 'chatIntegrations.createFailed'),
        variant: 'destructive',
      })
    },
  })
}

export function useDeleteIntegrationLink() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: async (linkId: string) =>
      (await apiClient.delete(`/integrations/links/${linkId}`)).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.integrationLinks })
      toast({
        title: t('common.success'),
        description: t('chatIntegrations.unlinkSuccess'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

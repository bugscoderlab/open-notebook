import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { apiClient } from '@/lib/api/client'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'

export type IntegrationPlatform = 'telegram' | 'whatsapp'

export type WhatsappPairingStatus = 'pairing' | 'connected' | 'disconnected' | 'logged_out'

export interface WhatsappPairing {
  status: WhatsappPairingStatus
  qr: string | null
  identity: string | null
  updated_at: string | null
}

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

/**
 * Latest WhatsApp pairing state pushed by the gateway (QR + connection).
 * Polls every 3s while `enabled` — the QR rotates until scanned or times out.
 */
export function useWhatsAppPairing(enabled: boolean) {
  return useQuery({
    queryKey: QUERY_KEYS.whatsappPairing,
    queryFn: async () => (await apiClient.get<WhatsappPairing>('/integrations/whatsapp/pairing')).data,
    enabled,
    refetchInterval: enabled ? 3000 : false,
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

/**
 * One-click link of the WhatsApp identity the gateway is connected as.
 * Alternative to the /start chat flow for sole-number setups; the existing
 * links polling picks up the new link and closes the connect dialog.
 */
export function useClaimSelfWhatsApp() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: async (code: string) =>
      (await apiClient.post('/integrations/whatsapp/claim-self', { code })).data,
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

/**
 * Admin: ask the gateway to wipe its WhatsApp session and mint a fresh
 * pairing QR (one-click re-pair). The dialog's pairing poll picks up the
 * resulting QR state automatically.
 */
export function useRequestWhatsappRePair() {
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: async () =>
      (await apiClient.post('/integrations/whatsapp/reset')).data,
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorMessage(error, (key) => t(key), 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

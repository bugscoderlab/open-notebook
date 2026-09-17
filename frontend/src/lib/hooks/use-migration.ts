import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { migrationApi } from '@/lib/api/migration'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import {
  AssignMigrationItemRequest,
  MigrationItemKind,
} from '@/lib/types/migration'

/**
 * Content-classification migration data layer (issue #7/T6). Admin-only
 * backend surfaces (403 for everyone else); the role-aware nav hides the
 * page, these hooks power it.
 */

export const MIGRATION_QUERY_KEYS = {
  status: ['migration', 'status'] as const,
  items: ['migration', 'items'] as const,
}

function useInvalidateMigration() {
  const queryClient = useQueryClient()
  return () => {
    queryClient.invalidateQueries({ queryKey: MIGRATION_QUERY_KEYS.status })
    queryClient.invalidateQueries({ queryKey: MIGRATION_QUERY_KEYS.items })
  }
}

export function useMigrationStatus() {
  return useQuery({
    queryKey: MIGRATION_QUERY_KEYS.status,
    queryFn: () => migrationApi.status(),
  })
}

export function useMigrationItems() {
  return useQuery({
    queryKey: MIGRATION_QUERY_KEYS.items,
    queryFn: () => migrationApi.items(),
  })
}

export function useRunClassification() {
  const invalidate = useInvalidateMigration()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: () => migrationApi.run(),
    onSuccess: (summary) => {
      invalidate()
      toast({
        title: t('common.success'),
        description: t('migration.runSuccess', {
          sources: summary.classified_sources,
          notebooks: summary.classified_notebooks,
          flagged: summary.flagged_sources + summary.flagged_notebooks,
        }),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorKey(error, t('common.error')),
        variant: 'destructive',
      })
    },
  })
}

export function useAssignMigrationItem() {
  const invalidate = useInvalidateMigration()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      kind,
      id,
      ...data
    }: AssignMigrationItemRequest & { kind: MigrationItemKind; id: string }) =>
      migrationApi.assign(kind, id, data),
    onSuccess: (item) => {
      invalidate()
      toast({
        title: t('common.success'),
        description: t('migration.assignSuccess', { team: item.team_name }),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('common.error'),
        description: getApiErrorKey(error, t('common.error')),
        variant: 'destructive',
      })
    },
  })
}

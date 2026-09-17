import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { teamsApi, usersApi } from '@/lib/api/admin'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import { CreateTeamRequest, InviteUserRequest, UpdateTeamRequest, UpdateUserRequest } from '@/lib/types/admin'

/**
 * Team-access admin data layer (issue #5/T4). All queries are admin-only
 * backend surfaces (403 for everyone else); the role-aware nav hides the
 * pages, these hooks power them.
 */

export const ADMIN_QUERY_KEYS = {
  users: ['admin', 'users'] as const,
  teams: ['admin', 'teams'] as const,
}

export function useUsers() {
  return useQuery({
    queryKey: ADMIN_QUERY_KEYS.users,
    queryFn: () => usersApi.list(),
  })
}

export function useTeams() {
  return useQuery({
    queryKey: ADMIN_QUERY_KEYS.teams,
    queryFn: () => teamsApi.list(),
  })
}

export function useInviteUser() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (data: InviteUserRequest) => usersApi.invite(data),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: ADMIN_QUERY_KEYS.users })
      toast({
        title: t('common.success'),
        description: t('users.inviteSuccess', { name: user.display_name }),
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

export function useUpdateUser() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({ id, ...data }: UpdateUserRequest & { id: string }) =>
      usersApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_QUERY_KEYS.users })
      toast({
        title: t('common.success'),
        description: t('users.updateSuccess'),
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

export function useCreateTeam() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (data: CreateTeamRequest) => teamsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_QUERY_KEYS.teams })
      toast({
        title: t('common.success'),
        description: t('teams.createSuccess'),
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

export function useUpdateTeam() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({ id, ...data }: UpdateTeamRequest & { id: string }) =>
      teamsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_QUERY_KEYS.teams })
      toast({
        title: t('common.success'),
        description: t('teams.updateSuccess'),
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

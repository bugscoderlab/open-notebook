'use client'

import { useMemo, useState } from 'react'
import { useTeams, useUsers } from '@/lib/hooks/use-admin'

/**
 * Filtering state for the admin Users page (issue #5/T4): free-text search
 * across name/email plus team and status filters. Client-side — the list is
 * small (one row per user) and already fully loaded for the stats row.
 */
export function useAdminUsersPage() {
  const usersQuery = useUsers()
  const teamsQuery = useTeams()
  const [search, setSearch] = useState('')
  const [teamFilter, setTeamFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')

  const users = useMemo(() => usersQuery.data ?? [], [usersQuery.data])
  const teams = useMemo(() => teamsQuery.data ?? [], [teamsQuery.data])

  const teamSlugById = useMemo(() => {
    const map: Record<string, string> = {}
    for (const team of teams) {
      map[team.id] = team.slug
    }
    return map
  }, [teams])

  const filteredUsers = useMemo(() => {
    const term = search.trim().toLowerCase()
    return users.filter((user) => {
      if (teamFilter !== 'all' && user.team_id !== teamFilter) return false
      if (statusFilter !== 'all' && user.status !== statusFilter) return false
      if (
        term &&
        !user.display_name.toLowerCase().includes(term) &&
        !user.email.toLowerCase().includes(term)
      ) {
        return false
      }
      return true
    })
  }, [users, search, teamFilter, statusFilter])

  return {
    users,
    teams,
    filteredUsers,
    search,
    setSearch,
    teamFilter,
    setTeamFilter,
    statusFilter,
    setStatusFilter,
    teamSlug: (teamId: string) => teamSlugById[teamId] ?? '',
    isLoading: usersQuery.isLoading || teamsQuery.isLoading,
  }
}

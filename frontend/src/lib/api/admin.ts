import apiClient from './client'
import {
  AdminUser,
  CreateTeamRequest,
  InviteUserRequest,
  TeamSummary,
  UpdateTeamRequest,
  UpdateUserRequest,
} from '@/lib/types/admin'

export const usersApi = {
  list: async () => {
    const response = await apiClient.get<AdminUser[]>('/users')
    return response.data
  },

  invite: async (data: InviteUserRequest) => {
    const response = await apiClient.post<AdminUser>('/auth/invite', data)
    return response.data
  },

  update: async (id: string, data: UpdateUserRequest) => {
    const response = await apiClient.patch<AdminUser>(`/users/${id}`, data)
    return response.data
  },
}

export const teamsApi = {
  list: async () => {
    const response = await apiClient.get<TeamSummary[]>('/teams')
    return response.data
  },

  create: async (data: CreateTeamRequest) => {
    const response = await apiClient.post<TeamSummary>('/teams', data)
    return response.data
  },

  update: async (id: string, data: UpdateTeamRequest) => {
    const response = await apiClient.patch<TeamSummary>(`/teams/${id}`, data)
    return response.data
  },
}

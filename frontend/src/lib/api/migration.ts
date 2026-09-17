import apiClient from './client'
import {
  AssignedMigrationItem,
  AssignMigrationItemRequest,
  ClassificationRunSummary,
  FlaggedItems,
  MigrationItemKind,
  MigrationStatus,
} from '@/lib/types/migration'

export const migrationApi = {
  status: async () => {
    const response = await apiClient.get<MigrationStatus>('/migration/status')
    return response.data
  },

  items: async () => {
    const response = await apiClient.get<FlaggedItems>('/migration/items')
    return response.data
  },

  run: async () => {
    const response = await apiClient.post<ClassificationRunSummary>('/migration/run')
    return response.data
  },

  assign: async (kind: MigrationItemKind, id: string, data: AssignMigrationItemRequest) => {
    const response = await apiClient.patch<AssignedMigrationItem>(
      `/migration/items/${kind}/${id}`,
      data
    )
    return response.data
  },
}

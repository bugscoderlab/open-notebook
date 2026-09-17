// Content classification / migration types (issue #7/T6) — shapes from
// docs/7-DEVELOPMENT/team-access/api-contracts.md (Migration).

export type MigrationItemKind = 'notebook' | 'source'

export type MigrationItemReason =
  | 'ambiguous_tokens'
  | 'mixed_team_sources'
  | 'cross_team_link'

export interface MigrationStatus {
  completed: boolean
  completed_at: string | null
  flagged_notebooks: number
  flagged_sources: number
}

export interface FlaggedNotebook {
  id: string
  name: string
  reason: MigrationItemReason
  linked_teams: string[]
}

export interface FlaggedSource {
  id: string
  title: string
  reason: MigrationItemReason
  linked_teams: string[]
}

export interface FlaggedItems {
  notebooks: FlaggedNotebook[]
  sources: FlaggedSource[]
}

export interface ClassificationRunSummary {
  classified_sources: number
  classified_notebooks: number
  flagged_sources: number
  flagged_notebooks: number
  completed: boolean
  actions: string[]
}

export interface AssignMigrationItemRequest {
  team_id: string
  visibility: 'team' | 'company_shared'
}

export interface AssignedMigrationItem {
  id: string
  kind: MigrationItemKind
  team_id: string
  team_name: string
  visibility: 'team' | 'company_shared'
}

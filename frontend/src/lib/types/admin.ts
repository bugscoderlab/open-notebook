// Team-access admin types (issue #5/T4) — frozen contract shapes from
// docs/7-DEVELOPMENT/team-access/api-contracts.md (Users & teams).

export type UserRole = 'member' | 'team_manager' | 'ceo' | 'admin'

export type UserStatus = 'invited' | 'active' | 'disabled'

export interface AdminUser {
  id: string
  email: string
  display_name: string
  role: UserRole
  status: UserStatus
  team_id: string
  team_name: string
  last_active_at: string | null
}

export interface TeamSummary {
  id: string
  slug: string
  name: string
  description: string | null
  manager_id: string
  manager_name: string
  active: boolean
  member_count: number
  notebook_count: number
}

export interface InviteUserRequest {
  email: string
  display_name: string
  team_id: string
  role: UserRole
  temp_password: string
}

export interface UpdateUserRequest {
  team_id?: string
  role?: UserRole
  status?: UserStatus
  /** Regenerates the sign-in password (the no-SMTP "resend invite") and
   * revokes all sessions. */
  temp_password?: string
}

export interface CreateTeamRequest {
  slug: string
  name: string
  description?: string | null
}

export interface UpdateTeamRequest {
  name?: string
  description?: string | null
  manager_id?: string | null
  active?: boolean
}

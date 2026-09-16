'use client'

/**
 * Stubbed current-user hook for the T2 prototype shell.
 *
 * TODO(T3): replace this static stub with a real `GET /api/auth/me` call
 * (TanStack Query hook) once the auth backend lands. Shape follows the
 * frozen AuthUser contract in
 * docs/7-DEVELOPMENT/team-access/api-contracts.md.
 */
export interface AuthTeam {
  id: string
  slug: string
  name: string
}

export interface AuthUser {
  id: string
  email: string
  display_name: string
  role: 'member' | 'team_manager' | 'ceo' | 'admin'
  team: AuthTeam
}

const STUB_USER: AuthUser = {
  id: 'app_user:stub',
  email: 'aisha@company.com',
  display_name: 'Aisha Hassan',
  role: 'admin',
  team: { id: 'team:hr', slug: 'hr', name: 'HR' },
}

export function useCurrentUser(): { user: AuthUser } {
  return { user: STUB_USER }
}

/** Initials for the top-bar avatar, e.g. "Aisha Hassan" → "AH". */
export function getUserInitials(displayName: string): string {
  return displayName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toUpperCase())
    .join('')
}

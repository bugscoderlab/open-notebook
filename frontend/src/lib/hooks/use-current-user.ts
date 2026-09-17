'use client'

import { useAuthStore } from '@/lib/stores/auth-store'
import type { AuthTeam, AuthUser } from '@/lib/stores/auth-store'

export type { AuthTeam, AuthUser }

/**
 * Current identity for the shell (T3).
 *
 * The user comes from the auth store (populated from GET /api/auth/me, the
 * frozen AuthUser contract). Null in open mode (no users seeded yet) — the
 * shell must render an identity-neutral chrome in that case.
 */
export function useCurrentUser(): { user: AuthUser | null } {
  const user = useAuthStore((state) => state.user)
  return { user }
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

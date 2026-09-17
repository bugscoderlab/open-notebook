import axios from 'axios'
import { create } from 'zustand'
import { apiClient, setUnauthorizedHandler } from '@/lib/api/client'
import { CSRF_HEADER_NAME, getCsrfToken } from '@/lib/csrf'

/**
 * Cookie-session auth store (ADR-010, T3).
 *
 * The session token lives in an HttpOnly cookie — nothing token-shaped is
 * kept here or in localStorage. This store only holds the *identity* (the
 * AuthUser contract from GET /api/auth/me) plus UI state. Before bootstrap
 * seeds the first user the API reports auth disabled and the app runs in
 * open mode (authEnabled === false, user === null).
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
  team: AuthTeam | null
}

export type AuthErrorCode =
  | 'invalid_credentials'
  | 'rate_limited'
  | 'network'
  | 'server'
  | 'unknown'

interface AuthState {
  user: AuthUser | null
  /** null = not probed yet; false = open mode (no users seeded). */
  authEnabled: boolean | null
  isCheckingAuth: boolean
  isLoading: boolean
  /** Server error detail, when available (display fallback). */
  error: string | null
  /** Stable, i18n-mappable classification of the last failure. */
  errorCode: AuthErrorCode | null
  checkAuth: () => Promise<boolean>
  refreshUser: () => Promise<boolean>
  login: (email: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
}

function classifyError(error: unknown): AuthErrorCode {
  if (axios.isAxiosError(error)) {
    const status = error.response?.status
    if (status === 401) return 'invalid_credentials'
    if (status === 429) return 'rate_limited'
    if (!error.response) return 'network'
    return 'server'
  }
  return 'unknown'
}

function errorDetail(error: unknown): string | null {
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: string } | undefined)?.detail
    if (detail) return detail
  }
  return error instanceof Error ? error.message : null
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  user: null,
  authEnabled: null,
  isCheckingAuth: false,
  isLoading: false,
  error: null,
  errorCode: null,

  checkAuth: async () => {
    if (get().isCheckingAuth) {
      return get().user !== null || get().authEnabled === false
    }
    // Identity already loaded: authoritative until a request 401s (the
    // response interceptor clears the user). Without this, every useAuth()
    // mount re-probes, flips isCheckingAuth, and the dashboard guard
    // unmounts/remounts its children in an infinite /auth/me loop.
    if (get().user) {
      return true
    }
    set({ isCheckingAuth: true, error: null, errorCode: null })
    try {
      if (get().authEnabled === null) {
        const status = await apiClient.get<{ auth_enabled?: boolean }>('/auth/status', {
          headers: { 'Cache-Control': 'no-store' },
        })
        set({ authEnabled: status.data.auth_enabled ?? false })
      }
      if (get().authEnabled === false) {
        return true // open mode — no users seeded yet
      }
      return await get().refreshUser()
    } catch (error) {
      console.error('Failed to check auth:', error)
      set({ errorCode: classifyError(error), error: errorDetail(error) })
      return false
    } finally {
      set({ isCheckingAuth: false })
    }
  },

  refreshUser: async () => {
    try {
      const response = await apiClient.get<AuthUser>('/auth/me', {
        headers: { 'Cache-Control': 'no-store' },
      })
      set({ user: response.data, error: null, errorCode: null })
      return true
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 401) {
        set({ user: null })
        return false
      }
      throw error
    }
  },

  login: async (email: string, password: string) => {
    set({ isLoading: true, error: null, errorCode: null })
    try {
      await apiClient.post('/auth/login', { email, password })
      const authenticated = await get().refreshUser()
      if (!authenticated) {
        throw new Error('Session was not established after login')
      }
      set({ isLoading: false })
      return true
    } catch (error) {
      console.error('Login failed:', error)
      set({
        isLoading: false,
        user: null,
        errorCode: classifyError(error),
        error: errorDetail(error),
      })
      return false
    }
  },

  logout: async () => {
    const csrf = getCsrfToken()
    try {
      await apiClient.post('/auth/logout', null, {
        headers: csrf ? { [CSRF_HEADER_NAME]: csrf } : undefined,
      })
    } catch (error) {
      // Best effort: local identity clears even if the server is unreachable.
      console.error('Logout request failed:', error)
    }
    set({ user: null })
  },
}))

// A 401 from any request means the session died — drop the identity and let
// the dashboard guard redirect to /login.
setUnauthorizedHandler(() => {
  useAuthStore.setState({ user: null })
})

// Migration from the bearer-token era: any token material persisted under the
// old key is deleted on load (acceptance: no token material in localStorage).
export function clearLegacyAuthStorage(): void {
  if (typeof window !== 'undefined') {
    window.localStorage.removeItem('auth-storage')
  }
}

clearLegacyAuthStorage()

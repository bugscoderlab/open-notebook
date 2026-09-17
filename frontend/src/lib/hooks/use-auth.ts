'use client'

import { useAuthStore } from '@/lib/stores/auth-store'
import { useRouter } from 'next/navigation'
import { useEffect } from 'react'

/**
 * Auth facade for components (T3, ADR-010).
 *
 * Authenticated means: open mode (no users seeded yet — dev default) or a
 * live session (the store holds the AuthUser from GET /api/auth/me). The
 * session token itself is an HttpOnly cookie and never surfaces here.
 */
export function useAuth() {
  const router = useRouter()
  const {
    user,
    authEnabled,
    isCheckingAuth,
    isLoading,
    error,
    errorCode,
    login,
    logout,
    checkAuth,
  } = useAuthStore()

  useEffect(() => {
    void checkAuth()
    // checkAuth is a stable zustand action; run once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const isAuthenticated = authEnabled === false || user !== null

  const handleLogin = async (email: string, password: string) => {
    const success = await login(email, password)
    if (success) {
      const redirectPath = sessionStorage.getItem('redirectAfterLogin')
      if (redirectPath) {
        sessionStorage.removeItem('redirectAfterLogin')
        router.push(redirectPath)
      } else {
        router.push('/notebooks')
      }
    }
    return success
  }

  const handleLogout = async () => {
    await logout()
    router.push('/login')
  }

  return {
    user,
    authEnabled,
    isAuthenticated,
    // Probe state: true while checking auth (first paint). A finished probe
    // that failed (error set, authEnabled still unknown) is NOT loading — the
    // guards redirect to /login, where the connection-error card renders.
    isLoading: isCheckingAuth || (authEnabled === null && !error),
    // Login request in flight (form submit state) — distinct from probing so
    // the login page can keep showing the form after a failed attempt.
    isSubmitting: isLoading,
    error,
    errorCode,
    login: handleLogin,
    logout: handleLogout,
  }
}

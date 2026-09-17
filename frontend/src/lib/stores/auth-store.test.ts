import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'

// The store must work against the apiClient seam only — no real HTTP.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  unauthorized: null as null | (() => void),
}))

vi.mock('@/lib/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  setUnauthorizedHandler: (handler: () => void) => {
    mocks.unauthorized = handler
  },
}))

import { useAuthStore, clearLegacyAuthStorage } from './auth-store'

const AISHA = {
  id: 'app_user:aisha',
  email: 'aisha@company.com',
  display_name: 'Aisha Hassan',
  role: 'member' as const,
  team: { id: 'team:hr', slug: 'hr', name: 'HR' },
}

function axiosError(status: number, detail: string) {
  return new axios.AxiosError(
    `Request failed with status code ${status}`,
    String(status),
    undefined,
    undefined,
    { status, statusText: '', headers: {}, config: {} as never, data: { detail } },
  )
}

function resetStore() {
  useAuthStore.setState({
    user: null,
    authEnabled: null,
    isCheckingAuth: false,
    isLoading: false,
    error: null,
    errorCode: null,
  })
  mocks.get.mockReset()
  mocks.post.mockReset()
}

describe('auth-store (cookie sessions)', () => {
  beforeEach(resetStore)

  describe('checkAuth', () => {
    it('open mode: no users seeded → authenticated without a user', async () => {
      mocks.get.mockResolvedValueOnce({ data: { auth_enabled: false } })

      const authenticated = await useAuthStore.getState().checkAuth()

      expect(authenticated).toBe(true)
      expect(useAuthStore.getState().authEnabled).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('enabled: valid session → user loaded from /auth/me', async () => {
      mocks.get
        .mockResolvedValueOnce({ data: { auth_enabled: true } })
        .mockResolvedValueOnce({ data: AISHA })

      const authenticated = await useAuthStore.getState().checkAuth()

      expect(authenticated).toBe(true)
      expect(useAuthStore.getState().user).toEqual(AISHA)
    })

    it('enabled: no session → /auth/me 401 → not authenticated', async () => {
      mocks.get
        .mockResolvedValueOnce({ data: { auth_enabled: true } })
        .mockRejectedValueOnce(axiosError(401, 'Authentication required'))

      const authenticated = await useAuthStore.getState().checkAuth()

      expect(authenticated).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('network failure leaves authEnabled unknown with a network errorCode', async () => {
      mocks.get.mockRejectedValueOnce(
        new axios.AxiosError('Network Error', 'ERR_NETWORK'),
      )

      const authenticated = await useAuthStore.getState().checkAuth()

      expect(authenticated).toBe(false)
      expect(useAuthStore.getState().authEnabled).toBeNull()
      expect(useAuthStore.getState().errorCode).toBe('network')
    })

    it('does not re-probe status once authEnabled is known', async () => {
      useAuthStore.setState({ authEnabled: true })
      mocks.get.mockRejectedValueOnce(axiosError(401, 'Authentication required'))

      const authenticated = await useAuthStore.getState().checkAuth()

      expect(mocks.get).toHaveBeenCalledTimes(1)
      expect(mocks.get.mock.calls[0][0]).toBe('/auth/me')
      expect(authenticated).toBe(false)
    })
  })

  describe('login', () => {
    it('success: POSTs credentials, then loads identity from /auth/me', async () => {
      mocks.post.mockResolvedValueOnce({ status: 204 })
      mocks.get.mockResolvedValueOnce({ data: AISHA })

      const ok = await useAuthStore.getState().login('aisha@company.com', 'password')

      expect(ok).toBe(true)
      expect(mocks.post).toHaveBeenCalledWith('/auth/login', {
        email: 'aisha@company.com',
        password: 'password',
      })
      expect(useAuthStore.getState().user).toEqual(AISHA)
      expect(useAuthStore.getState().errorCode).toBeNull()
    })

    it('invalid credentials → generic errorCode, no user', async () => {
      mocks.post.mockRejectedValueOnce(
        axiosError(401, 'Invalid email or password'),
      )

      const ok = await useAuthStore.getState().login('aisha@company.com', 'wrong')

      expect(ok).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
      expect(useAuthStore.getState().errorCode).toBe('invalid_credentials')
    })

    it('rate limited → 429 mapped to rate_limited', async () => {
      mocks.post.mockRejectedValueOnce(
        axiosError(429, 'Too many login attempts. Try again later.'),
      )

      const ok = await useAuthStore.getState().login('aisha@company.com', 'password')

      expect(ok).toBe(false)
      expect(useAuthStore.getState().errorCode).toBe('rate_limited')
    })

    it('session not established after login (me 401) → login fails', async () => {
      mocks.post.mockResolvedValueOnce({ status: 204 })
      mocks.get.mockRejectedValueOnce(axiosError(401, 'Authentication required'))

      const ok = await useAuthStore.getState().login('aisha@company.com', 'password')

      expect(ok).toBe(false)
      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  describe('logout', () => {
    it('POSTs logout with the CSRF header and clears the user', async () => {
      document.cookie = 'open_notebook_csrf=tok'
      useAuthStore.setState({ user: AISHA })
      mocks.post.mockResolvedValueOnce({ status: 204 })

      await useAuthStore.getState().logout()

      expect(mocks.post).toHaveBeenCalledWith('/auth/logout', null, {
        headers: { 'x-csrf-token': 'tok' },
      })
      expect(useAuthStore.getState().user).toBeNull()
    })

    it('clears the user even when the logout request fails', async () => {
      useAuthStore.setState({ user: AISHA })
      mocks.post.mockRejectedValueOnce(new Error('network'))

      await useAuthStore.getState().logout()

      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  describe('unauthorized handler', () => {
    it('a 401 anywhere clears the identity', () => {
      useAuthStore.setState({ user: AISHA })
      expect(mocks.unauthorized).not.toBeNull()
      mocks.unauthorized!()
      expect(useAuthStore.getState().user).toBeNull()
    })
  })

  describe('legacy localStorage token', () => {
    it('no token material is persisted under auth-storage', () => {
      window.localStorage.setItem(
        'auth-storage',
        JSON.stringify({ state: { token: 'secret' }, version: 0 }),
      )
      useAuthStore.getState() // touch the store
      clearLegacyAuthStorage()
      expect(window.localStorage.getItem('auth-storage')).toBeNull()
    })
  })
})

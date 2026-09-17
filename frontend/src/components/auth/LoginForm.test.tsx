import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'

// src/test/setup.ts stubs use-auth globally; this suite exercises the real
// facade (store → mocked apiClient), so restore the real module here.
vi.unmock('@/lib/hooks/use-auth')

// The form's auth flow goes through the store → apiClient; stub HTTP.
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

vi.mock('@/lib/api/client', () => ({
  apiClient: {
    get: (...args: unknown[]) => mocks.get(...args),
    post: (...args: unknown[]) => mocks.post(...args),
  },
  setUnauthorizedHandler: vi.fn(),
}))

import { LoginForm } from './LoginForm'
import { useAuthStore } from '@/lib/stores/auth-store'

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

/** Probe succeeds: auth enabled; /auth/me behaves per `me`. */
function mockProbeEnabled(me: 'unauthenticated' | 'aisha') {
  mocks.get.mockImplementation((url: string) => {
    if (url === '/auth/status') {
      return Promise.resolve({ data: { auth_enabled: true } })
    }
    if (url === '/auth/me') {
      return me === 'aisha'
        ? Promise.resolve({ data: AISHA })
        : Promise.reject(axiosError(401, 'Authentication required'))
    }
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
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
}

async function submitForm() {
  fireEvent.change(screen.getByPlaceholderText('auth.emailPlaceholder'), {
    target: { value: 'aisha@company.com' },
  })
  fireEvent.change(screen.getByPlaceholderText('auth.passwordPlaceholder'), {
    target: { value: 'password' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'auth.signIn' }))
}

// t() returns the key in tests (see src/test/setup.ts), so assertions match keys.
describe('LoginForm', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.post.mockReset()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it('shows the connection-error card (not a spinner) when the auth probe fails', async () => {
    // Regression: a failed probe leaves authEnabled null AND sets an error —
    // the spinner branch must not shadow the connection card.
    mocks.get.mockRejectedValue(new axios.AxiosError('Network Error', 'ERR_NETWORK'))

    render(<LoginForm />)

    expect(await screen.findByText('common.connectionError')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText('auth.emailPlaceholder')).not.toBeInTheDocument()
  })

  it('shows the sign-in form once the probe reports auth enabled without a session', async () => {
    mockProbeEnabled('unauthenticated')

    render(<LoginForm />)

    expect(await screen.findByPlaceholderText('auth.emailPlaceholder')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('auth.passwordPlaceholder')).toBeInTheDocument()
  })

  it('maps a 401 login failure to the localized generic message', async () => {
    mockProbeEnabled('unauthenticated')
    mocks.post.mockRejectedValue(axiosError(401, 'Invalid email or password'))

    render(<LoginForm />)
    await screen.findByPlaceholderText('auth.emailPlaceholder')
    await submitForm()

    expect(await screen.findByText('auth.invalidCredentials')).toBeInTheDocument()
  })

  it('maps a 429 login failure to the rate-limit message', async () => {
    mockProbeEnabled('unauthenticated')
    mocks.post.mockRejectedValue(
      axiosError(429, 'Too many login attempts. Try again later.'),
    )

    render(<LoginForm />)
    await screen.findByPlaceholderText('auth.emailPlaceholder')
    await submitForm()

    expect(await screen.findByText('auth.tooManyAttempts')).toBeInTheDocument()
  })

  it('maps an unknown error to the shared generic error, not the raw server string', async () => {
    mockProbeEnabled('unauthenticated')
    mocks.post.mockRejectedValue(new Error('weird server detail'))

    render(<LoginForm />)
    await screen.findByPlaceholderText('auth.emailPlaceholder')
    await submitForm()

    expect(await screen.findByText('errors.genericError')).toBeInTheDocument()
    expect(screen.queryByText('weird server detail')).not.toBeInTheDocument()
  })
})

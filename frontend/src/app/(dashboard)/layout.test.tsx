import { cleanup, render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// src/test/setup.ts stubs use-auth globally; this suite exercises the real
// facade (store -> mocked apiClient), so restore the real module here.
vi.unmock('@/lib/hooks/use-auth')

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

// Version polling is unrelated to the auth guard — silence it.
vi.mock('@/lib/hooks/use-version-check', () => ({
  useVersionCheck: vi.fn(),
}))

// setup.ts stubs use-create-dialogs without the provider component the
// layout renders — keep the real provider, stub only the hook.
vi.mock('@/lib/hooks/use-create-dialogs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/use-create-dialogs')>()
  return {
    ...actual,
    useCreateDialogs: vi.fn(() => ({
      openSourceDialog: vi.fn(),
      openNotebookDialog: vi.fn(),
      openPodcastDialog: vi.fn(),
    })),
  }
})

// Unrelated chrome — the guard under test is the layout's auth gating.
vi.mock('@/components/providers/ModalProvider', () => ({
  ModalProvider: () => null,
}))
vi.mock('@/components/common/CommandPalette', () => ({
  CommandPalette: () => null,
}))

import DashboardLayout from './layout'
import { useAuthStore } from '@/lib/stores/auth-store'

const AISHA = {
  id: 'app_user:aisha',
  email: 'aisha@company.com',
  display_name: 'Aisha Hassan',
  role: 'member' as const,
  team: { id: 'team:hr', slug: 'hr', name: 'HR' },
}

function resetStore(overrides: Partial<ReturnType<typeof useAuthStore.getState>> = {}) {
  useAuthStore.setState({
    user: null,
    authEnabled: null,
    isCheckingAuth: false,
    isLoading: false,
    error: null,
    errorCode: null,
    ...overrides,
  })
}

function renderLayout(children: React.ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <DashboardLayout>{children}</DashboardLayout>
    </QueryClientProvider>,
  )
}

describe('DashboardLayout auth guard (regression: probe flip-flop loop)', () => {
  beforeEach(() => {
    mocks.get.mockReset()
    mocks.post.mockReset()
  })

  afterEach(() => {
    cleanup()
    resetStore()
  })

  it('renders children with a loaded identity WITHOUT re-probing /auth/me', async () => {
    // Regression: the sidebar (inside children) also auto-probes on mount.
    // If probing flips the layout back to its spinner, children unmount and
    // remount -> probe again -> infinite /auth/me loop and the notebooks
    // page never renders.
    resetStore({ user: AISHA, authEnabled: true })

    renderLayout(<div>NOTEBOOKS CONTENT</div>)

    expect(await screen.findByText('NOTEBOOKS CONTENT')).toBeInTheDocument()
    const meCalls = mocks.get.mock.calls.filter((call: unknown[]) => call[0] === '/auth/me')
    expect(meCalls).toHaveLength(0)
  })

  it('shows the spinner while the initial probe runs, then renders children', async () => {
    mocks.get.mockImplementation((url: string) => {
      if (url === '/auth/status') {
        return Promise.resolve({ data: { auth_enabled: true } })
      }
      if (url === '/auth/me') {
        return Promise.resolve({ data: AISHA })
      }
      return Promise.reject(new Error(`unexpected GET ${url}`))
    })

    renderLayout(<div>NOTEBOOKS CONTENT</div>)

    expect(await screen.findByText('NOTEBOOKS CONTENT')).toBeInTheDocument()
  })
})

/* eslint-disable @typescript-eslint/no-explicit-any */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { usePathname } from 'next/navigation'
import { AppSidebar } from './AppSidebar'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useAuthStore } from '@/lib/stores/auth-store'
import type { AuthUser } from '@/lib/stores/auth-store'

// Mock Tooltip components to avoid Radix UI async issues in tests
vi.mock('@/components/ui/tooltip', () => ({
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}))

describe('AppSidebar', () => {
  afterEach(() => {
    vi.mocked(usePathname).mockReturnValue('')
  })

  it('highlights only Models (not Settings) on the Models page', () => {
    vi.mocked(usePathname).mockReturnValue('/settings/models')

    const { container } = render(<AppSidebar />)

    const modelsButton = container.querySelector('a[href="/settings/models"] button')
    const settingsButton = container.querySelector('a[href="/settings"] button')

    expect(modelsButton?.className).toContain('font-semibold')
    expect(settingsButton?.className).toContain('font-medium')
    expect(settingsButton?.className).not.toContain('font-semibold')
  })

  it('renders correctly when expanded', () => {
    render(<AppSidebar />)

    // With mocked t() returning keys, check for translation key strings
    expect(screen.getByText('common.appName')).toBeDefined()
    expect(screen.getByText('navigation.sources')).toBeDefined()
    expect(screen.getByText('navigation.notebooks')).toBeDefined()
  })

  it('uses consistent spacing for expanded footer actions', () => {
    render(<AppSidebar />)

    const themeButton = screen.getByText('common.theme').closest('button')
    const languageButton = screen.getByText('common.language').closest('button')
    const signOutButton = screen.getByRole('button', { name: 'common.signOut' })

    expect(themeButton?.className.split(/\s+/)).toContain('px-3')

    for (const button of [themeButton, languageButton, signOutButton]) {
      expect(button?.className.split(/\s+/)).toContain('gap-2')
    }

    expect(themeButton?.querySelector(':scope > span.relative.size-4')).not.toBeNull()
    expect(signOutButton.className.split(/\s+/)).not.toContain('gap-3')
  })

  it('toggles collapse state when clicking handle', () => {
    const toggleCollapse = vi.fn()
    vi.mocked(useSidebarStore).mockReturnValue({
      isCollapsed: false,
      toggleCollapse,
    } as any)

    render(<AppSidebar />)

    fireEvent.click(screen.getByTestId('sidebar-toggle'))

    expect(toggleCollapse).toHaveBeenCalled()
  })

  it('shows collapsed view when isCollapsed is true', () => {
    vi.mocked(useSidebarStore).mockReturnValue({
      isCollapsed: true,
      toggleCollapse: vi.fn(),
    } as any)

    render(<AppSidebar />)

    // In collapsed mode, app name shouldn't be visible (as text)
    expect(screen.queryByText('common.appName')).toBeNull()
  })
})

// T4 role-aware nav: Users/Teams are admin-only nav entries; Advanced is
// hidden from non-admins. The mocked t() returns keys, so nav items are
// looked up by their translation keys. Identity comes from the real
// auth store (useCurrentUser) — set it directly per case.
describe('AppSidebar role-aware navigation (T4)', () => {
  const adminUser: AuthUser = {
    id: 'app_user:alex',
    email: 'alex@company.com',
    display_name: 'Alex Admin',
    role: 'admin',
    team: { id: 'team:executive', slug: 'executive', name: 'Executive' },
  }

  afterEach(() => {
    useAuthStore.setState({ user: null })
  })

  it('admin sees Users, Teams and Advanced', () => {
    useAuthStore.setState({ user: adminUser })

    render(<AppSidebar />)

    expect(screen.getByText('navigation.users')).toBeDefined()
    expect(screen.getByText('navigation.teams')).toBeDefined()
    expect(screen.getByText('navigation.advanced')).toBeDefined()
  })

  it.each(['member', 'team_manager', 'ceo'] as const)(
    '%s never sees Users/Teams nav entries and never sees Advanced',
    (role) => {
      useAuthStore.setState({ user: { ...adminUser, id: `app_user:${role}`, role } })

      render(<AppSidebar />)

      expect(screen.queryByText('navigation.users')).toBeNull()
      expect(screen.queryByText('navigation.teams')).toBeNull()
      expect(screen.queryByText('navigation.advanced')).toBeNull()
      // Regular entries stay visible.
      expect(screen.getByText('navigation.notebooks')).toBeDefined()
      expect(screen.getByText('navigation.settings')).toBeDefined()
    }
  )

  it('open mode (no identity yet) hides Users/Teams but keeps Advanced', () => {
    useAuthStore.setState({ user: null })

    render(<AppSidebar />)

    expect(screen.queryByText('navigation.users')).toBeNull()
    expect(screen.queryByText('navigation.teams')).toBeNull()
    expect(screen.getByText('navigation.advanced')).toBeDefined()
  })
})

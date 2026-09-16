'use client'

import { AppSidebar } from './AppSidebar'
import { AppTopBar } from './AppTopBar'
import { SetupBanner } from './SetupBanner'

interface AppShellProps {
  children: React.ReactNode
}

/**
 * Prototype shell (T2) — dark left nav + white top bar over the page
 * content. Widths follow the team-access prototype (244px sidebar, 76px
 * icon rail at ≤950px, sidebar hidden at ≤650px — see globals.css and
 * AppSidebar's media-query handling).
 */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen overflow-hidden">
      <AppSidebar />
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <AppTopBar />
        <SetupBanner />
        {children}
      </main>
    </div>
  )
}

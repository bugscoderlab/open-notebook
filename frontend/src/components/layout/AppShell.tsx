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
 *
 * The root also carries `contain:layout`: Chrome propagates the scrollable
 * overflow of nested page scrollers (`flex-1 overflow-y-auto`) up to the
 * document in some layouts, making the window itself scrollable into empty
 * space past the h-screen shell (observed on /settings). Layout containment
 * stops the propagation. It does not act as a containing block for
 * fixed-position descendants and has no visual effect.
 */
export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen overflow-hidden [contain:layout]">
      <AppSidebar />
      <main className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <AppTopBar />
        <SetupBanner />
        {children}
      </main>
    </div>
  )
}

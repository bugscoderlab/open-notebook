'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Search } from 'lucide-react'

import { Input } from '@/components/ui/input'
import { Pill } from '@/components/shell/pill'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getUserInitials, useCurrentUser } from '@/lib/hooks/use-current-user'
import { cn } from '@/lib/utils'

const TEAM_PILL_VARIANTS: Record<string, 'hr' | 'finance' | 'executive' | 'company-shared'> = {
  hr: 'hr',
  finance: 'finance',
  executive: 'executive',
}

/**
 * Prototype shell (T2) — the white top bar of the team-access prototype:
 * a search field on the left (visual only; submitting routes to /search,
 * matching the existing CommandPalette behaviour) and the account area
 * (stubbed user) on the right.
 */
export function AppTopBar() {
  const { t } = useTranslation()
  const router = useRouter()
  const { user } = useCurrentUser()
  const [query, setQuery] = useState('')

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmed = query.trim()
    router.push(trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : '/search')
  }

  return (
    <header className="flex h-[68px] shrink-0 items-center gap-4 border-b bg-card px-7 max-[650px]:px-3.5">
      <form onSubmit={handleSubmit} className="relative w-[min(520px,50vw)] max-[650px]:hidden" role="search">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-shell-muted"
        />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('shell.searchPlaceholder')}
          aria-label={t('shell.searchAria')}
          className="h-[39px] rounded-[9px] border-shell-line bg-shell-paper pl-9"
        />
      </form>

      <div className="ml-auto flex min-w-0 items-center gap-2 max-[650px]:w-full max-[650px]:justify-end">
        <span className="hidden text-sm font-medium text-shell-ink sm:block">
          {user.display_name}
        </span>
        <Pill variant={TEAM_PILL_VARIANTS[user.team.slug] ?? 'company-shared'}>
          {user.team.name}
        </Pill>
        <span
          aria-hidden="true"
          className={cn(
            'grid size-[35px] shrink-0 place-items-center rounded-full',
            'bg-shell-green text-xs font-bold text-white'
          )}
        >
          {getUserInitials(user.display_name)}
        </span>
      </div>
    </header>
  )
}

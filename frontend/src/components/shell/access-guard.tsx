'use client'

import { Lock } from 'lucide-react'

import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'

/**
 * Prototype shell (T2) — the "Access enforced" guard card pinned to the
 * sidebar footer in the team-access prototype. Visual only until the
 * team-access backend lands; the copy already reflects the intended rule.
 */
export function AccessGuard({ className }: { className?: string }) {
  const { t } = useTranslation()

  return (
    <div
      className={cn(
        'rounded-[11px] border border-shell-nav-guard-border bg-shell-nav-guard-bg p-[11px]',
        className
      )}
    >
      <b className="flex items-center gap-1.5 text-[11px] text-white">
        <Lock className="h-3 w-3" />
        {t('shell.accessGuardTitle')}
      </b>
      <p className="mt-1 text-[10px] leading-snug text-shell-nav-muted">
        {t('shell.accessGuardDescription')}
      </p>
    </div>
  )
}

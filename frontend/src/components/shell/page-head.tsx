import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.head` of the team-access prototype:
 * page title + optional description on the left, actions on the right.
 * The shell carries the visual language; pages keep their own logic and
 * only delegate the header chrome.
 */
function PageHead({
  className,
  eyebrow,
  title,
  description,
  actions,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  eyebrow?: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <div
      className={cn('flex items-start justify-between gap-[18px]', className)}
      {...props}
    >
      <div className="min-w-0 space-y-1">
        {eyebrow && (
          <div className="text-[10px] font-extrabold uppercase tracking-[0.12em] text-shell-green">
            {eyebrow}
          </div>
        )}
        <h1 className="font-display text-3xl font-bold tracking-tight">{title}</h1>
        {description && (
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export { PageHead }

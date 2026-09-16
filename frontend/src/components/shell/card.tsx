import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.card` of the team-access prototype:
 * a panel with a hover lift, used for notebook/team/transformation cards.
 */
function Card({ className, ...props }: React.HTMLAttributes<HTMLElement>) {
  return (
    <article
      className={cn(
        'flex min-h-[165px] flex-col rounded-xl border bg-card text-card-foreground p-4 transition-all duration-150 hover:-translate-y-0.5 hover:shadow-[0_10px_30px_rgba(24,61,48,0.06)]',
        className
      )}
      {...props}
    />
  )
}

export { Card }

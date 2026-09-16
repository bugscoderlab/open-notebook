import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.status` chip of the team-access
 * prototype (e.g. "ACTIVE", "HEALTHY"). Kept as its own component so call
 * sites don't restyle Pill for the common status case.
 */
function StatusBadge({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'inline-flex w-fit items-center rounded-full bg-pill-status-bg px-[7px] py-[3px] text-[9px] font-extrabold uppercase leading-none tracking-wide text-pill-status-fg',
        className
      )}
      {...props}
    />
  )
}

export { StatusBadge }

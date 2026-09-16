import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — white rounded container with a hairline
 * border, matching the `.panel` of the team-access prototype. Surfaces use
 * theme-adaptive colors so dark mode keeps working.
 */
function Panel({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('rounded-xl border bg-card text-card-foreground p-[18px]', className)}
      {...props}
    />
  )
}

export { Panel }

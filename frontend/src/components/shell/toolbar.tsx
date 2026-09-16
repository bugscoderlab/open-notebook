import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.toolbar` of the team-access
 * prototype: a filter/search row that sits between the page head and lists.
 */
function Toolbar({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('my-[18px] flex items-center gap-2', className)} {...props} />
}

export { Toolbar }

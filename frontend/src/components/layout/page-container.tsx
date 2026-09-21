import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Catalog (01-catalog.html) — the `#page` container: one scrollable column
 * capped at 1400px with the standard page padding. Every dashboard page that
 * scrolls as a whole wraps its content in this.
 *
 * `min-h-0`: as a direct child of the AppShell's flex column, without it the
 * container's automatic minimum size equals its content height, so an inner
 * `overflow-auto` list never constrains and no scrollbar appears.
 */
function PageContainer({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('mx-auto min-h-0 w-full max-w-[1400px] flex-1 p-6 lg:p-8', className)}
      {...props}
    />
  )
}

export { PageContainer }

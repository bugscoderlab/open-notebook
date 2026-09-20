import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Catalog (01-catalog.html) — the `#page` container: one scrollable column
 * capped at 1400px with the standard page padding. Every dashboard page that
 * scrolls as a whole wraps its content in this.
 */
function PageContainer({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('mx-auto w-full max-w-[1400px] flex-1 p-6 lg:p-8', className)}
      {...props}
    />
  )
}

export { PageContainer }

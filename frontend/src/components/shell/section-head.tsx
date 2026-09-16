import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.section-head` of the team-access
 * prototype: a section title with a muted hint on the right.
 */
function SectionHead({
  className,
  title,
  hint,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  title: React.ReactNode
  hint?: React.ReactNode
}) {
  return (
    <div className={cn('mb-3 mt-6 flex items-end justify-between gap-4', className)} {...props}>
      <h2 className="text-base font-bold leading-tight">{title}</h2>
      {hint && <span className="text-[11px] text-muted-foreground">{hint}</span>}
    </div>
  )
}

export { SectionHead }

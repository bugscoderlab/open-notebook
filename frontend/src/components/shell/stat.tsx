import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.stat` of the team-access prototype:
 * small muted label over a large number.
 */
function Stat({
  className,
  label,
  value,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  label: React.ReactNode
  value: React.ReactNode
}) {
  return (
    <div
      className={cn('rounded-xl border bg-card text-card-foreground px-4 py-3.5', className)}
      {...props}
    >
      <small className="text-xs text-muted-foreground">{label}</small>
      <strong className="mt-[3px] block text-[23px] font-bold leading-tight">{value}</strong>
    </div>
  )
}

export { Stat }

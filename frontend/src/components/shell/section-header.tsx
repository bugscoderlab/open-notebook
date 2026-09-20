import * as React from 'react'

import { ChevronDown } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * Catalog (01-catalog.html) — `sectionHeader()`: a section title with a
 * mono count on the right, separated by a hairline. Optionally collapsible.
 */
function SectionHeader({
  className,
  title,
  count,
  collapsible = false,
  open = true,
  onToggle,
  actions,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  title: React.ReactNode
  count?: React.ReactNode
  collapsible?: boolean
  open?: boolean
  onToggle?: () => void
  actions?: React.ReactNode
}) {
  return (
    <div
      className={cn('flex items-center gap-2 border-b border-border pb-3', className)}
      {...props}
    >
      {collapsible && (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="-ml-1 rounded-md p-1 text-muted-foreground transition-colors hover:bg-card"
        >
          <ChevronDown
            className={cn('h-4 w-4 transition-transform', !open && '-rotate-90')}
          />
        </button>
      )}
      <h2 className="font-display text-lg font-semibold tracking-tight">{title}</h2>
      {actions}
      {count != null && (
        <span
          className={cn(
            'ml-auto font-mono text-xs tabular-nums text-muted-foreground',
            collapsible && 'mr-1'
          )}
        >
          {count}
        </span>
      )}
    </div>
  )
}

export { SectionHeader }

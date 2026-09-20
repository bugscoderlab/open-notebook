import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Catalog (01-catalog.html) — the Settings/Advanced section card: a bordered
 * surface whose header bar carries `border-b px-5 py-3.5` with a display-font
 * title.
 */
function SectionCard({
  className,
  title,
  titleClassName,
  action,
  children,
  ...props
}: Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> & {
  title: React.ReactNode
  titleClassName?: string
  action?: React.ReactNode
}) {
  return (
    <section className={cn('rounded-lg border bg-card', className)} {...props}>
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-3.5">
        <h2
          className={cn(
            'font-display text-base font-semibold tracking-tight',
            titleClassName
          )}
        >
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export { SectionCard }

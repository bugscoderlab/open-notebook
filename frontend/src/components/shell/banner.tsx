import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.banner` of the team-access
 * prototype: pale-green info strip with an icon, a title/text block and an
 * optional trailing element (usually a Pill).
 */
function Banner({
  className,
  icon,
  title,
  children,
  action,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & {
  icon?: React.ReactNode
  title?: React.ReactNode
  action?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-2.5 rounded-xl border px-3.5 py-[11px] bg-shell-banner-bg border-shell-banner-border text-shell-ink',
        className
      )}
      {...props}
    >
      {icon}
      <div className="min-w-0 flex-1 text-[11px]">
        {title && <b className="block text-xs">{title}</b>}
        {children && <span className="text-shell-muted">{children}</span>}
      </div>
      {action}
    </div>
  )
}

export { Banner }

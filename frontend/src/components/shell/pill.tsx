import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * Prototype shell primitive (T2) — the `.pill` of the team-access prototype:
 * uppercase, letter-spaced department/status chip. Variants map to the
 * department palette (hr / finance / executive / company-shared) plus the
 * neutral "status" chip.
 */
const pillVariants = cva(
  'inline-flex w-fit items-center rounded-full px-2 py-1 text-[9px] font-extrabold uppercase leading-none tracking-[0.07em]',
  {
    variants: {
      variant: {
        hr: 'bg-pill-hr-bg text-pill-hr-fg',
        finance: 'bg-pill-finance-bg text-pill-finance-fg',
        executive: 'bg-pill-executive-bg text-pill-executive-fg',
        'company-shared': 'bg-pill-shared-bg text-pill-shared-fg',
        status: 'bg-pill-status-bg text-pill-status-fg',
      },
    },
    defaultVariants: {
      variant: 'status',
    },
  }
)

export interface PillProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof pillVariants> {}

function Pill({ className, variant, ...props }: PillProps) {
  return <span className={cn(pillVariants({ variant }), className)} {...props} />
}

export { Pill, pillVariants }

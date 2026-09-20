'use client'

import { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { ChevronLeft } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CollapsibleColumnProps {
  isCollapsed: boolean
  onToggle: () => void
  /** Color bar shown at the top of the collapsed rail (e.g. 'bg-sage', 'bg-gold'). */
  collapsedColor: string
  collapsedLabel: string
  children: ReactNode
}

export function CollapsibleColumn({
  isCollapsed,
  onToggle,
  collapsedColor,
  collapsedLabel,
  children,
}: CollapsibleColumnProps) {
  const isCJK = /[\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af]/.test(collapsedLabel);

  if (isCollapsed) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={onToggle}
              className={cn(
                'flex h-full min-h-[280px] w-full flex-col items-center gap-3',
                'rounded-md border',
                'bg-card hover:bg-accent/50',
                'px-2 py-4',
                'text-muted-foreground hover:text-foreground',
                'transition-colors',
                'cursor-pointer group',
                'lg:w-12'
              )}
              aria-label={`Expand ${collapsedLabel}`}
            >
              <span
                aria-hidden
                className={cn('h-3.5 w-[3px] shrink-0 rounded-full', collapsedColor)}
              />
              <div
                className="text-[11px] font-semibold uppercase tracking-[0.13em] whitespace-nowrap"
                style={{ writingMode: 'vertical-rl', transform: isCJK ? 'none' : 'rotate(180deg)', textOrientation: 'mixed' }}
              >
                {collapsedLabel}
              </div>
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">
            <p>Expand {collapsedLabel}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  return (
    <div className="h-full min-h-0 transition-all duration-150">
      {children}
    </div>
  )
}

// Factory function to create a collapse button for card headers
export function createCollapseButton(onToggle: () => void, label: string) {
  return (
    <div className="hidden lg:block">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={(e) => {
                e.stopPropagation()
                onToggle()
              }}
              className="h-7 w-7 hover:bg-accent"
              aria-label={`Collapse ${label}`}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            <p>Collapse {label}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  )
}

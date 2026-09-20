'use client'

import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

interface ContextIndicatorProps {
  sourcesInsights: number
  sourcesFull: number
  notesCount: number
  tokenCount?: number
  charCount?: number
  className?: string
}

// Helper function to format large numbers with K/M suffixes
function formatNumber(num: number): string {
  if (num >= 1000000) {
    return `${(num / 1000000).toFixed(1)}M`
  }
  if (num >= 1000) {
    return `${(num / 1000).toFixed(1)}K`
  }
  return num.toString()
}

const chipClass = 'inline-flex items-center gap-1.5 rounded-[4px] bg-muted px-2 py-1 font-mono text-[10.5px] text-muted-foreground ring-1 ring-inset ring-border'
const dotClass = 'h-1.5 w-1.5 rounded-full'

export function ContextIndicator({
  sourcesInsights,
  sourcesFull,
  notesCount,
  tokenCount,
  charCount,
  className
}: ContextIndicatorProps) {
  const hasContext = (sourcesInsights + sourcesFull) > 0 || notesCount > 0

  if (!hasContext) {
    return (
      <div className={cn('flex shrink-0 items-center border-b border-border px-4 py-2.5 text-xs text-muted-foreground', className)}>
        No sources or notes included in context. Toggle icons on cards to include them.
      </div>
    )
  }

  return (
    <div className={cn('flex shrink-0 flex-wrap items-center gap-1.5 border-b border-border px-4 py-2.5', className)}>
      {sourcesInsights > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={cn(chipClass, 'cursor-default')}>
              <span aria-hidden className={cn(dotClass, 'bg-sage')} />
              <span>{sourcesInsights}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>Insights for {sourcesInsights} source{sourcesInsights !== 1 ? 's' : ''}</p>
          </TooltipContent>
        </Tooltip>
      )}

      {sourcesFull > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={cn(chipClass, 'cursor-default')}>
              <span aria-hidden className={cn(dotClass, 'bg-sage')} />
              <span>{sourcesFull}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>{sourcesFull} full source{sourcesFull !== 1 ? 's' : ''}</p>
          </TooltipContent>
        </Tooltip>
      )}

      {notesCount > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className={cn(chipClass, 'cursor-default')}>
              <span aria-hidden className={cn(dotClass, 'bg-gold')} />
              <span>{notesCount}</span>
            </span>
          </TooltipTrigger>
          <TooltipContent>
            <p>{notesCount} full note{notesCount !== 1 ? 's' : ''}</p>
          </TooltipContent>
        </Tooltip>
      )}

      {(tokenCount !== undefined || charCount !== undefined) && (
        (tokenCount !== undefined && tokenCount > 0) || (charCount !== undefined && charCount > 0)
      ) && (
        <span className={chipClass}>
          <span aria-hidden className={cn(dotClass, 'bg-teal')} />
          <span>
            {tokenCount !== undefined && tokenCount > 0 && (
              <>{formatNumber(tokenCount)} tokens</>
            )}
            {tokenCount !== undefined && charCount !== undefined && tokenCount > 0 && charCount > 0 && (
              <> / </>
            )}
            {charCount !== undefined && charCount > 0 && (
              <>{formatNumber(charCount)} chars</>
            )}
          </span>
        </span>
      )}
    </div>
  )
}

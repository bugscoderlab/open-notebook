'use client'

import Link from 'next/link'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import type { Locale } from 'date-fns/locale'
import { BookOpen, ChevronDown, ChevronRight, FileText } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { notebooksApi } from '@/lib/api/notebooks'
import type { RecentlyViewedResponse } from '@/lib/types/api'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'

interface RecentlyViewedProps {
  limit?: number
}

function getItemHref(item: RecentlyViewedResponse) {
  if (item.type === 'notebook') {
    return `/notebooks/${encodeURIComponent(item.id)}`
  }

  return `/sources/${item.id}`
}

function formatViewedAt(value: string, locale: Locale) {
  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  return formatDistanceToNow(date, {
    addSuffix: true,
    locale,
  })
}

export function RecentlyViewed({ limit = 12 }: RecentlyViewedProps) {
  const { t, language } = useTranslation()
  const [isOpen, setIsOpen] = useState(true)
  const locale = getDateLocale(language)
  const { data: items, isLoading, isError } = useQuery({
    queryKey: ['recently-viewed', limit],
    queryFn: () => notebooksApi.recentlyViewed(limit),
  })

  if (isLoading || isError || !items || items.length === 0) {
    return null
  }

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen} className="space-y-4">
      <div className="flex items-center gap-2 border-b pb-3">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="-ml-1 h-7 w-7 p-0" aria-expanded={isOpen}>
            {isOpen ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            <span className="sr-only">
              {t('notebooks.toggleRecentlyViewed')}
            </span>
          </Button>
        </CollapsibleTrigger>
        <h2 className="font-display text-lg font-semibold tracking-tight">
          {t('notebooks.recentlyViewed')}
        </h2>
        <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
          {items.length}
        </span>
      </div>

      <CollapsibleContent>
        <div className="rail -mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-1">
          {items.map((item) => {
            const Icon = item.type === 'notebook' ? BookOpen : FileText
            const typeLabel =
              item.type === 'notebook'
                ? t('notebooks.recentlyViewedNotebook')
                : t('notebooks.recentlyViewedSource')

            return (
              <Link
                key={`${item.type}-${item.id}`}
                href={getItemHref(item)}
                className="group w-[240px] shrink-0 snap-start rounded-md border bg-card px-3 py-2.5 card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-muted ${
                      item.type === 'notebook' ? 'text-teal' : 'text-sage'
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{item.title}</p>
                    <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                      {t('notebooks.lastViewed', {
                        time: formatViewedAt(item.last_viewed_at, locale),
                      })}
                    </p>
                  </div>
                </div>
                <span className="mt-2 block text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                  {typeLabel}
                </span>
              </Link>
            )
          })}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

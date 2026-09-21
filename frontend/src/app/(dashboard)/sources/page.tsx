'use client'

import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { sourcesApi, type SourceSortField } from '@/lib/api/sources'
import { SourceListResponse } from '@/lib/types/api'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { EmptyState } from '@/components/common/EmptyState'
import { AppShell } from '@/components/layout/AppShell'
import { PageContainer } from '@/components/layout/page-container'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { FileText, AlignLeft, Link as LinkIcon, MoreVertical, Plus, Search, Trash2 } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import { AddSourceDialog } from '@/components/sources/AddSourceDialog'

type SourceKind = 'link' | 'file' | 'text'
type SourceFilter = 'all' | SourceKind

const getSourceKind = (source: SourceListResponse): SourceKind => {
  if (source.asset?.url) return 'link'
  if (source.asset?.file_path) return 'file'
  return 'text'
}

export default function SourcesPage() {
  const { t, language } = useTranslation()
  const [sourceDialogOpen, setSourceDialogOpen] = useState(false)
  const failedToLoadMessage = t('sources.failedToLoad')
  const [sources, setSources] = useState<SourceListResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [filter, setFilter] = useState<SourceFilter>('all')
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState<SourceSortField>('updated')
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; source: SourceListResponse | null }>({
    open: false,
    source: null
  })
  const router = useRouter()
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const offsetRef = useRef(0)
  const loadingMoreRef = useRef(false)
  const hasMoreRef = useRef(true)
  const PAGE_SIZE = 30

  const fetchSources = useCallback(async (reset = false) => {
    try {
      // Check flags before proceeding
      if (!reset && (loadingMoreRef.current || !hasMoreRef.current)) {
        return
      }

      if (reset) {
        setLoading(true)
        offsetRef.current = 0
        setSources([])
        hasMoreRef.current = true
      } else {
        loadingMoreRef.current = true
        setLoadingMore(true)
      }

      const data = await sourcesApi.list({
        limit: PAGE_SIZE,
        offset: offsetRef.current,
        sort_by: sortBy,
        sort_order: 'desc',
      })

      if (reset) {
        setSources(data)
      } else {
        setSources(prev => [...prev, ...data])
      }

      // Check if we have more data
      const hasMoreData = data.length === PAGE_SIZE
      hasMoreRef.current = hasMoreData
      offsetRef.current += data.length
    } catch (err) {
      console.error('Failed to fetch sources:', err)
      setError(failedToLoadMessage)
      toast.error(failedToLoadMessage)
    } finally {
      setLoading(false)
      setLoadingMore(false)
      loadingMoreRef.current = false
    }
  }, [sortBy, failedToLoadMessage])

  // Initial load and when sort changes
  useEffect(() => {
    fetchSources(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortBy])

  // Catalog rows: filter chips + client-side search over the loaded pages
  const visibleSources = useMemo(() => {
    const q = search.trim().toLowerCase()
    return sources.filter((source) => {
      if (filter !== 'all' && getSourceKind(source) !== filter) return false
      if (!q) return true
      return (
        (source.title || '').toLowerCase().includes(q) ||
        (source.asset?.url || '').toLowerCase().includes(q) ||
        (source.asset?.file_path || '').toLowerCase().includes(q)
      )
    })
  }, [sources, filter, search])

  useEffect(() => {
    setSelectedIndex(0)
  }, [filter, search])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (visibleSources.length === 0) return

      // Don't hijack keys while typing in inputs or picking from selects
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((prev) => {
            const newIndex = Math.min(prev + 1, visibleSources.length - 1)
            // Scroll to keep selected row visible
            setTimeout(() => scrollToSelectedRow(newIndex), 0)
            return newIndex
          })
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((prev) => {
            const newIndex = Math.max(prev - 1, 0)
            // Scroll to keep selected row visible
            setTimeout(() => scrollToSelectedRow(newIndex), 0)
            return newIndex
          })
          break
        case 'Enter':
          e.preventDefault()
          if (visibleSources[selectedIndex]) {
            router.push(`/sources/${visibleSources[selectedIndex].id}`)
          }
          break
        case 'Home':
          e.preventDefault()
          setSelectedIndex(0)
          setTimeout(() => scrollToSelectedRow(0), 0)
          break
        case 'End':
          e.preventDefault()
          const lastIndex = visibleSources.length - 1
          setSelectedIndex(lastIndex)
          setTimeout(() => scrollToSelectedRow(lastIndex), 0)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [visibleSources, selectedIndex, router])

  const scrollToSelectedRow = (index: number) => {
    const scrollContainer = scrollContainerRef.current
    if (!scrollContainer) return

    const selectedRow = scrollContainer.querySelectorAll('[data-source-row]')[index] as HTMLElement | undefined
    if (!selectedRow) return

    const containerRect = scrollContainer.getBoundingClientRect()
    const rowRect = selectedRow.getBoundingClientRect()

    // Check if row is above visible area
    if (rowRect.top < containerRect.top) {
      selectedRow.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
    // Check if row is below visible area
    else if (rowRect.bottom > containerRect.bottom) {
      selectedRow.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }

  // Set up scroll listener after sources are loaded
  useEffect(() => {
    const scrollContainer = scrollContainerRef.current
    if (!scrollContainer) return

    let scrollTimeout: NodeJS.Timeout | null = null

    const handleScroll = () => {
      if (scrollTimeout) {
        clearTimeout(scrollTimeout)
      }

      scrollTimeout = setTimeout(() => {
        if (!scrollContainerRef.current) return

        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight

        // Load more when within 200px of the bottom
        if (distanceFromBottom < 200 && !loadingMoreRef.current && hasMoreRef.current) {
          fetchSources(false)
        }
      }, 100)
    }

    scrollContainer.addEventListener('scroll', handleScroll)
    handleScroll() // Check on mount

    return () => {
      scrollContainer.removeEventListener('scroll', handleScroll)
      if (scrollTimeout) {
        clearTimeout(scrollTimeout)
      }
    }
  }, [fetchSources, sources.length])

  // Catalog STATUS style — fern when ready, pulsing gold while queued/running,
  // danger on failure.
  const renderStatusChip = (status: string | undefined) => {
    if (!status) return null

    const config: Record<string, { label: string; dot: string; text: string; tint?: boolean }> = {
      new: { label: t('sources.statusPreparing'), dot: 'bg-gold animate-pulse', text: 'text-gold' },
      queued: { label: t('sources.statusQueued'), dot: 'bg-gold animate-pulse', text: 'text-gold' },
      running: { label: t('sources.statusProcessing'), dot: 'bg-gold animate-pulse', text: 'text-gold' },
      completed: { label: t('sources.statusCompleted'), dot: 'bg-fern', text: 'text-fern', tint: true },
      failed: { label: t('sources.statusFailed'), dot: 'bg-danger', text: 'text-danger' },
    }
    const chip = config[status]

    if (!chip) return null

    return (
      <span
        className={cn(
          'inline-flex items-center gap-1.5 rounded-[4px] px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ring-border',
          chip.tint ? 'bg-fern-tint' : 'bg-muted'
        )}
      >
        <span aria-hidden className={cn('h-2 w-2 rounded-[3px]', chip.dot)} />
        <span className={chip.text}>{chip.label}</span>
      </span>
    )
  }

  const getOrigin = (source: SourceListResponse): string | null => {
    if (source.asset?.url) {
      try {
        return new URL(source.asset.url).host
      } catch {
        return source.asset.url
      }
    }
    if (source.asset?.file_path) {
      return source.asset.file_path.split(/[/\\]/).pop() || null
    }
    return null
  }

  const getTypeIcon = (kind: SourceKind) => {
    if (kind === 'link') return <LinkIcon className="h-4 w-4" />
    if (kind === 'file') return <FileText className="h-4 w-4" />
    return <AlignLeft className="h-4 w-4" />
  }

  const getTypeLabel = (kind: SourceKind) => {
    if (kind === 'link') return t('sources.type.link')
    if (kind === 'file') return t('sources.type.file')
    return t('sources.type.text')
  }

  const getMetaLine = (source: SourceListResponse): string => {
    const parts: string[] = []
    const origin = getOrigin(source)
    if (origin) parts.push(origin)
    parts.push(getTypeLabel(getSourceKind(source)))
    if (source.insights_count) {
      parts.push(t('sources.insightsCount', { count: source.insights_count }))
    }
    return parts.join(' · ')
  }

  const handleRowClick = useCallback((index: number, sourceId: string) => {
    setSelectedIndex(index)
    router.push(`/sources/${sourceId}`)
  }, [router])

  const handleDeleteClick = useCallback((source: SourceListResponse) => {
    setDeleteDialog({ open: true, source })
  }, [])

  const handleDeleteConfirm = async () => {
    if (!deleteDialog.source) return

    try {
      await sourcesApi.delete(deleteDialog.source.id)
      toast.success(t('sources.deleteSuccess'))
      // Remove the deleted source from the list
      setSources(prev => prev.filter(s => s.id !== deleteDialog.source?.id))
      setDeleteDialog({ open: false, source: null })
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Failed to delete source:', error)
      toast.error(t(getApiErrorKey(error.response?.data?.detail || error.message)))
    }
  }

  const filters: Array<{ key: SourceFilter; label: string }> = [
    { key: 'all', label: t('common.all') },
    { key: 'link', label: t('sources.type.link') },
    { key: 'file', label: t('sources.type.file') },
    { key: 'text', label: t('sources.type.text') },
  ]

  const sortOptions: Array<{ key: SourceSortField; label: string }> = [
    { key: 'updated', label: t('common.updated_label') },
    { key: 'created', label: t('common.created_label') },
    { key: 'title', label: t('common.title') },
    { key: 'type', label: t('common.type') },
    { key: 'insights_count', label: t('sources.insights') },
    { key: 'embedded', label: t('sources.embedded') },
  ]

  const renderContent = () => {
    if (loading) {
      return (
        <div className="flex h-full items-center justify-center">
          <LoadingSpinner />
        </div>
      )
    }

    if (error) {
      return (
        <div className="flex h-full items-center justify-center">
          <p className="text-destructive">{error}</p>
        </div>
      )
    }

    if (sources.length === 0) {
      return (
        <EmptyState
          icon={FileText}
          title={t('sources.noSourcesYet')}
          description={t('sources.allSourcesDescShort')}
          action={
            <Button onClick={() => setSourceDialogOpen(true)} variant="outline" className="mt-4">
              <Plus className="h-4 w-4 mr-2" />
              {t('sources.newSource')}
            </Button>
          }
        />
      )
    }

    return (<>
      {/* Whole page scrolls (browser scrollbar) — the list grows naturally and
          infinite scroll listens on this container */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto">
        <PageContainer className="space-y-8">
          {/* Catalog header — display title, quiet subtitle, search + primary action */}
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <h1 className="font-display text-3xl font-bold tracking-tight">{t('sources.title')}</h1>
              <p className="mt-1 text-sm text-muted-foreground">
                {t('sources.catalogDesc')}
              </p>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                <Input
                  type="search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('sources.searchPlaceholder')}
                  className="w-full pl-9 sm:w-64"
                />
              </div>
              <Button onClick={() => setSourceDialogOpen(true)}>
                <Plus className="mr-2 h-4 w-4" />
                {t('sources.add')}
              </Button>
            </div>
          </div>

          {/* Filter chips rail + sort */}
          <div className="flex items-center gap-2">
            <div className="rail -mx-1 flex flex-1 gap-2 overflow-x-auto px-1 pb-1">
              {filters.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  onClick={() => setFilter(f.key)}
                  aria-pressed={filter === f.key}
                  className={cn(
                    'shrink-0 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors',
                    filter === f.key
                      ? 'border-fern bg-fern-tint text-fern'
                      : 'border-border bg-card text-muted-foreground hover:bg-muted'
                  )}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="w-40 shrink-0">
              <Select
                value={sortBy}
                onValueChange={(value) => setSortBy(value as SourceSortField)}
                aria-label={t('sources.sortBy')}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sortOptions.map((option) => (
                    <SelectItem key={option.key} value={option.key} className="text-xs">
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Catalog row list */}
          {visibleSources.length === 0 ? (
            <EmptyState
              icon={Search}
              title={t('common.noMatches')}
              description={t('common.tryDifferentSearch')}
            />
          ) : (
            <div className="divide-y divide-border overflow-hidden rounded-lg border bg-card">
              {visibleSources.map((source, index) => {
                const kind = getSourceKind(source)
                return (
                  <div
                    key={source.id}
                    data-source-row
                    onClick={() => handleRowClick(index, source.id)}
                    onMouseEnter={() => setSelectedIndex(index)}
                    className={cn(
                      'group flex cursor-pointer items-center gap-4 px-4 py-3 transition-colors',
                      selectedIndex === index ? 'bg-accent' : 'hover:bg-accent/60'
                    )}
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-slate-hue">
                      {getTypeIcon(kind)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">
                        {source.title || t('sources.untitledSource')}
                      </div>
                      <p className="mt-0.5 truncate text-xs text-muted-foreground">
                        {getMetaLine(source)}
                      </p>
                    </div>
                    <div className="hidden shrink-0 sm:block">
                      {renderStatusChip(source.status)}
                    </div>
                    <span className="hidden w-24 shrink-0 text-right font-mono text-[11px] text-muted-foreground md:block">
                      {formatDistanceToNow(new Date(source.updated), {
                        addSuffix: true,
                        locale: getDateLocale(language)
                      })}
                    </span>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('common.actions')}
                          onClick={(e) => e.stopPropagation()}
                          className="shrink-0 text-muted-foreground opacity-0 transition-opacity hover:bg-muted group-hover:opacity-100"
                        >
                          <MoreVertical className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                        <DropdownMenuItem
                          className="text-destructive"
                          onClick={() => handleDeleteClick(source)}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          {t('sources.delete')}
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )
              })}
            </div>
          )}
          {loadingMore && (
            <div className="flex h-16 items-center justify-center">
              <LoadingSpinner />
              <span className="ml-2 text-muted-foreground">{t('sources.loadingMore')}</span>
            </div>
          )}
        </PageContainer>
      </div>

      <ConfirmDialog
        open={deleteDialog.open}
        onOpenChange={(open) => setDeleteDialog({ open, source: deleteDialog.source })}
        title={t('sources.delete')}
        description={t('sources.deleteConfirmWithTitle', { title: deleteDialog.source?.title || t('sources.untitledSource') })}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
        onConfirm={handleDeleteConfirm}
      />
    </>)
  }

  return (
    <AppShell>
      {renderContent()}
      <AddSourceDialog
        open={sourceDialogOpen}
        onOpenChange={(open) => {
          setSourceDialogOpen(open)
          if (!open) fetchSources(true)
        }}
      />
    </AppShell>
  )
}

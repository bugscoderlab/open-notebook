'use client'

import { useRouter } from 'next/navigation'
import { NotebookResponse } from '@/lib/types/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { MoreHorizontal, Archive, ArchiveRestore, Trash2, FileText, StickyNote } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useUpdateNotebook } from '@/lib/hooks/use-notebooks'
import { NotebookDeleteDialog } from './NotebookDeleteDialog'
import { useState, type CSSProperties } from 'react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'
interface NotebookCardProps {
  notebook: NotebookResponse
  index?: number
}

export function NotebookCard({ notebook, index = 0 }: NotebookCardProps) {
  const { t, language } = useTranslation()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const router = useRouter()
  const updateNotebook = useUpdateNotebook()

  const handleArchiveToggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    updateNotebook.mutate({
      id: notebook.id,
      data: { archived: !notebook.archived }
    })
  }

  const handleCardClick = () => {
    router.push(`/notebooks/${encodeURIComponent(notebook.id)}`)
  }

  return (
    <>
      <Card
        className="group card-hover rise gap-0 overflow-hidden py-0"
        style={{ '--i': index } as CSSProperties}
        onClick={handleCardClick}
      >
        <CardHeader className="px-5 pb-0 pt-5">
          <div className="flex items-start justify-between">
            <span aria-hidden className="block h-2 w-2 rounded-[3px] bg-teal" />
            <span
              aria-hidden
              className="font-mono text-[11px] tabular-nums text-muted-foreground/60"
            >
              {String(index + 1).padStart(2, '0')}
            </span>
          </div>
          <div className="mt-1 flex items-start justify-between gap-2">
            <CardTitle className="min-w-0 flex-1 truncate font-display text-[17px] leading-snug">
              {notebook.name}
            </CardTitle>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="-mr-2 h-7 w-7 p-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
                  aria-label={t('common.actions')}
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                <DropdownMenuItem onClick={handleArchiveToggle}>
                  {notebook.archived ? (
                    <>
                      <ArchiveRestore className="h-4 w-4 mr-2" />
                      {t('notebooks.unarchive')}
                    </>
                  ) : (
                    <>
                      <Archive className="h-4 w-4 mr-2" />
                      {t('notebooks.archive')}
                    </>
                  )}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation()
                    setShowDeleteDialog(true)
                  }}
                  className="text-destructive"
                >
                  <Trash2 className="h-4 w-4 mr-2" />
                  {t('common.delete')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
          {notebook.archived && (
            <Badge variant="secondary" className="mt-1.5 w-fit ring-1 ring-inset ring-border">
              {t('notebooks.archived')}
            </Badge>
          )}
        </CardHeader>

        <CardContent className="flex flex-1 flex-col px-5 pb-5 pt-2">
          <CardDescription className="mb-3 line-clamp-2 text-sm">
            {notebook.description || t('chat.noDescription')}
          </CardDescription>

          <div className="mt-auto flex items-center justify-between gap-3 border-t pt-3 text-xs text-muted-foreground">
            <div className="flex items-center gap-4 font-mono tabular-nums">
              <span className="flex items-center gap-1.5">
                <FileText className="h-3 w-3" />
                <span>{notebook.source_count}</span>
              </span>
              <span className="flex items-center gap-1.5">
                <StickyNote className="h-3 w-3" />
                <span>{notebook.note_count}</span>
              </span>
            </div>
            <span className="truncate">
              {t('common.updated', { time: formatDistanceToNow(new Date(notebook.updated), {
                addSuffix: true,
                locale: getDateLocale(language)
              }) })}
            </span>
          </div>
        </CardContent>
      </Card>

      <NotebookDeleteDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        notebookId={notebook.id}
        notebookName={notebook.name}
      />
    </>
  )
}

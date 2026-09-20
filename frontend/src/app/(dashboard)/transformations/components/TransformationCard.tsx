'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { Trash2, Wand2, Edit } from 'lucide-react'
import { Transformation } from '@/lib/types/transformations'
import { useDeleteTransformation, useUpdateTransformation } from '@/lib/hooks/use-transformations'
import { useTranslation } from '@/lib/hooks/use-translation'

interface TransformationCardProps {
  transformation: Transformation
  onPlayground?: () => void
  onEdit?: () => void
}

export function TransformationCard({ transformation, onPlayground, onEdit }: TransformationCardProps) {
  const { t } = useTranslation()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const deleteTransformation = useDeleteTransformation()
  const updateTransformation = useUpdateTransformation()

  const handleDelete = () => {
    deleteTransformation.mutate(transformation.id)
    setShowDeleteDialog(false)
  }

  const handleToggleDefault = () => {
    updateTransformation.mutate({
      id: transformation.id,
      data: { apply_default: !transformation.apply_default },
    })
  }

  return (
    <>
      <div className="card-hover flex flex-col rounded-lg border bg-card p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="font-display text-[17px] font-semibold tracking-tight">{transformation.name}</h3>
            {transformation.description && (
              <p className="mt-1 text-sm text-muted-foreground">{transformation.description}</p>
            )}
          </div>
          <button
            type="button"
            className="switch"
            role="switch"
            aria-checked={transformation.apply_default}
            aria-label={t('transformations.suggestDefault')}
            disabled={updateTransformation.isPending}
            onClick={handleToggleDefault}
          />
        </div>

        <div className="mt-4 flex-1 rounded-md border bg-muted/60 p-3.5">
          <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground">
            {transformation.prompt}
          </pre>
        </div>

        <div className="mt-4 flex items-center justify-end gap-2 border-t border-border pt-3">
          {onEdit && (
            <Button variant="ghost" size="sm" onClick={onEdit}>
              <Edit className="h-4 w-4 mr-2" />
              {t('common.edit')}
            </Button>
          )}
          {onPlayground && (
            <Button variant="ghost" size="sm" onClick={onPlayground}>
              <Wand2 className="h-4 w-4 mr-2" />
              {t('transformations.playground')}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={() => setShowDeleteDialog(true)}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        title={t('sources.delete')}
        description={t('transformations.deleteConfirm')}
        confirmText={t('common.delete')}
        confirmVariant="destructive"
        onConfirm={handleDelete}
        isLoading={deleteTransformation.isPending}
      />
    </>
  )
}

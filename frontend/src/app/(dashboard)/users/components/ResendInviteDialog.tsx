'use client'

import { useEffect } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useUpdateUser } from '@/lib/hooks/use-admin'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { AdminUser } from '@/lib/types/admin'

const resendSchema = z.object({
  temp_password: z.string().min(8),
})

type ResendFormData = z.infer<typeof resendSchema>

interface ResendInviteDialogProps {
  /** null = closed. */
  user: AdminUser | null
  onOpenChange: (open: boolean) => void
}

/**
 * Resend invite (issue #5/T4): no SMTP exists, so resending means setting a
 * fresh temporary password that the admin re-shares out-of-band. The backend
 * revokes all sessions, exactly like a password reset.
 */
export function ResendInviteDialog({ user, onOpenChange }: ResendInviteDialogProps) {
  const { t } = useTranslation()
  const updateUser = useUpdateUser()
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isValid },
  } = useForm<ResendFormData>({
    resolver: zodResolver(resendSchema),
    mode: 'onChange',
    defaultValues: { temp_password: '' },
  })

  useEffect(() => {
    if (!user) {
      reset()
    }
  }, [user, reset])

  const closeDialog = () => onOpenChange(false)

  const onSubmit = async (data: ResendFormData) => {
    if (!user) return
    await updateUser.mutateAsync({ id: user.id, ...data })
    closeDialog()
  }

  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{t('users.resendDialogTitle')}</DialogTitle>
          <DialogDescription>
            {user ? t('users.resendDialogDesc', { name: user.display_name }) : ''}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="resend-password">{t('users.tempPasswordLabel')} *</Label>
            <Input
              id="resend-password"
              {...register('temp_password')}
              placeholder={t('users.tempPasswordPlaceholder')}
              autoComplete="new-password"
            />
            {errors.temp_password && (
              <p className="text-sm text-destructive">{errors.temp_password.message}</p>
            )}
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || updateUser.isPending}>
              {updateUser.isPending
                ? t('common.saving')
                : t('users.resendDialogSubmit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

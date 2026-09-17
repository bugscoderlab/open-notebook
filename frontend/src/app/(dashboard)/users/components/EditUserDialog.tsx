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
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useUpdateUser } from '@/lib/hooks/use-admin'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { AdminUser, TeamSummary, UserRole, UserStatus } from '@/lib/types/admin'

const editUserSchema = z.object({
  team_id: z.string().min(1),
  role: z.enum(['member', 'team_manager', 'ceo', 'admin']),
  status: z.enum(['invited', 'active', 'disabled']),
})

type EditUserFormData = z.infer<typeof editUserSchema>

interface EditUserDialogProps {
  /** null = closed. The dialog remounts per user so defaults re-apply. */
  user: AdminUser | null
  onOpenChange: (open: boolean) => void
  teams: TeamSummary[]
}

export function EditUserDialog({ user, onOpenChange, teams }: EditUserDialogProps) {
  const { t } = useTranslation()
  const updateUser = useUpdateUser()
  const {
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { isValid },
  } = useForm<EditUserFormData>({
    resolver: zodResolver(editUserSchema),
    mode: 'onChange',
    defaultValues: {
      team_id: '',
      role: 'member',
      status: 'active',
    },
  })

  useEffect(() => {
    if (user) {
      reset({
        team_id: user.team_id,
        role: user.role,
        status: user.status,
      })
    }
  }, [user, reset])

  const closeDialog = () => onOpenChange(false)

  const onSubmit = async (data: EditUserFormData) => {
    if (!user) return
    await updateUser.mutateAsync({ id: user.id, ...data })
    closeDialog()
  }

  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{t('users.editDialogTitle')}</DialogTitle>
          <DialogDescription>
            {user ? `${user.display_name} · ${user.email}` : ''}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label>{t('users.teamLabel')}</Label>
            <Select
              value={watch('team_id')}
              onValueChange={(value) => setValue('team_id', value, { shouldValidate: true })}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {teams.map((team) => (
                  <SelectItem key={team.id} value={team.id}>
                    {team.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>{t('users.roleLabel')}</Label>
              <Select
                value={watch('role')}
                onValueChange={(value) =>
                  setValue('role', value as UserRole, { shouldValidate: true })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="member">{t('users.role.member')}</SelectItem>
                  <SelectItem value="team_manager">
                    {t('users.role.team_manager')}
                  </SelectItem>
                  <SelectItem value="ceo">{t('users.role.ceo')}</SelectItem>
                  <SelectItem value="admin">{t('users.role.admin')}</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>{t('users.statusLabel')}</Label>
              <Select
                value={watch('status')}
                onValueChange={(value) =>
                  setValue('status', value as UserStatus, { shouldValidate: true })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">{t('users.status.active')}</SelectItem>
                  <SelectItem value="invited">{t('users.status.invited')}</SelectItem>
                  <SelectItem value="disabled">{t('users.status.disabled')}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {watch('status') === 'disabled' && (
            <p className="text-xs text-muted-foreground">{t('users.disableHint')}</p>
          )}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || updateUser.isPending}>
              {updateUser.isPending ? t('common.saving') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

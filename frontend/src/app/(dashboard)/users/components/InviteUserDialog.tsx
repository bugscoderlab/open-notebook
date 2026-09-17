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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useInviteUser } from '@/lib/hooks/use-admin'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TeamSummary, UserRole } from '@/lib/types/admin'

const inviteSchema = z.object({
  email: z.string().email(),
  display_name: z.string().min(1),
  team_id: z.string().min(1),
  role: z.enum(['member', 'team_manager', 'ceo', 'admin']),
  temp_password: z.string().min(8),
})

type InviteFormData = z.infer<typeof inviteSchema>

interface InviteUserDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  teams: TeamSummary[]
}

export function InviteUserDialog({ open, onOpenChange, teams }: InviteUserDialogProps) {
  const { t } = useTranslation()
  const inviteUser = useInviteUser()
  const {
    register,
    handleSubmit,
    setValue,
    watch,
    formState: { errors, isValid },
    reset,
  } = useForm<InviteFormData>({
    resolver: zodResolver(inviteSchema),
    mode: 'onChange',
    defaultValues: {
      email: '',
      display_name: '',
      team_id: '',
      role: 'member',
      temp_password: '',
    },
  })

  const closeDialog = () => onOpenChange(false)

  const onSubmit = async (data: InviteFormData) => {
    await inviteUser.mutateAsync(data)
    closeDialog()
    reset()
  }

  useEffect(() => {
    if (!open) {
      reset()
    }
  }, [open, reset])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{t('users.inviteDialogTitle')}</DialogTitle>
          <DialogDescription>{t('users.inviteDialogDesc')}</DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="invite-email">{t('users.emailLabel')} *</Label>
            <Input
              id="invite-email"
              type="email"
              {...register('email')}
              placeholder="name@company.com"
              autoComplete="off"
            />
            {errors.email && (
              <p className="text-sm text-destructive">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="invite-name">{t('users.displayNameLabel')} *</Label>
            <Input
              id="invite-name"
              {...register('display_name')}
              autoComplete="off"
            />
            {errors.display_name && (
              <p className="text-sm text-destructive">{errors.display_name.message}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>{t('users.teamLabel')} *</Label>
              <Select
                value={watch('team_id')}
                onValueChange={(value) => setValue('team_id', value, { shouldValidate: true })}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t('users.teamPlaceholder')} />
                </SelectTrigger>
                <SelectContent>
                  {teams.map((team) => (
                    <SelectItem key={team.id} value={team.id}>
                      {team.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {errors.team_id && (
                <p className="text-sm text-destructive">{errors.team_id.message}</p>
              )}
            </div>

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
          </div>

          <div className="space-y-2">
            <Label htmlFor="invite-password">{t('users.tempPasswordLabel')} *</Label>
            <Input
              id="invite-password"
              {...register('temp_password')}
              placeholder={t('users.tempPasswordPlaceholder')}
              autoComplete="new-password"
            />
            {errors.temp_password && (
              <p className="text-sm text-destructive">{errors.temp_password.message}</p>
            )}
            <p className="text-xs text-muted-foreground">{t('users.tempPasswordHint')}</p>
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || inviteUser.isPending}>
              {inviteUser.isPending ? t('common.creating') : t('users.inviteDialogSubmit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

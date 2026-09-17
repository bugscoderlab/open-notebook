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
import { Textarea } from '@/components/ui/textarea'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCreateTeam, useUpdateTeam, useUsers } from '@/lib/hooks/use-admin'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TeamSummary } from '@/lib/types/admin'

const teamFormSchema = z.object({
  slug: z
    .string()
    .min(1)
    .regex(/^[a-z0-9_-]+$/, 'slug format'),
  name: z.string().min(1),
  description: z.string().optional(),
  manager_id: z.string().optional(),
  active: z.boolean(),
})

type TeamFormData = z.infer<typeof teamFormSchema>

interface TeamFormDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** null = create mode; otherwise edit/archive mode. */
  team: TeamSummary | null
}

const NO_MANAGER = '__none__'

export function TeamFormDialog({ open, onOpenChange, team }: TeamFormDialogProps) {
  const { t } = useTranslation()
  const createTeam = useCreateTeam()
  const updateTeam = useUpdateTeam()
  const usersQuery = useUsers()
  const users = usersQuery.data ?? []
  const isEditing = team !== null
  const isPending = createTeam.isPending || updateTeam.isPending

  const {
    register,
    handleSubmit,
    setValue,
    watch,
    reset,
    formState: { errors, isValid },
  } = useForm<TeamFormData>({
    resolver: zodResolver(teamFormSchema),
    mode: 'onChange',
    defaultValues: {
      slug: '',
      name: '',
      description: '',
      manager_id: '',
      active: true,
    },
  })

  useEffect(() => {
    if (open) {
      reset({
        slug: team?.slug ?? '',
        name: team?.name ?? '',
        description: team?.description ?? '',
        manager_id: team?.manager_id || NO_MANAGER,
        active: team?.active ?? true,
      })
    }
  }, [open, team, reset])

  const closeDialog = () => onOpenChange(false)

  const onSubmit = async (data: TeamFormData) => {
    const managerId = data.manager_id && data.manager_id !== NO_MANAGER ? data.manager_id : null
    if (isEditing && team) {
      await updateTeam.mutateAsync({
        id: team.id,
        name: data.name,
        description: data.description || null,
        manager_id: managerId,
        active: data.active,
      })
    } else {
      await createTeam.mutateAsync({
        slug: data.slug,
        name: data.name,
        description: data.description || null,
      })
    }
    closeDialog()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>
            {isEditing ? t('teams.editDialogTitle') : t('teams.createDialogTitle')}
          </DialogTitle>
          <DialogDescription>
            {isEditing
              ? t('teams.editDialogDesc')
              : t('teams.createDialogDesc')}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="team-slug">{t('teams.slugLabel')} *</Label>
            <Input
              id="team-slug"
              {...register('slug')}
              placeholder={t('teams.slugPlaceholder')}
              disabled={isEditing}
              autoComplete="off"
            />
            {errors.slug ? (
              <p className="text-sm text-destructive">{errors.slug.message}</p>
            ) : (
              <p className="text-xs text-muted-foreground">{t('teams.slugHint')}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="team-name">{t('teams.nameLabel')} *</Label>
            <Input id="team-name" {...register('name')} autoComplete="off" />
            {errors.name && (
              <p className="text-sm text-destructive">{errors.name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="team-description">{t('teams.descriptionLabel')}</Label>
            <Textarea
              id="team-description"
              {...register('description')}
              rows={3}
            />
          </div>

          {isEditing && (
            <div className="space-y-2">
              <Label>{t('teams.managerFieldLabel')}</Label>
              <Select
                value={watch('manager_id')}
                onValueChange={(value) =>
                  setValue('manager_id', value, { shouldValidate: true })
                }
              >
                <SelectTrigger>
                  <SelectValue placeholder={t('teams.noManager')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_MANAGER}>{t('teams.noManager')}</SelectItem>
                  {users.map((user) => (
                    <SelectItem key={user.id} value={user.id}>
                      {user.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {isEditing && (
            <div className="flex items-center gap-2">
              <Checkbox
                id="team-active"
                checked={watch('active')}
                onCheckedChange={(checked) =>
                  setValue('active', checked === true, { shouldValidate: true })
                }
              />
              <Label htmlFor="team-active" className="font-normal">
                {t('teams.activeLabel')}
              </Label>
            </div>
          )}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={closeDialog}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!isValid || isPending}>
              {isPending
                ? t('common.saving')
                : isEditing
                  ? t('common.save')
                  : t('teams.createDialogSubmit')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

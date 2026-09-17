'use client'

import { useState } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { PageHead } from '@/components/shell/page-head'
import { Card } from '@/components/shell/card'
import { Pill } from '@/components/shell/pill'
import { StatusBadge } from '@/components/shell/status-badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { useTeams, useUsers } from '@/lib/hooks/use-admin'
import { TeamFormDialog } from './components/TeamFormDialog'
import { teamPillVariant } from '@/lib/utils/team'
import { Plus, ShieldCheck } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TeamSummary } from '@/lib/types/admin'

export default function TeamsPage() {
  const { t } = useTranslation()
  const teamsQuery = useTeams()
  // Loaded for the manager assignment dropdown inside the form dialog.
  useUsers()
  const teams = teamsQuery.data ?? []
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editingTeam, setEditingTeam] = useState<TeamSummary | null>(null)

  const openCreate = () => {
    setEditingTeam(null)
    setDialogOpen(true)
  }

  const openEdit = (team: TeamSummary) => {
    setEditingTeam(team)
    setDialogOpen(true)
  }

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-5xl">
            <PageHead
              className="mb-6"
              eyebrow={t('teams.eyebrow')}
              title={t('navigation.teams')}
              description={t('teams.description')}
              actions={
                <Button size="sm" onClick={openCreate}>
                  <Plus className="mr-2 h-4 w-4" />
                  {t('teams.create')}
                </Button>
              }
            />

            <Alert className="mb-6">
              <ShieldCheck className="h-4 w-4" />
              <AlertTitle>{t('teams.inheritedTitle')}</AlertTitle>
              <AlertDescription>{t('teams.inheritedDesc')}</AlertDescription>
            </Alert>

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {teams.map((team) => (
                <Card key={team.id} className="gap-3">
                  <div className="flex items-start justify-between">
                    <Pill variant={teamPillVariant(team.slug)}>{team.name}</Pill>
                    <div className="flex items-center gap-2">
                      {!team.active && <StatusBadge>{t('teams.archivedBadge')}</StatusBadge>}
                      <Button variant="link" size="sm" onClick={() => openEdit(team)}>
                        {t('teams.edit')}
                      </Button>
                    </div>
                  </div>
                  <div>
                    <h3 className="font-display text-base font-bold leading-tight">
                      {team.name}
                    </h3>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {team.description}
                    </p>
                  </div>
                  <div className="mt-auto flex items-center justify-between text-xs text-muted-foreground">
                    <span>
                      {t('teams.membersNotebooks', {
                        members: team.member_count,
                        notebooks: team.notebook_count,
                      })}
                    </span>
                    <span>
                      {team.manager_name
                        ? t('teams.managerLabel', { name: team.manager_name })
                        : t('teams.noManager')}
                    </span>
                  </div>
                </Card>
              ))}
              {teams.length === 0 && !teamsQuery.isLoading && (
                <p className="col-span-full py-10 text-center text-muted-foreground">
                  {t('teams.empty')}
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      <TeamFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        team={editingTeam}
      />
    </AppShell>
  )
}

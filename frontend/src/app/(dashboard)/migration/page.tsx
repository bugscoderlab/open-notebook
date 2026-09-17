'use client'

import { useState } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { PageHead } from '@/components/shell/page-head'
import { Card } from '@/components/shell/card'
import { StatusBadge } from '@/components/shell/status-badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  useAssignMigrationItem,
  useMigrationItems,
  useMigrationStatus,
  useRunClassification,
} from '@/lib/hooks/use-migration'
import { useTeams } from '@/lib/hooks/use-admin'
import { useTranslation } from '@/lib/hooks/use-translation'
import { CheckCircle2, ListChecks, Play } from 'lucide-react'
import type { TeamSummary } from '@/lib/types/admin'
import type {
  FlaggedNotebook,
  FlaggedSource,
  MigrationItemKind,
} from '@/lib/types/migration'

function AssignControls({
  kind,
  itemId,
  teams,
}: {
  kind: MigrationItemKind
  itemId: string
  teams: TeamSummary[]
}) {
  const { t } = useTranslation()
  const assign = useAssignMigrationItem()
  const [teamId, setTeamId] = useState('')
  const [visibility, setVisibility] = useState<'team' | 'company_shared'>('team')

  return (
    <div className="flex items-center gap-2">
      <Select value={teamId} onValueChange={setTeamId}>
        <SelectTrigger className="w-40" aria-label={t('migration.assignTeamLabel')}>
          <SelectValue placeholder={t('migration.assignTeamPlaceholder')} />
        </SelectTrigger>
        <SelectContent>
          {teams.map((team) => (
            <SelectItem key={team.id} value={team.id}>
              {team.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={visibility}
        onValueChange={(value) => setVisibility(value as 'team' | 'company_shared')}
      >
        <SelectTrigger className="w-40" aria-label={t('migration.assignVisibilityLabel')}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="team">{t('migration.visibilityTeam')}</SelectItem>
          <SelectItem value="company_shared">
            {t('migration.visibilityCompanyShared')}
          </SelectItem>
        </SelectContent>
      </Select>
      <Button
        size="sm"
        disabled={!teamId || assign.isPending}
        onClick={() =>
          assign.mutate({ kind, id: itemId, team_id: teamId, visibility })
        }
      >
        {t('migration.assignAction')}
      </Button>
    </div>
  )
}

const REASON_KEYS: Record<string, string> = {
  ambiguous_tokens: 'migration.reason.ambiguous_tokens',
  mixed_team_sources: 'migration.reason.mixed_team_sources',
  cross_team_link: 'migration.reason.cross_team_link',
}

function ReasonBadge({ reason }: { reason: string }) {
  const { t } = useTranslation()
  return <StatusBadge>{t(REASON_KEYS[reason] ?? reason)}</StatusBadge>
}

interface FlaggedItem {
  id: string
  reason: string
  linked_teams: string[]
  label: string
}

function FlaggedItemList({
  title,
  items,
  kind,
  teams,
  emptyText,
}: {
  title: string
  items: FlaggedItem[]
  kind: MigrationItemKind
  teams: TeamSummary[]
  emptyText: string
}) {
  return (
    <Card className="gap-3">
      <h3 className="font-display text-base font-bold leading-tight">{title}</h3>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="divide-y">
          {items.map((item) => (
            <li
              key={item.id}
              className="flex flex-wrap items-center justify-between gap-3 py-3"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{item.label}</span>
                  <ReasonBadge reason={item.reason} />
                </div>
                {item.linked_teams.length > 0 && (
                  <p className="text-xs text-muted-foreground">
                    <LinkedTeams teams={item.linked_teams} />
                  </p>
                )}
              </div>
              <AssignControls kind={kind} itemId={item.id} teams={teams} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function LinkedTeams({ teams: teamNames }: { teams: string[] }) {
  const { t } = useTranslation()
  return <>{t('migration.linkedTeams', { teams: teamNames.join(', ') })}</>
}

export default function MigrationPage() {
  const { t } = useTranslation()
  const statusQuery = useMigrationStatus()
  const itemsQuery = useMigrationItems()
  const teamsQuery = useTeams()
  const runClassification = useRunClassification()

  const status = statusQuery.data
  const items = itemsQuery.data ?? { notebooks: [], sources: [] }
  const teams = teamsQuery.data ?? []
  const flaggedTotal = items.notebooks.length + items.sources.length

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-5xl">
            <PageHead
              className="mb-6"
              eyebrow={t('migration.eyebrow')}
              title={t('navigation.migration')}
              description={t('migration.description')}
              actions={
                <Button
                  size="sm"
                  onClick={() => runClassification.mutate()}
                  disabled={runClassification.isPending}
                >
                  <Play className="mr-2 h-4 w-4" />
                  {t('migration.runAction')}
                </Button>
              }
            />

            {status?.completed ? (
              <Alert className="mb-6">
                <CheckCircle2 className="h-4 w-4" />
                <AlertTitle>{t('migration.completedTitle')}</AlertTitle>
                <AlertDescription>
                  {t('migration.completedDesc')}
                </AlertDescription>
              </Alert>
            ) : (
              <Alert className="mb-6">
                <ListChecks className="h-4 w-4" />
                <AlertTitle>{t('migration.pendingTitle')}</AlertTitle>
                <AlertDescription>
                  {t('migration.pendingDesc')}
                </AlertDescription>
              </Alert>
            )}

            <div className="space-y-6">
              <FlaggedItemList
                title={t('migration.flaggedSources')}
                items={items.sources.map((source: FlaggedSource) => ({
                  id: source.id,
                  reason: source.reason,
                  linked_teams: source.linked_teams,
                  label: source.title,
                }))}
                kind="source"
                teams={teams}
                emptyText={t('migration.emptySection')}
              />

              <FlaggedItemList
                title={t('migration.flaggedNotebooks')}
                items={items.notebooks.map((notebook: FlaggedNotebook) => ({
                  id: notebook.id,
                  reason: notebook.reason,
                  linked_teams: notebook.linked_teams,
                  label: notebook.name,
                }))}
                kind="notebook"
                teams={teams}
                emptyText={t('migration.emptySection')}
              />

              {status && !status.completed && flaggedTotal === 0 && (
                <p className="text-sm text-muted-foreground">
                  {t('migration.nothingFlagged')}
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  )
}

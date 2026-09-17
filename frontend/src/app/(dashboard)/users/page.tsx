'use client'

import { useState } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { PageHead } from '@/components/shell/page-head'
import { Stat } from '@/components/shell/stat'
import { Toolbar } from '@/components/shell/toolbar'
import { Panel } from '@/components/shell/panel'
import { Pill } from '@/components/shell/pill'
import { StatusBadge } from '@/components/shell/status-badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAdminUsersPage } from './components/use-admin-users-page'
import { InviteUserDialog } from './components/InviteUserDialog'
import { EditUserDialog } from './components/EditUserDialog'
import { ResendInviteDialog } from './components/ResendInviteDialog'
import { UserPlus } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { TFunction } from 'i18next'
import { teamPillVariant } from '@/lib/utils/team'
import type { AdminUser } from '@/lib/types/admin'

/** Compact relative label for last activity ("Now", "12 min ago", ...). */
function formatLastActive(iso: string | null, t: TFunction): string {
  if (!iso) return t('users.lastActiveNever')
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return t('users.lastActiveNever')
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (seconds < 60) return t('users.lastActiveNow')
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return t('users.lastActiveMinutes', { count: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('users.lastActiveHours', { count: hours })
  const days = Math.floor(hours / 24)
  if (days < 30) return t('users.lastActiveDays', { count: days })
  return new Date(then).toLocaleDateString()
}

export default function UsersPage() {
  const { t } = useTranslation()
  const page = useAdminUsersPage()
  const [inviteOpen, setInviteOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null)
  const [resendingUser, setResendingUser] = useState<AdminUser | null>(null)

  const allAccessCount = page.users.filter(
    (u) => u.role === 'ceo' || u.role === 'admin'
  ).length

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-5xl">
            <PageHead
              className="mb-6"
              eyebrow={t('users.eyebrow')}
              title={t('navigation.users')}
              description={t('users.description')}
              actions={
                <Button size="sm" onClick={() => setInviteOpen(true)}>
                  <UserPlus className="mr-2 h-4 w-4" />
                  {t('users.invite')}
                </Button>
              }
            />

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label={t('users.statTotal')} value={page.users.length} />
              <Stat
                label={t('users.statActive')}
                value={page.users.filter((u) => u.status === 'active').length}
              />
              <Stat
                label={t('users.statInvited')}
                value={page.users.filter((u) => u.status === 'invited').length}
              />
              <Stat label={t('users.statAllAccess')} value={allAccessCount} />
            </div>

            <Toolbar>
              <Input
                className="max-w-xs"
                placeholder={t('users.searchPlaceholder')}
                value={page.search}
                onChange={(event) => page.setSearch(event.target.value)}
              />
              <Select value={page.teamFilter} onValueChange={page.setTeamFilter}>
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('users.filterAllTeams')}</SelectItem>
                  {page.teams.map((team) => (
                    <SelectItem key={team.id} value={team.id}>
                      {team.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={page.statusFilter} onValueChange={page.setStatusFilter}>
                <SelectTrigger className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('users.filterAllStatuses')}</SelectItem>
                  <SelectItem value="active">{t('users.status.active')}</SelectItem>
                  <SelectItem value="invited">{t('users.status.invited')}</SelectItem>
                  <SelectItem value="disabled">{t('users.status.disabled')}</SelectItem>
                </SelectContent>
              </Select>
            </Toolbar>

            <Panel className="p-0">
              <table className="w-full min-w-[860px]">
                <thead>
                  <tr className="border-b">
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colUser')}
                    </th>
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colTeam')}
                    </th>
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colRole')}
                    </th>
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colAccess')}
                    </th>
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colStatus')}
                    </th>
                    <th className="h-11 px-4 text-left align-middle text-[9px] font-extrabold uppercase tracking-[0.07em] text-muted-foreground">
                      {t('users.colLastActive')}
                    </th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {page.filteredUsers.map((user) => (
                    <tr key={user.id} className="border-b last:border-0">
                      <td className="px-4 py-3 align-middle">
                        <div className="font-medium">{user.display_name}</div>
                        <div className="text-xs text-muted-foreground">{user.email}</div>
                      </td>
                      <td className="px-4 py-3 align-middle">
                        {user.team_name && (
                          <Pill variant={teamPillVariant(page.teamSlug(user.team_id))}>
                            {user.team_name}
                          </Pill>
                        )}
                      </td>
                      <td className="px-4 py-3 align-middle">{t(`users.role.${user.role}`)}</td>
                      <td className="px-4 py-3 align-middle text-muted-foreground">
                        {user.role === 'ceo' || user.role === 'admin'
                          ? t('users.accessAll')
                          : t('users.accessTeamCompany', { team: user.team_name })}
                      </td>
                      <td className="px-4 py-3 align-middle">
                        <StatusBadge
                          className={
                            user.status === 'disabled'
                              ? 'bg-destructive/10 text-destructive'
                              : user.status === 'invited'
                                ? 'bg-gold/20 text-gold-foreground'
                                : undefined
                          }
                        >
                          {t(`users.status.${user.status}`)}
                        </StatusBadge>
                      </td>
                      <td className="px-4 py-3 align-middle text-muted-foreground">
                        {formatLastActive(user.last_active_at, t)}
                      </td>
                      <td className="px-4 py-3 align-middle text-right">
                        {user.status === 'invited' && (
                          <Button
                            variant="link"
                            size="sm"
                            onClick={() => setResendingUser(user)}
                          >
                            {t('users.resend')}
                          </Button>
                        )}
                        <Button
                          variant="link"
                          size="sm"
                          onClick={() => setEditingUser(user)}
                        >
                          {t('users.edit')}
                        </Button>
                      </td>
                    </tr>
                  ))}
                  {page.filteredUsers.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-muted-foreground">
                        {t('users.empty')}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </Panel>
          </div>
        </div>
      </div>

      <InviteUserDialog open={inviteOpen} onOpenChange={setInviteOpen} teams={page.teams} />
      <EditUserDialog
        user={editingUser}
        onOpenChange={(open) => {
          if (!open) setEditingUser(null)
        }}
        teams={page.teams}
      />
      <ResendInviteDialog
        user={resendingUser}
        onOpenChange={(open) => {
          if (!open) setResendingUser(null)
        }}
      />
    </AppShell>
  )
}

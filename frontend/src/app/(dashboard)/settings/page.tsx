'use client'

import { AppShell } from '@/components/layout/AppShell'
import { PageHead } from '@/components/shell/page-head'
import { SettingsForm } from './components/SettingsForm'
import { useSettings } from '@/lib/hooks/use-settings'
import { Button } from '@/components/ui/button'
import { RefreshCw } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'

export default function SettingsPage() {
  const { t } = useTranslation()
  const { refetch } = useSettings()

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <div className="p-6">
          <div className="max-w-4xl">
            <PageHead
              className="mb-6"
              title={t('navigation.settings')}
              actions={
                <Button variant="outline" size="sm" onClick={() => refetch()}>
                  <RefreshCw className="h-4 w-4" />
                </Button>
              }
            />

            <SettingsForm />
          </div>
        </div>
      </div>
    </AppShell>
  )
}

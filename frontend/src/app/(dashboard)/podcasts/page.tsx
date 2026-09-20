'use client'

import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { PageContainer } from '@/components/layout/page-container'
import { PageHead } from '@/components/shell/page-head'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { EpisodesTab } from '@/components/podcasts/EpisodesTab'
import { TemplatesTab } from '@/components/podcasts/TemplatesTab'
import { GeneratePodcastDialog } from '@/components/podcasts/GeneratePodcastDialog'
import { Mic, LayoutTemplate } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useEpisodeProfiles, useSpeakerProfiles } from '@/lib/hooks/use-podcasts'
import { needsModelSetup } from '@/lib/types/podcasts'

export default function PodcastsPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'episodes' | 'templates'>('episodes')
  const [showGenerateDialog, setShowGenerateDialog] = useState(false)

  const { episodeProfiles } = useEpisodeProfiles()
  const { speakerProfiles } = useSpeakerProfiles(episodeProfiles)

  const hasUnconfiguredProfiles = useMemo(() => {
    return episodeProfiles.some(needsModelSetup) || speakerProfiles.some(needsModelSetup)
  }, [episodeProfiles, speakerProfiles])

  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <PageContainer className="space-y-6">
          <PageHead
            title={t('podcasts.listTitle')}
            description={t('podcasts.listDesc')}
            actions={
              <Button onClick={() => setShowGenerateDialog(true)}>
                {t('podcasts.generateBtn')}
              </Button>
            }
          />

          {hasUnconfiguredProfiles ? (
            <Alert className="bg-warn-tint text-warn border-warn/30">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t('podcasts.setupRequired')}</AlertTitle>
              <AlertDescription>
                {t('podcasts.setupRequiredDesc')}
              </AlertDescription>
            </Alert>
          ) : null}

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as 'episodes' | 'templates')}
            className="space-y-6"
          >
            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t('podcasts.chooseAView')}</p>
              <TabsList aria-label={t('common.accessibility.podcastViews')} className="w-full max-w-md">
                <TabsTrigger value="episodes">
                  <Mic className="h-4 w-4" />
                  {t('podcasts.episodesTab')}
                </TabsTrigger>
                <TabsTrigger value="templates">
                  <LayoutTemplate className="h-4 w-4" />
                  {t('podcasts.templatesTab')}
                </TabsTrigger>
              </TabsList>
            </div>

            <TabsContent value="episodes">
              <EpisodesTab />
            </TabsContent>

            <TabsContent value="templates">
              <TemplatesTab />
            </TabsContent>
          </Tabs>
        </PageContainer>
      </div>

      <GeneratePodcastDialog
        open={showGenerateDialog}
        onOpenChange={setShowGenerateDialog}
      />
    </AppShell>
  )
}

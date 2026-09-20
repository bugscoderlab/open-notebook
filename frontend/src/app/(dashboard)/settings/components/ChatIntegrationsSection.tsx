'use client'

import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, Loader2, MessageCircle, Plus, RefreshCw, Send, Trash2 } from 'lucide-react'

import { SectionCard } from '@/components/shell/section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Progress } from '@/components/ui/progress'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  IntegrationLink,
  IntegrationPlatform,
  useCreateIntegrationLink,
  useDeleteIntegrationLink,
  useIntegrationLinks,
} from '@/lib/hooks/use-integrations'

const CODE_TTL_SECONDS = 10 * 60

const PLATFORM_META: Record<
  IntegrationPlatform,
  { nameKey: string; icon: typeof Send; botRefKey: string }
> = {
  telegram: {
    nameKey: 'chatIntegrations.platforms.telegram',
    icon: Send,
    botRefKey: 'chatIntegrations.telegramBotRef',
  },
  whatsapp: {
    nameKey: 'chatIntegrations.platforms.whatsapp',
    icon: MessageCircle,
    botRefKey: 'chatIntegrations.whatsappBotRef',
  },
}

function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function PlatformIcon({ platform, className }: { platform: IntegrationPlatform; className?: string }) {
  const Icon = PLATFORM_META[platform].icon
  return <Icon className={className} />
}

/** Dialog state: which platform we're connecting, and the issued code. */
interface ConnectState {
  platform: IntegrationPlatform
  code: string
  expiresAt: number
}

export function ChatIntegrationsSection() {
  const { t } = useTranslation()
  const { toast } = useToast()
  const linksQuery = useIntegrationLinks()
  const createLink = useCreateIntegrationLink()
  const deleteLink = useDeleteIntegrationLink()

  const [connecting, setConnecting] = useState<ConnectState | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [managePlatform, setManagePlatform] = useState<IntegrationPlatform | null>(null)

  const links = useMemo(() => linksQuery.data ?? [], [linksQuery.data])

  const secondsLeft = connecting
    ? Math.max(0, Math.floor((connecting.expiresAt - now) / 1000))
    : 0
  const expired = connecting !== null && secondsLeft === 0

  // Countdown tick while the dialog is open.
  useEffect(() => {
    if (!connecting) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [connecting])

  // While waiting, poll for the claimed link and close on success.
  const { refetch } = linksQuery
  useEffect(() => {
    if (!connecting || expired) return
    const platform = connecting.platform
    const timer = setInterval(() => {
      refetch().then((result) => {
        const found = (result.data ?? []).some((link) => link.platform === platform)
        if (found) {
          clearInterval(timer)
          setConnecting(null)
          toast({
            title: t('common.success'),
            description: t('chatIntegrations.linkedSuccess'),
          })
        }
      })
    }, 3000)
    return () => clearInterval(timer)
  }, [connecting, expired, refetch, t, toast])

  const startConnect = async (platform: IntegrationPlatform) => {
    try {
      const issued = await createLink.mutateAsync(platform)
      setNow(Date.now())
      setConnecting({
        platform,
        code: issued.code,
        expiresAt: new Date(issued.expires_at).getTime(),
      })
    } catch {
      // Error toast is shown by the hook's onError.
    }
  }

  const platformLinks = (platform: IntegrationPlatform): IntegrationLink[] =>
    links.filter((link) => link.platform === platform)

  const renderIdentityCard = (link: IntegrationLink) => (
    <div
      key={link.id}
      className="flex items-center justify-between rounded-md border bg-card px-3 py-2"
    >
      <div className="text-sm">
        <span className="font-medium">{link.identity}</span>
        <span className="ml-2 text-muted-foreground">
          {t('chatIntegrations.linkedOn', {
            date: new Date(link.linked_at).toLocaleDateString(),
          })}
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        disabled={deleteLink.isPending}
        onClick={() => deleteLink.mutate(link.id)}
      >
        <Trash2 className="mr-1 h-4 w-4" /> {t('chatIntegrations.unlink')}
      </Button>
    </div>
  )

  return (
    <SectionCard
      title={
        <>
          {t('chatIntegrations.title')}
          <span className="mt-1 block font-sans text-sm font-normal tracking-normal text-muted-foreground">
            {t('chatIntegrations.description')}
          </span>
        </>
      }
    >
      <div className="divide-y">
        {(Object.keys(PLATFORM_META) as IntegrationPlatform[]).map((platform) => {
          const meta = PLATFORM_META[platform]
          const connected = platformLinks(platform)
          const isConnected = connected.length > 0
          const managing = managePlatform === platform
          return (
            <div key={platform} className="px-5 py-4">
              <div className="flex items-center gap-4">
                <div className="flex h-10 w-10 items-center justify-center rounded-md border bg-muted/50">
                  <PlatformIcon platform={platform} className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{t(meta.nameKey)}</span>
                    <Badge variant={isConnected ? 'default' : 'secondary'}>
                      {isConnected
                        ? t('chatIntegrations.connected')
                        : t('chatIntegrations.notConnected')}
                    </Badge>
                  </div>
                  <p className="truncate text-sm text-muted-foreground">
                    {isConnected
                      ? t('chatIntegrations.linkedCount', { count: connected.length })
                      : t('chatIntegrations.notConnectedDesc', { platform: t(meta.nameKey) })}
                  </p>
                </div>
                {isConnected && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setManagePlatform(managing ? null : platform)}
                  >
                    {managing ? t('chatIntegrations.close') : t('chatIntegrations.manage')}
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  disabled={createLink.isPending}
                  onClick={() => startConnect(platform)}
                >
                  <Plus className="mr-1 h-4 w-4" /> {t('chatIntegrations.connect')}
                </Button>
              </div>

              {managing && (
                <div className="mt-3 space-y-2 border-l-2 pl-4">
                  {connected.map(renderIdentityCard)}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Dialog
        open={connecting !== null}
        onOpenChange={(open) => {
          if (!open) setConnecting(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {connecting &&
                t('chatIntegrations.connectTitle', {
                  platform: t(PLATFORM_META[connecting.platform].nameKey),
                })}
            </DialogTitle>
            <DialogDescription>{t('chatIntegrations.connectDesc')}</DialogDescription>
          </DialogHeader>

          {connecting && (
            <div className="space-y-5 py-2">
              <ol className="space-y-2 text-sm text-muted-foreground">
                <li>
                  {t('chatIntegrations.stepSend', {
                    bot: t(PLATFORM_META[connecting.platform].botRefKey),
                  })}
                </li>
                <li>{t('chatIntegrations.stepWait')}</li>
              </ol>

              <div className="rounded-lg border bg-muted/30 py-5">
                {expired ? (
                  <div className="space-y-3 px-4 text-center">
                    <p className="text-sm text-destructive">
                      {t('chatIntegrations.codeExpired')}
                    </p>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => startConnect(connecting.platform)}
                    >
                      <RefreshCw className="mr-1 h-4 w-4" />{' '}
                      {t('chatIntegrations.generateNew')}
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-4 px-4">
                    <CodeBlock code={connecting.code} copiedLabel={t('chatIntegrations.copied')} />
                    <p className="text-center text-sm text-muted-foreground">
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                        /start {connecting.code}
                      </span>
                    </p>
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>
                          {t('chatIntegrations.expiresIn', {
                            time: formatCountdown(secondsLeft),
                          })}
                        </span>
                      </div>
                      <Progress value={(secondsLeft / CODE_TTL_SECONDS) * 100} />
                    </div>
                    <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />{' '}
                      {t('chatIntegrations.waiting')}
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={() => setConnecting(null)}>
              {t('common.cancel')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  )
}

function CodeBlock({ code, copiedLabel }: { code: string; copiedLabel: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-center justify-center gap-3">
      <span className="font-mono text-3xl font-semibold tracking-[0.35em]">{code}</span>
      <Button
        size="icon"
        variant="ghost"
        aria-label={copiedLabel}
        onClick={() => {
          navigator.clipboard?.writeText(code).catch(() => {})
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check className="h-4 w-4 text-green-600" /> : <Copy className="h-4 w-4" />}
      </Button>
    </div>
  )
}

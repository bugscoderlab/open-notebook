'use client'

import { useState, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/lib/hooks/use-auth'
import { getConfig } from '@/lib/config'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { AlertCircle } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useTranslation } from '@/lib/hooks/use-translation'

// Dev-only login shortcuts (admin `dev-seed` personas). Rendered only in
// development builds — never ship these credentials to production.
const DEV_ACCOUNTS = [
  { email: 'aisha@company.com', password: 'password', label: 'Aisha · HR member' },
  { email: 'daniel@company.com', password: 'password', label: 'Daniel · Finance manager' },
  { email: 'mei@company.com', password: 'password', label: 'Mei · CEO' },
  { email: 'alex@company.com', password: 'password', label: 'Alex · Admin' },
]

export function LoginForm() {
  const { t, language } = useTranslation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const {
    login,
    isSubmitting,
    error,
    errorCode,
    authEnabled,
    isAuthenticated,
  } = useAuth()
  const [configInfo, setConfigInfo] = useState<{ apiUrl: string; version: string; buildTime: string } | null>(null)
  const router = useRouter()

  // Load config info for debugging
  useEffect(() => {
    getConfig().then(cfg => {
      setConfigInfo({
        apiUrl: cfg.apiUrl,
        version: cfg.version,
        buildTime: cfg.buildTime,
      })
    }).catch(err => {
      console.error('Failed to load config:', err)
    })
  }, [])

  // Open mode (no users seeded): nothing to sign in against — go to the app.
  useEffect(() => {
    if (authEnabled === false || (authEnabled === true && isAuthenticated)) {
      router.push('/notebooks')
    }
  }, [authEnabled, isAuthenticated, router])

  // Show a spinner only while the auth probe is still running cleanly, or a
  // login request is in flight. A probe that FAILED (error set) must fall
  // through to the connection-error card below — never spin forever.
  if ((authEnabled === null && !error) || isSubmitting) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <LoadingSpinner />
      </div>
    )
  }

  // The API was unreachable.
  if (error && (errorCode === 'network' || errorCode === 'server')) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader className="text-center">
            <CardTitle>{t('common.connectionError')}</CardTitle>
            <CardDescription>
              {t('common.unableToConnect')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="flex items-start gap-2 text-destructive text-sm">
                <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  {t('auth.connectErrorHint')}
                </div>
              </div>

              {configInfo && (
                <div className="space-y-2 text-xs text-muted-foreground border-t pt-3">
                  <div className="font-medium">{t('common.diagnosticInfo')}:</div>
                  <div className="space-y-1 font-mono">
                    <div>{t('common.version')}: {configInfo.version}</div>
                    <div>{t('common.built')}: {new Date(configInfo.buildTime).toLocaleString(language === 'zh-CN' ? 'zh-CN' : language === 'zh-TW' ? 'zh-TW' : 'en-US')}</div>
                    <div className="break-all">{t('common.apiUrl')}: {configInfo.apiUrl}</div>
                    <div className="break-all">{t('common.frontendUrl')}: {typeof window !== 'undefined' ? window.location.href : 'N/A'}</div>
                  </div>
                  <div className="text-xs pt-2">
                    {t('common.checkConsoleLogs')}
                  </div>
                </div>
              )}

              <Button
                onClick={() => window.location.reload()}
                className="w-full"
              >
                {t('common.retryConnection')}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (email.trim() && password) {
      try {
        await login(email.trim(), password)
      } catch (error) {
        console.error('Unhandled error during login:', error)
      }
    }
  }

  // Server messages are English generics; map stable codes to locale strings.
  // 'unknown' falls back to the shared generic error — raw server detail is
  // never rendered as UI text (i18n rule).
  const errorMessage =
    errorCode === 'invalid_credentials'
      ? t('auth.invalidCredentials')
      : errorCode === 'rate_limited'
        ? t('auth.tooManyAttempts')
        : errorCode === 'migration_pending'
          ? t('auth.migrationPending')
          : errorCode === 'unknown'
            ? t('errors.genericError')
            : null

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle>{t('auth.loginTitle')}</CardTitle>
          <CardDescription>
            {t('auth.loginDesc')}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Input
                type="email"
                autoComplete="username"
                placeholder={t('auth.emailPlaceholder')}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
              />
            </div>
            <div>
              <Input
                type="password"
                autoComplete="current-password"
                placeholder={t('auth.passwordPlaceholder')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={isSubmitting}
              />
            </div>

            {errorMessage && (
              <div className="flex items-center gap-2 text-destructive text-sm">
                <AlertCircle className="h-4 w-4" />
                {errorMessage}
              </div>
            )}

            <Button
              type="submit"
              className="w-full"
              disabled={isSubmitting || !email.trim() || !password}
            >
              {isSubmitting ? t('auth.signingIn') : t('auth.signIn')}
            </Button>

            {process.env.NODE_ENV === 'development' && (
              <div className="text-left border-t pt-3 space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  {t('auth.devAccountsTitle')}
                </div>
                <div className="flex flex-wrap gap-2">
                  {DEV_ACCOUNTS.map((account) => (
                    <button
                      key={account.email}
                      type="button"
                      onClick={() => {
                        setEmail(account.email)
                        setPassword(account.password)
                      }}
                      disabled={isSubmitting}
                      className="text-xs px-2.5 py-1.5 rounded-md border bg-muted/50 hover:bg-muted transition-colors disabled:opacity-50"
                      title={account.email}
                    >
                      {account.label}
                    </button>
                  ))}
                </div>
                <div className="text-[10px] text-muted-foreground">
                  {t('auth.devAccountsHint')}
                </div>
              </div>
            )}

            {configInfo && (
              <div className="text-xs text-center text-muted-foreground pt-2 border-t">
                <div>{t('common.version')} {configInfo.version}</div>
                <div className="font-mono text-[10px]">{configInfo.apiUrl}</div>
              </div>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/hooks/use-auth'
import { useMediaQuery } from '@/lib/hooks/use-media-query'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ThemeToggle } from '@/components/common/ThemeToggle'
import { LanguageToggle } from '@/components/common/LanguageToggle'
import { AccessGuard } from '@/components/shell/access-guard'
import { useCurrentUser } from '@/lib/hooks/use-current-user'
import type { TFunction } from 'i18next'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  Home,
  Book,
  Search,
  Mic,
  Bot,
  Shuffle,
  Settings,
  LogOut,
  ChevronLeft,
  Menu,
  FileText,
  Plus,
  Wrench,
  Command,
  Users,
  UsersRound,
  FolderCog,
} from 'lucide-react'

// Catalog shell (01-catalog.html): same sections and role rules as before,
// but the chrome follows the "Quiet Green" catalog prototype — a recessed
// light sidebar (bg-deep) with ink text, a fern active indicator, and
// ring-inset hairlines instead of the old dark green nav palette.
//
// Role awareness (T4): `requiresAdmin` items (Users/Teams) render only for
// admins; `hideFromNonAdmins` items (Advanced) render for admins and in open
// mode (no users seeded yet — there is no non-admin to hide it from). The
// backend enforces 403 independently of these flags.
type NavItem = {
  name: string
  href: string
  icon: React.ComponentType<{ className?: string }>
  iconClass?: string
  requiresAdmin?: boolean
  hideFromNonAdmins?: boolean
}

const getNavigation = (t: TFunction): { title: string; items: NavItem[] }[] => [
  {
    title: t('navigation.knowledge'),
    items: [
      { name: t('navigation.home'), href: '/home', icon: Home, iconClass: undefined },
      { name: t('navigation.notebooks'), href: '/notebooks', icon: Book, iconClass: undefined },
      { name: t('navigation.sources'), href: '/sources', icon: FileText, iconClass: undefined },
      { name: t('navigation.askAndSearch'), href: '/search', icon: Search, iconClass: undefined },
    ],
  },
  {
    title: t('navigation.create'),
    items: [
      { name: t('navigation.podcasts'), href: '/podcasts', icon: Mic, iconClass: undefined },
      { name: t('navigation.transformations'), href: '/transformations', icon: Shuffle, iconClass: undefined },
      { name: t('navigation.advanced'), href: '/advanced', icon: Wrench, iconClass: undefined, hideFromNonAdmins: true },
    ],
  },
  {
    title: t('navigation.system'),
    items: [
      { name: t('navigation.users'), href: '/users', icon: Users, iconClass: undefined, requiresAdmin: true },
      { name: t('navigation.teams'), href: '/teams', icon: UsersRound, iconClass: undefined, requiresAdmin: true },
      { name: t('navigation.migration'), href: '/migration', icon: FolderCog, iconClass: undefined, requiresAdmin: true },
      { name: t('navigation.settings'), href: '/settings', icon: Settings, iconClass: undefined },
      { name: t('navigation.models'), href: '/settings/models', icon: Bot, iconClass: undefined },
    ],
  },
]

// The tri-hue mark recomposed in the owned palette: fern / gold / teal.
function LogoPebbles({ className }: { className?: string }) {
  return (
    <span className={cn('flex items-center gap-[3px]', className)} aria-hidden="true">
      <span className="size-[9px] rounded-[3px] bg-fern" />
      <span className="size-[9px] rounded-[3px] bg-gold" />
      <span className="size-[9px] rounded-[3px] bg-teal" />
    </span>
  )
}

type CreateTarget = 'source' | 'notebook' | 'podcast'

export function AppSidebar() {
  const { t } = useTranslation()
  const pathname = usePathname()
  const { logout } = useAuth()
  const { user } = useCurrentUser()
  const isAdmin = user?.role === 'admin'
  // T4 role-aware nav: admins see everything; non-admins never see
  // Users/Teams; Advanced additionally stays visible in open mode (no
  // identity yet — there is no non-admin to hide it from).
  const isItemVisible = (item: NavItem) => {
    if (item.requiresAdmin) return isAdmin
    if (item.hideFromNonAdmins) return isAdmin || user === null
    return true
  }
  const navigation = getNavigation(t).map((section) => ({
    ...section,
    items: section.items.filter(isItemVisible),
  }))
  const { isCollapsed, toggleCollapse } = useSidebarStore()
  const { openSourceDialog, openNotebookDialog, openPodcastDialog } = useCreateDialogs()
  // Prototype shell (T2): at ≤950px the sidebar becomes a 76px icon rail,
  // regardless of the stored collapse preference.
  const isRail = useMediaQuery('(max-width: 950px)')
  const showCollapsed = isCollapsed || isRail

  // The active item is the longest href that prefixes the current path.
  // Longest-wins keeps `/settings` from also highlighting on `/settings/models`
  // (the Models page is a URL child of the Settings page but a distinct item).
  // The Home item (`/`) only matches an exact path, never as a prefix.
  const activeHref = navigation
    .map((section) => section.items)
    .flat()
    .filter((item) => pathname === item.href || pathname?.startsWith(`${item.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0]?.href

  const [createMenuOpen, setCreateMenuOpen] = useState(false)
  const [isMac, setIsMac] = useState(true) // Default to Mac for SSR

  // Detect platform for keyboard shortcut display
  useEffect(() => {
    setIsMac(navigator.platform.toLowerCase().includes('mac'))
  }, [])

  const handleCreateSelection = (target: CreateTarget) => {
    setCreateMenuOpen(false)

    if (target === 'source') {
      openSourceDialog()
    } else if (target === 'notebook') {
      openNotebookDialog()
    } else if (target === 'podcast') {
      openPodcastDialog()
    }
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div
        className={cn(
          'app-sidebar flex h-full flex-col border-r border-sidebar-border bg-sidebar transition-all duration-300',
          showCollapsed ? 'w-[76px]' : 'w-[244px]'
        )}
      >
        <div
          className={cn(
            'flex h-16 items-center group',
            showCollapsed ? 'justify-center px-2' : 'justify-between px-4'
          )}
        >
          {showCollapsed ? (
            <div className="relative flex items-center justify-center w-full">
              <LogoPebbles className="flex-col gap-[3px] transition-opacity group-hover:opacity-0" />
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="absolute text-muted-foreground opacity-0 transition-opacity hover:bg-sidebar-accent hover:text-foreground group-hover:opacity-100"
              >
                <Menu className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2.5">
                <LogoPebbles />
                <span className="font-display text-[15px] font-bold tracking-tight text-sidebar-foreground">
                  {t('common.appName')}
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
                data-testid="sidebar-toggle"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>

        <nav
          className={cn(
            'flex-1 space-y-1 overflow-y-auto py-4',
            showCollapsed ? 'px-2' : 'px-3'
          )}
        >
          <div
            className={cn(
              'mb-4',
              showCollapsed ? 'px-0' : 'px-3'
            )}
          >
            <DropdownMenu open={createMenuOpen} onOpenChange={setCreateMenuOpen}>
              {showCollapsed ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <DropdownMenuTrigger asChild>
                      <Button
                        onClick={() => setCreateMenuOpen(true)}
                        variant="default"
                        size="sm"
                        className="w-full justify-center px-2 font-display font-bold"
                        aria-label={t('common.create')}
                      >
                        <Plus className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                  </TooltipTrigger>
                   <TooltipContent side="right">{t('common.create')}</TooltipContent>
                </Tooltip>
              ) : (
                <DropdownMenuTrigger asChild>
                  <Button
                    onClick={() => setCreateMenuOpen(true)}
                    variant="default"
                    size="sm"
                    className="w-full justify-start font-display font-bold"
                   >
                    <Plus className="h-4 w-4 mr-2" />
                    {t('common.create')}
                  </Button>
                </DropdownMenuTrigger>
              )}

              <DropdownMenuContent
                align={showCollapsed ? 'end' : 'start'}
                side={showCollapsed ? 'right' : 'bottom'}
                className="w-48"
              >
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('source')
                  }}
                  className="gap-2"
                >
                   <FileText className="h-4 w-4" />
                  {t('common.source')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('notebook')
                  }}
                  className="gap-2"
                >
                   <Book className="h-4 w-4" />
                  {t('common.notebook')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('podcast')
                  }}
                  className="gap-2"
                >
                   <Mic className="h-4 w-4" />
                  {t('common.podcast')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {navigation.map((section, index) => (
            <div key={section.title}>
              {index > 0 && (
                <div className="mx-3 my-3 h-px bg-border" />
              )}
              <div className="space-y-1">
                {!showCollapsed && (
                  <h3 className="mb-1.5 px-3 text-[10.5px] font-bold uppercase tracking-[0.14em] text-muted-foreground/70">
                    {section.title}
                  </h3>
                )}

                {section.items.map((item) => {
                  const isActive = item.href === activeHref
                  const button = (
                    <Button
                      variant="ghost"
                      className={cn(
                        'sidebar-menu-item relative w-full gap-2.5 rounded-md text-[13px] font-medium text-muted-foreground',
                        'hover:bg-sidebar-accent',
                        isActive &&
                          'bg-sidebar-accent font-semibold text-sidebar-foreground ring-1 ring-inset ring-border before:absolute before:-left-1.5 before:top-[7px] before:bottom-[7px] before:w-[3px] before:rounded-[2px] before:bg-fern',
                        showCollapsed ? 'justify-center px-2' : 'justify-start'
                      )}
                    >
                      <item.icon className={cn('h-4 w-4 opacity-85', isActive && 'text-teal')} />
                      {!showCollapsed && <span>{item.name}</span>}
                    </Button>
                  )

                  if (showCollapsed) {
                    return (
                      <Tooltip key={item.name}>
                        <TooltipTrigger asChild>
                          <Link href={item.href}>
                            {button}
                          </Link>
                        </TooltipTrigger>
                        <TooltipContent side="right">{item.name}</TooltipContent>
                      </Tooltip>
                    )
                  }

                  return (
                    <Link key={item.name} href={item.href}>
                      {button}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div
          className={cn(
            'border-t border-sidebar-border p-3 space-y-2',
            showCollapsed && 'px-2'
          )}
        >
          {!showCollapsed && (
            <div className="sidebar-guard">
              <AccessGuard />
            </div>
          )}

          {/* Command Palette hint */}
          {!showCollapsed && (
            <div className="px-3 py-1.5 text-xs text-muted-foreground">
              <div className="flex items-center justify-between">
                 <span className="flex items-center gap-1.5">
                  <Command className="h-3 w-3" />
                  {t('common.quickActions')}
                </span>
                <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border border-border bg-secondary px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
                  {isMac ? <span className="text-xs">⌘</span> : <span>Ctrl+</span>}K
                </kbd>
              </div>
               <p className="mt-1 text-[10px] text-muted-foreground/70">
                {t('common.quickActionsDesc')}
              </p>
            </div>
          )}

           <div
            className={cn(
              'flex flex-col gap-2',
              showCollapsed ? 'items-center' : 'items-stretch'
            )}
          >
            {showCollapsed ? (
              <>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div>
                      <ThemeToggle iconOnly />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">{t('common.theme')}</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div>
                      <LanguageToggle iconOnly />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">{t('common.language')}</TooltipContent>
                </Tooltip>
              </>
            ) : (
              <>
                <ThemeToggle />
                <LanguageToggle />
              </>
            )}
          </div>

          {showCollapsed ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  className="w-full justify-center sidebar-menu-item bg-card"
                  onClick={logout}
                  aria-label={t('common.signOut')}
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
               <TooltipContent side="right">{t('common.signOut')}</TooltipContent>
            </Tooltip>
          ) : (
            <Button
              variant="outline"
              className="w-full justify-start gap-2 sidebar-menu-item bg-card text-muted-foreground hover:text-foreground"
              onClick={logout}
              aria-label={t('common.signOut')}
             >
              <LogOut className="h-4 w-4" />
              {t('common.signOut')}
            </Button>
          )}
        </div>
      </div>
    </TooltipProvider>
  )
}

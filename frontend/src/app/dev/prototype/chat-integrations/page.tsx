"use client"

// PROTOTYPE (throwaway) — answers the #42 question: what should the Settings →
// "Chat integrations" section look like? Three structurally different variants
// of the same section, switchable via ?variant=A|B|C (arrow keys work too).
//
// Plan: "Three variants of the Settings → Chat integrations section, switchable
// via ?variant=, hosted on the dev-only prototype route with real AppShell chrome."
//
// Dev-only: returns 404 in production. Not translated on purpose. All data is
// mock, all backend calls are simulated — the question is "what should this look
// like", not "does the backend work". When a variant wins: fold the winner into
// the real settings page as its own SectionCard (outside SettingsForm), then
// delete this file. The full variant set is primary source — capture it on a
// throwaway branch before deleting (see prototype skill, step 6).

import { Suspense, useCallback, useEffect, useRef, useState } from "react"
import { notFound, useRouter, useSearchParams } from "next/navigation"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Copy,
  Loader2,
  MessageCircle,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from "lucide-react"

import { AppShell } from "@/components/layout/AppShell"
import { SectionCard } from "@/components/shell/section-card"
import { CreateDialogsProvider } from "@/lib/hooks/use-create-dialogs"
import { ModalProvider } from "@/components/providers/ModalProvider"
import { CommandPalette } from "@/components/common/CommandPalette"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Progress } from "@/components/ui/progress"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

// ---------------------------------------------------------------------------
// Mock domain (shared by all variants)
// ---------------------------------------------------------------------------

type Platform = "telegram" | "whatsapp"

interface ChatLink {
  id: string
  platform: Platform
  identity: string // @username or masked phone
  linkedAt: string
}

type PendingPhase = "waiting" | "confirmed" | "expired"

interface Pending {
  platform: Platform
  code: string
  secondsLeft: number
  phase: PendingPhase
}

const CODE_TTL_SECONDS = 10 * 60

const PLATFORM_META: Record<
  Platform,
  { name: string; icon: typeof Send; botRef: string; instructions: string }
> = {
  telegram: {
    name: "Telegram",
    icon: Send,
    botRef: "@open_notebook_bot",
    instructions: "Open Telegram, find @open_notebook_bot, and send:",
  },
  whatsapp: {
    name: "WhatsApp",
    icon: MessageCircle,
    botRef: "the Open Notebook number",
    instructions: "Message the Open Notebook WhatsApp number with:",
  },
}

const INITIAL_LINKS: ChatLink[] = [
  { id: "l1", platform: "telegram", identity: "@zoran", linkedAt: "2026-09-14" },
  { id: "l2", platform: "whatsapp", identity: "+1 ••• ••• 4567", linkedAt: "2026-09-12" },
  { id: "l3", platform: "whatsapp", identity: "+44 ••• ••• 8901", linkedAt: "2026-09-18" },
]

let mockId = 100
const genCode = () => String(Math.floor(100000 + Math.random() * 900000))
const fmtCountdown = (s: number) =>
  `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`

/** Mock of POST /api/integrations/link → claim flow. Demo buttons stand in for the bot. */
function useConnectFlow(onConfirm: (platform: Platform) => void) {
  const [pending, setPending] = useState<Pending | null>(null)
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const clear = useCallback(() => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
  }, [])

  const start = useCallback(
    (platform: Platform) => {
      clear()
      setPending({ platform, code: genCode(), secondsLeft: CODE_TTL_SECONDS, phase: "waiting" })
      timer.current = setInterval(() => {
        setPending((p) => {
          if (!p) return p
          if (p.secondsLeft <= 1) {
            clear()
            return { ...p, secondsLeft: 0, phase: "expired" }
          }
          return { ...p, secondsLeft: p.secondsLeft - 1 }
        })
      }, 1000)
    },
    [clear]
  )

  const cancel = useCallback(() => {
    clear()
    setPending(null)
  }, [clear])

  const simulateConfirm = useCallback(() => {
    const platform = pending?.platform
    clear()
    setPending(null)
    if (platform) onConfirm(platform)
  }, [pending, clear, onConfirm])

  const simulateExpiry = useCallback(() => {
    clear()
    setPending((p) => (p ? { ...p, secondsLeft: 0, phase: "expired" } : p))
  }, [clear])

  useEffect(() => clear, [clear])

  return { pending, start, cancel, simulateConfirm, simulateExpiry }
}

/** Clearly-labeled demo controls standing in for the bot's half of the flow. */
function DemoControls({
  onConfirm,
  onExpire,
}: {
  onConfirm: () => void
  onExpire: () => void
}) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-dashed border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
      <span className="font-medium uppercase tracking-wide">demo</span>
      <span className="mr-auto">stand in for the bot:</span>
      <Button size="sm" variant="outline" onClick={onConfirm}>
        <Check className="mr-1 h-3.5 w-3.5" /> bot confirms
      </Button>
      <Button size="sm" variant="outline" onClick={onExpire}>
        <Clock3 className="mr-1 h-3.5 w-3.5" /> code expires
      </Button>
    </div>
  )
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex items-center justify-center gap-3">
      <span className="font-mono text-3xl font-semibold tracking-[0.35em]">{code}</span>
      <Button
        size="icon"
        variant="ghost"
        aria-label="Copy code"
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

function CountdownLine({ pending }: { pending: Pending }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {pending.phase === "expired"
            ? "Code expired"
            : `Expires in ${fmtCountdown(pending.secondsLeft)}`}
        </span>
        {pending.phase === "expired" && <Badge variant="destructive">Expired</Badge>}
      </div>
      <Progress value={(pending.secondsLeft / CODE_TTL_SECONDS) * 100} />
    </div>
  )
}

function useLinks() {
  const [links, setLinks] = useState<ChatLink[]>(INITIAL_LINKS)
  const addLink = useCallback((platform: Platform) => {
    setLinks((ls) => [
      ...ls,
      {
        id: `l${++mockId}`,
        platform,
        identity: platform === "telegram" ? "@new_user" : "+1 ••• ••• 0000",
        linkedAt: "2026-09-20",
      },
    ])
  }, [])
  const removeLink = useCallback((id: string) => {
    setLinks((ls) => ls.filter((l) => l.id !== id))
  }, [])
  return { links, addLink, removeLink }
}

function PlatformIcon({ platform, className }: { platform: Platform; className?: string }) {
  const Icon = PLATFORM_META[platform].icon
  return <Icon className={className} />
}

// ---------------------------------------------------------------------------
// Variant A — "Platform rows": one row per platform, connect happens in a dialog.
// Management-first: the default view is status + history of each platform.
// ---------------------------------------------------------------------------

function VariantA() {
  const { links, addLink, removeLink } = useLinks()
  const flow = useConnectFlow(addLink)
  const [dialogPlatform, setDialogPlatform] = useState<Platform | null>(null)
  const [managePlatform, setManagePlatform] = useState<Platform | null>(null)

  const platforms: Platform[] = ["telegram", "whatsapp"]

  return (
    <SectionCard
      title={
        <>
          Chat integrations
          <span className="mt-1 block font-sans text-sm font-normal tracking-normal text-muted-foreground">
            Ask questions and search your notebooks from Telegram or WhatsApp.
          </span>
        </>
      }
    >
      <div className="divide-y">
        {platforms.map((platform) => {
          const meta = PLATFORM_META[platform]
          const platformLinks = links.filter((l) => l.platform === platform)
          const connected = platformLinks.length > 0
          const managing = managePlatform === platform
          return (
            <div key={platform} className="px-5 py-4">
              <div className="flex items-center gap-4">
                <div className="flex h-10 w-10 items-center justify-center rounded-md border bg-muted/50">
                  <PlatformIcon platform={platform} className="h-5 w-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{meta.name}</span>
                    <Badge variant={connected ? "default" : "secondary"}>
                      {connected ? "Connected" : "Not connected"}
                    </Badge>
                  </div>
                  <p className="truncate text-sm text-muted-foreground">
                    {connected
                      ? `${platformLinks.length} linked ${platformLinks.length === 1 ? "identity" : "identities"}`
                      : `Link your ${meta.name} account to ask from chat`}
                  </p>
                </div>
                {connected && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setManagePlatform(managing ? null : platform)}
                  >
                    {managing ? "Close" : "Manage"}
                  </Button>
                )}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    flow.start(platform)
                    setDialogPlatform(platform)
                  }}
                >
                  <Plus className="mr-1 h-4 w-4" /> Connect
                </Button>
              </div>

              {managing && (
                <div className="mt-3 space-y-2 border-l-2 pl-4">
                  {platformLinks.map((link) => (
                    <div
                      key={link.id}
                      className="flex items-center justify-between rounded-md border bg-card px-3 py-2"
                    >
                      <div className="text-sm">
                        <span className="font-medium">{link.identity}</span>
                        <span className="ml-2 text-muted-foreground">linked {link.linkedAt}</span>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removeLink(link.id)}
                      >
                        <Trash2 className="mr-1 h-4 w-4" /> Unlink
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>

      <Dialog
        open={dialogPlatform !== null}
        onOpenChange={(open) => {
          if (!open) {
            flow.cancel()
            setDialogPlatform(null)
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Connect {dialogPlatform && PLATFORM_META[dialogPlatform].name}</DialogTitle>
            <DialogDescription>
              You&apos;ll confirm the link from your phone — takes under a minute.
            </DialogDescription>
          </DialogHeader>

          {dialogPlatform && flow.pending && (
            <div className="space-y-5 py-2">
              <ol className="space-y-2 text-sm text-muted-foreground">
                <li>1. {PLATFORM_META[dialogPlatform].instructions}</li>
                <li>2. Wait here — we confirm automatically.</li>
              </ol>

              <div className="rounded-lg border bg-muted/30 py-5">
                {flow.pending.phase === "expired" ? (
                  <div className="space-y-3 px-4 text-center">
                    <p className="text-sm text-destructive">This code expired.</p>
                    <Button variant="outline" size="sm" onClick={() => flow.start(dialogPlatform)}>
                      <RefreshCw className="mr-1 h-4 w-4" /> Generate a new code
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-4 px-4">
                    <CodeBlock code={flow.pending.code} />
                    <p className="text-center text-sm text-muted-foreground">
                      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                        /start {flow.pending.code}
                      </span>
                    </p>
                    <CountdownLine pending={flow.pending} />
                    <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" /> Waiting for{" "}
                      {PLATFORM_META[dialogPlatform].botRef} to confirm…
                    </p>
                    <DemoControls onConfirm={flow.simulateConfirm} onExpire={flow.simulateExpiry} />
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={() => { flow.cancel(); setDialogPlatform(null) }}>
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  )
}

// ---------------------------------------------------------------------------
// Variant B — "Connect wizard": flow-first. Active integrations are small chips;
// the screen is dominated by the connect flow as a stepper.
// ---------------------------------------------------------------------------

function VariantB() {
  const { links, addLink, removeLink } = useLinks()
  const flow = useConnectFlow(addLink)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="font-display text-base font-semibold tracking-tight">Chat integrations</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Ask questions and search your notebooks from Telegram or WhatsApp.
        </p>
      </div>

      {/* Active integrations as chips */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Active:</span>
        {links.length === 0 && <span className="text-sm italic">none yet</span>}
        {links.map((link) => (
          <Badge key={link.id} variant="secondary" className="gap-1.5 py-1.5 pl-2 pr-1">
            <PlatformIcon platform={link.platform} className="h-3.5 w-3.5" />
            {link.identity}
            <button
              aria-label={`Unlink ${link.identity}`}
              className="rounded-full p-0.5 hover:bg-background"
              onClick={() => removeLink(link.id)}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>

      {!flow.pending ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {(Object.keys(PLATFORM_META) as Platform[]).map((platform) => {
            const meta = PLATFORM_META[platform]
            return (
              <button
                key={platform}
                onClick={() => flow.start(platform)}
                className="group flex flex-col items-start gap-3 rounded-lg border bg-card p-6 text-left transition-colors hover:border-foreground/30 hover:bg-muted/20"
              >
                <div className="flex h-11 w-11 items-center justify-center rounded-md border bg-muted/50">
                  <PlatformIcon platform={platform} className="h-5 w-5" />
                </div>
                <div>
                  <div className="font-medium">Connect {meta.name}</div>
                  <p className="mt-0.5 text-sm text-muted-foreground">
                    {platform === "telegram"
                      ? "Fastest to set up — works in under a minute."
                      : "Needs a spare phone number to act as the bot."}
                  </p>
                </div>
                <span className="mt-1 text-sm font-medium text-primary group-hover:underline">
                  Start →
                </span>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="rounded-lg border bg-card">
          {/* Stepper */}
          <div className="flex items-center gap-2 border-b px-6 py-4">
            {["Get your code", `Confirm in ${PLATFORM_META[flow.pending.platform].name}`, "Done"].map(
              (label, i) => {
                const active = i === 1
                const done = i === 2
                return (
                  <div key={label} className="flex items-center gap-2">
                    <span
                      className={`flex h-6 w-6 items-center justify-center rounded-full border text-xs ${
                        done
                          ? "border-green-600 bg-green-600 text-white"
                          : active
                            ? "border-foreground font-semibold"
                            : "border-border text-muted-foreground"
                      }`}
                    >
                      {done ? <Check className="h-3.5 w-3.5" /> : i + 1}
                    </span>
                    <span className={`text-sm ${active ? "font-medium" : "text-muted-foreground"}`}>
                      {label}
                    </span>
                    {i < 2 && <span className="mx-1 h-px w-8 bg-border" />}
                  </div>
                )
              }
            )}
          </div>

          <div className="px-6 py-8">
            {flow.pending.phase === "expired" ? (
              <div className="mx-auto max-w-sm space-y-3 text-center">
                <p className="text-sm text-destructive">This code expired before it was used.</p>
                <Button variant="outline" onClick={() => flow.start(flow.pending!.platform)}>
                  <RefreshCw className="mr-1 h-4 w-4" /> Get a fresh code
                </Button>
              </div>
            ) : (
              <div className="mx-auto max-w-sm space-y-5 text-center">
                <p className="text-sm text-muted-foreground">
                  {PLATFORM_META[flow.pending.platform].instructions}{" "}
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                    /start {flow.pending.code}
                  </span>
                </p>
                <CodeBlock code={flow.pending.code} />
                <CountdownLine pending={flow.pending} />
                <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> Watching for confirmation…
                </p>
                <DemoControls onConfirm={flow.simulateConfirm} onExpire={flow.simulateExpiry} />
              </div>
            )}
          </div>

          <div className="flex justify-end border-t px-6 py-3">
            <Button variant="ghost" size="sm" onClick={flow.cancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Variant C — "Dense table": no dialogs, no wizard. Everything is one inline
// table; adding a link is a row that appears in place. Utilitarian, scannable.
// ---------------------------------------------------------------------------

function VariantC() {
  const { links, addLink, removeLink } = useLinks()
  const flow = useConnectFlow(addLink)
  const [picked, setPicked] = useState<Platform>("telegram")

  return (
    <SectionCard
      title={
        <>
          Chat integrations
          <span className="mt-1 block font-sans text-sm font-normal tracking-normal text-muted-foreground">
            Ask questions and search your notebooks from Telegram or WhatsApp.
          </span>
        </>
      }
    >
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
            <th className="px-5 py-2.5 font-medium">Platform</th>
            <th className="px-3 py-2.5 font-medium">Identity</th>
            <th className="px-3 py-2.5 font-medium">Linked</th>
            <th className="px-3 py-2.5" />
          </tr>
        </thead>
        <tbody className="divide-y">
          {links.map((link) => (
            <tr key={link.id}>
              <td className="px-5 py-2.5">
                <span className="flex items-center gap-2">
                  <PlatformIcon platform={link.platform} className="h-4 w-4 text-muted-foreground" />
                  {PLATFORM_META[link.platform].name}
                </span>
              </td>
              <td className="px-3 py-2.5 font-mono text-xs">{link.identity}</td>
              <td className="px-3 py-2.5 text-muted-foreground">{link.linkedAt}</td>
              <td className="px-3 py-2.5 text-right">
                <Button variant="ghost" size="icon" aria-label="Unlink" onClick={() => removeLink(link.id)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </td>
            </tr>
          ))}

          {/* Add row: platform select + generate, or the pending code inline */}
          <tr>
            {flow.pending ? (
              <td colSpan={4} className="bg-muted/20 px-5 py-3">
                {flow.pending.phase === "expired" ? (
                  <div className="flex items-center gap-3 text-sm">
                    <span className="text-destructive">Code expired.</span>
                    <Button variant="outline" size="sm" onClick={() => flow.start(flow.pending!.platform)}>
                      <RefreshCw className="mr-1 h-3.5 w-3.5" /> Regenerate
                    </Button>
                    <Button variant="ghost" size="sm" onClick={flow.cancel}>
                      Dismiss
                    </Button>
                  </div>
                ) : (
                  <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
                    <span className="flex items-center gap-2 font-medium">
                      <PlatformIcon platform={flow.pending.platform} className="h-4 w-4" />
                      New {PLATFORM_META[flow.pending.platform].name} link
                    </span>
                    <span className="font-mono text-lg font-semibold tracking-[0.3em]">
                      {flow.pending.code}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      send <span className="rounded bg-background px-1 py-0.5 font-mono">/start {flow.pending.code}</span> to{" "}
                      {PLATFORM_META[flow.pending.platform].botRef}
                    </span>
                    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" /> waiting…{" "}
                      {fmtCountdown(flow.pending.secondsLeft)}
                    </span>
                    <span className="ml-auto flex items-center gap-2">
                      <DemoControls onConfirm={flow.simulateConfirm} onExpire={flow.simulateExpiry} />
                      <Button variant="ghost" size="sm" onClick={flow.cancel}>
                        Cancel
                      </Button>
                    </span>
                  </div>
                )}
              </td>
            ) : (
              <>
                <td colSpan={3} className="px-5 py-2.5">
                  <div className="flex items-center gap-2">
                    <Select value={picked} onValueChange={(v) => setPicked(v as Platform)}>
                      <SelectTrigger className="h-8 w-44" aria-label="Platform">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(Object.keys(PLATFORM_META) as Platform[]).map((p) => (
                          <SelectItem key={p} value={p}>
                            <span className="flex items-center gap-2">
                              <PlatformIcon platform={p} className="h-4 w-4" />
                              {PLATFORM_META[p].name}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button variant="outline" size="sm" onClick={() => flow.start(picked)}>
                      <Plus className="mr-1 h-4 w-4" /> Generate linking code
                    </Button>
                  </div>
                </td>
                <td className="px-3 py-2.5" />
              </>
            )}
          </tr>
        </tbody>
      </table>
    </SectionCard>
  )
}

// ---------------------------------------------------------------------------
// Switcher — floating bottom bar, ?variant= in the URL, arrow-key cycling.
// ---------------------------------------------------------------------------

const VARIANTS = [
  { key: "A", name: "Platform rows", component: VariantA },
  { key: "B", name: "Connect wizard", component: VariantB },
  { key: "C", name: "Dense table", component: VariantC },
] as const

function PrototypeSwitcher({ current }: { current: string }) {
  const router = useRouter()
  const idx = Math.max(0, VARIANTS.findIndex((v) => v.key === current))
  const variant = VARIANTS[idx] ?? VARIANTS[0]

  const go = useCallback(
    (delta: number) => {
      const next = VARIANTS[(idx + delta + VARIANTS.length) % VARIANTS.length]
      router.replace(`?variant=${next.key}`)
    },
    [idx, router]
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target?.closest("input, textarea, [contenteditable]")) return
      if (e.key === "ArrowLeft") go(-1)
      if (e.key === "ArrowRight") go(1)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [go])

  return (
    <div className="fixed bottom-6 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full border bg-foreground px-2 py-1.5 text-background shadow-lg">
      <Button
        size="icon"
        variant="ghost"
        className="h-7 w-7 rounded-full text-background hover:bg-background/20 hover:text-background"
        aria-label="Previous variant"
        onClick={() => go(-1)}
      >
        <ChevronLeft className="h-4 w-4" />
      </Button>
      <span className="px-2 font-mono text-xs">
        {variant.key} <span className="opacity-70">({variant.name})</span>
      </span>
      <Button
        size="icon"
        variant="ghost"
        className="h-7 w-7 rounded-full text-background hover:bg-background/20 hover:text-background"
        aria-label="Next variant"
        onClick={() => go(1)}
      >
        <ChevronRight className="h-4 w-4" />
      </Button>
    </div>
  )
}

function PrototypeInner() {
  const searchParams = useSearchParams()
  const variantKey = searchParams.get("variant") ?? "A"
  const variant = VARIANTS.find((v) => v.key === variantKey) ?? VARIANTS[0]
  const Active = variant.component

  return (
    <CreateDialogsProvider>
      <AppShell>
        <div className="flex-1 overflow-y-auto">
          <div className="p-6">
            <div className="max-w-3xl">
              <div className="mb-6 rounded-md border border-dashed border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
                PROTOTYPE — throwaway. Three variants of the Settings → Chat
                integrations section. Flip with ‹ › or arrow keys; the URL is
                shareable. All data is mock; the dashed demo boxes simulate the
                bot confirming or the code expiring.
              </div>
              <Active />
            </div>
          </div>
        </div>
        <PrototypeSwitcher current={variant.key} />
      </AppShell>
      <ModalProvider />
      <CommandPalette />
    </CreateDialogsProvider>
  )
}

export default function ChatIntegrationsPrototypePage() {
  if (process.env.NODE_ENV === "production") {
    notFound()
  }
  return (
    <Suspense fallback={null}>
      <PrototypeInner />
    </Suspense>
  )
}

'use client'

import { memo, useCallback, useEffect, useRef, useState } from 'react'
import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Bot, User, Send, Loader2, Clock, Sparkles, BookOpen } from 'lucide-react'
import { MarkdownRenderer } from '@/components/ui/markdown-renderer'
import { NotebookScopeSelector } from '@/components/search/NotebookScopeSelector'
import { SessionManager } from '@/components/sources/SessionManager'
import { MessageActions } from '@/components/sources/MessageActions'
import { convertReferencesToCompactMarkdown, createCompactReferenceLinkComponent } from '@/lib/utils/source-references'
import { useHomeChat } from '@/lib/hooks/use-home-chat'
import { useModelDefaults } from '@/lib/hooks/use-models'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { useTranslation } from '@/lib/hooks/use-translation'
import { SourceChatMessage } from '@/lib/types/api'
import { toast } from 'sonner'

interface StarterSuggestion {
  labelKey: string
  question: string
}

// Clickable starter questions for the empty state. Questions are sent
// verbatim; only the labels are localized.
const STARTER_SUGGESTIONS: StarterSuggestion[] = [
  {
    labelKey: 'home.starterHighestSpender',
    question: 'Who is the highest spender this year?',
  },
  {
    labelKey: 'home.starterRanking',
    question: 'Rank customers by total spend in 2026',
  },
  {
    labelKey: 'home.starterTopServices',
    question: 'What are our top services?',
  },
  {
    labelKey: 'home.starterKnowledgeBase',
    question: 'What documents are in my knowledge base?',
  },
]

export default function HomePage() {
  const { t } = useTranslation()
  const chat = useHomeChat()
  const { data: modelDefaults, isLoading: modelsLoading } = useModelDefaults()
  const [scopeNotebookIds, setScopeNotebookIds] = useState<string[]>([])
  const [sessionManagerOpen, setSessionManagerOpen] = useState(false)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const pinnedAiIdRef = useRef<string | null>(null)

  // Auto-scroll to the latest message — scoped to the chat viewport only.
  // scrollIntoView() scrolls EVERY scrollable ancestor, which shifted the
  // whole page once the history outgrew the viewport; it also restarted a
  // smooth scroll on every streaming delta, fighting anyone scrolled up.
  useEffect(() => {
    const viewport = scrollAreaRef.current?.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]',
    )
    if (!viewport) return
    // Don't fight the user: follow only when already near the bottom.
    const distanceFromBottom =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight
    if (distanceFromBottom > 80) return
    if (typeof viewport.scrollTo === 'function') {
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: 'smooth' })
    } else {
      // jsdom and older engines: assignment is a harmless no-op there.
      viewport.scrollTop = viewport.scrollHeight
    }
  }, [chat.messages])

  // When an answer starts streaming, pin the question it answers to the TOP
  // of the viewport — the answer then reads directly below it instead of
  // the view jumping to the stream tail. Fires once per answer, only while
  // streaming (never when reopening an old session).
  useEffect(() => {
    if (!chat.isStreaming) return
    const lastAiIndex = chat.messages.map((m) => m.type).lastIndexOf('ai')
    if (lastAiIndex < 0) return
    const lastAi = chat.messages[lastAiIndex]
    if (lastAi.id === pinnedAiIdRef.current) return
    const question = [...chat.messages.slice(0, lastAiIndex)]
      .reverse()
      .find((m) => m.type === 'human')
    if (!question) return
    pinnedAiIdRef.current = lastAi.id
    const viewport = scrollAreaRef.current?.querySelector<HTMLElement>(
      '[data-slot="scroll-area-viewport"]',
    )
    const el = viewport?.querySelector<HTMLElement>(
      `[data-message-id="${CSS.escape(question.id)}"]`,
    )
    if (!viewport || !el) return
    const top =
      el.getBoundingClientRect().top -
      viewport.getBoundingClientRect().top +
      viewport.scrollTop
    if (typeof viewport.scrollTo === 'function') {
      viewport.scrollTo({ top, behavior: 'smooth' })
    } else {
      viewport.scrollTop = top
    }
  }, [chat.messages, chat.isStreaming])

  const handleSend = useCallback((message: string) => {
    const models = modelDefaults?.default_chat_model
      ? {
          strategy: modelDefaults.default_chat_model,
          answer: modelDefaults.default_chat_model,
          finalAnswer: modelDefaults.default_chat_model
        }
      : undefined
    chat.sendMessage(message, { notebookIds: scopeNotebookIds, models })
  }, [chat, scopeNotebookIds, modelDefaults?.default_chat_model])

  return (
    <AppShell>
      <div className="flex flex-col flex-1 min-h-0">
        <div className="flex items-center justify-between px-4 pt-4">
          <h1 className="text-lg font-semibold">{t('navigation.home')}</h1>
          <Dialog open={sessionManagerOpen} onOpenChange={setSessionManagerOpen}>
            <Button
              variant="ghost"
              size="sm"
              className="gap-2 text-muted-foreground"
              onClick={() => setSessionManagerOpen(true)}
              disabled={chat.loadingSessions}
            >
              <Clock className="h-4 w-4" />
              <span className="text-xs">{t('chat.sessions')}</span>
            </Button>
            <DialogContent className="sm:max-w-[420px] p-0 overflow-hidden">
              <DialogTitle className="sr-only">{t('chat.sessionsTitle')}</DialogTitle>
              <SessionManager
                sessions={chat.sessions}
                currentSessionId={chat.currentSessionId}
                onCreateSession={(title) => {
                  chat.createSession({ title })
                }}
                onSelectSession={(sessionId) => {
                  chat.switchSession(sessionId)
                  setSessionManagerOpen(false)
                }}
                onUpdateSession={(sessionId, title) => chat.updateSession(sessionId, { title })}
                onDeleteSession={(sessionId) => chat.deleteSession(sessionId)}
                loadingSessions={chat.loadingSessions}
              />
            </DialogContent>
          </Dialog>
        </div>

        <ScrollArea ref={scrollAreaRef} className="flex-1 min-h-0 px-4">
          <div className="space-y-4 py-4 max-w-3xl mx-auto">
            {chat.messages.length === 0 ? (
              <div className="text-center text-muted-foreground py-12">
                <Bot className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p className="text-base font-medium text-foreground">{t('home.welcomeTitle')}</p>
                <p className="text-sm mt-2 max-w-md mx-auto">{t('home.welcomeHint')}</p>
                <div className="flex flex-wrap justify-center gap-2 mt-6 max-w-lg mx-auto">
                  {STARTER_SUGGESTIONS.map((suggestion) => (
                    <button
                      key={suggestion.question}
                      type="button"
                      onClick={() => handleSend(suggestion.question)}
                      disabled={chat.isStreaming || !modelDefaults?.default_chat_model}
                      className="rounded-md border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
                    >
                      {t(suggestion.labelKey)}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              chat.messages.map((message) => (
                <HomeChatMessageBubble
                  key={message.id}
                  message={message}
                />
              ))
            )}
            {chat.isStreaming && (
              <div className="flex gap-3 justify-start">
                <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-fern-tint text-fern">
                  <Sparkles className="h-4 w-4" />
                </span>
                <div className="rounded-lg px-4 py-2 bg-card border">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span className="sr-only">
                    {t('home.searching')}
                  </span>
                </div>
              </div>
            )}
          </div>
        </ScrollArea>

        {/* Follow-up suggestions from the last completed turn */}
        {!chat.isStreaming && chat.suggestions.length > 0 && (
          <div className="px-4 pb-2 max-w-3xl mx-auto w-full">
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground mb-2">
              <Sparkles className="h-3.5 w-3.5" />
              {t('home.suggestions')}
            </div>
            <div className="flex flex-wrap gap-2">
              {chat.suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => handleSend(suggestion)}
                  className="rounded-md border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        <HomeComposer
          onSend={handleSend}
          isStreaming={chat.isStreaming}
          disabled={modelsLoading || !modelDefaults?.default_chat_model}
          scopeNotebookIds={scopeNotebookIds}
          onScopeChange={setScopeNotebookIds}
        />
      </div>
    </AppShell>
  )
}

interface HomeChatMessageBubbleProps {
  message: SourceChatMessage
}

const HomeChatMessageBubble = memo(function HomeChatMessageBubble({
  message
}: HomeChatMessageBubbleProps) {
  const { t } = useTranslation()
  const { openModal } = useModalManager()

  const handleReferenceClick = useCallback((type: string, id: string) => {
    const modalType = type === 'source_insight' ? 'insight' : (type as 'source' | 'note' | 'insight')
    try {
      openModal(modalType, id)
    } catch {
      toast.error(t('common.noResults'))
    }
  }, [openModal, t])

  return (
    <div
      data-message-id={message.id}
      className={`flex gap-3 ${message.type === 'human' ? 'justify-end' : 'justify-start'}`}
    >
      {message.type === 'ai' && (
        <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-fern-tint text-fern">
          <Sparkles className="h-4 w-4" />
        </span>
      )}
      <div className="flex flex-col gap-2 max-w-[75%] min-w-0">
        <div className={`rounded-lg ${
          message.type === 'human'
            ? 'bg-fern px-4 py-2.5 text-sm leading-relaxed text-primary-foreground'
            : 'bg-card px-4 py-3 text-sm leading-relaxed ring-1 ring-inset ring-border'
        }`}>
          {message.type === 'ai' ? (
            <>
              <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide text-muted-foreground mb-1">
                <BookOpen className="h-3 w-3" />
                {t('home.answerSourceKnowledge')}
              </div>
              <KnowledgeAnswerContent
                content={message.content}
                onReferenceClick={handleReferenceClick}
              />
            </>
          ) : (
            <p className="text-sm break-words whitespace-pre-wrap">{message.content}</p>
          )}
        </div>
        {message.type === 'ai' && <MessageActions content={message.content} />}
      </div>
      {message.type === 'human' && (
        <div className="flex-shrink-0">
          <div className="h-8 w-8 rounded-full bg-muted border flex items-center justify-center">
            <User className="h-4 w-4 text-muted-foreground" />
          </div>
        </div>
      )}
    </div>
  )
})

function KnowledgeAnswerContent({
  content,
  onReferenceClick
}: {
  content: string
  onReferenceClick: (type: string, id: string) => void
}) {
  const { t } = useTranslation()
  const markdownWithCompactRefs = convertReferencesToCompactMarkdown(content, t('common.references'))
  const LinkComponent = createCompactReferenceLinkComponent(onReferenceClick)
  return (
    <MarkdownRenderer components={{ a: LinkComponent }}>
      {markdownWithCompactRefs}
    </MarkdownRenderer>
  )
}

interface HomeComposerProps {
  onSend: (message: string) => void
  isStreaming: boolean
  disabled: boolean
  scopeNotebookIds: string[]
  onScopeChange: (ids: string[]) => void
}

function HomeComposer({
  onSend,
  isStreaming,
  disabled,
  scopeNotebookIds,
  onScopeChange
}: HomeComposerProps) {
  const { t } = useTranslation()
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    if (input.trim() && !isStreaming && !disabled) {
      onSend(input.trim())
      setInput('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  return (
    <div className="flex-shrink-0 p-4 border-t max-w-3xl mx-auto w-full">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <NotebookScopeSelector
          selectedIds={scopeNotebookIds}
          onChange={onScopeChange}
          disabled={isStreaming}
        />
      </div>
      <div className="flex gap-2 items-end min-w-0">
        <Textarea
          ref={textareaRef}
          name="home-chat-message"
          autoComplete="off"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('home.placeholder')}
          disabled={isStreaming || disabled}
          className="flex-1 min-h-[44px] max-h-[120px] resize-none py-2 px-3 min-w-0"
          rows={1}
        />
        <Button
          onClick={handleSend}
          disabled={!input.trim() || isStreaming || disabled}
          size="icon"
          className="h-[44px] w-[44px] flex-shrink-0"
          aria-label={t('chat.send')}
        >
          {isStreaming ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
        </Button>
      </div>
    </div>
  )
}

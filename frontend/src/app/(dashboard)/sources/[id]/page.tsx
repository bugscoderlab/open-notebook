'use client'

import { useRouter, useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useSourceChat } from '@/lib/hooks/use-source-chat'
import { ChatPanel } from '@/components/sources/ChatPanel'
import { useNavigation } from '@/lib/hooks/use-navigation'
import { SourceDetailContent } from '@/components/sources/SourceDetailContent'
import { CollapsibleColumn } from '@/components/notebooks/CollapsibleColumn'
import { AppShell } from '@/components/layout/AppShell'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'

const CHAT_OPEN_STORAGE_KEY = 'source-detail-chat-open'

export default function SourceDetailPage() {
  const router = useRouter()
  const params = useParams()
  const sourceId = params?.id ? decodeURIComponent(params.id as string) : ''
  const navigation = useNavigation()
  const { t } = useTranslation()

  // Initialize source chat
  const chat = useSourceChat(sourceId)

  // Catalog source detail: the chat column collapses to a slim teal rail.
  // Persisted like the prototype (proto-src-chat), hydrated after mount to
  // avoid SSR mismatches.
  const [chatOpen, setChatOpen] = useState(true)
  useEffect(() => {
    setChatOpen(localStorage.getItem(CHAT_OPEN_STORAGE_KEY) !== '0')
  }, [])
  useEffect(() => {
    localStorage.setItem(CHAT_OPEN_STORAGE_KEY, chatOpen ? '1' : '0')
  }, [chatOpen])

  const chatLabel = t('chat.chatWith', { name: t('navigation.sources') })

  const handleBack = useCallback(() => {
    const returnPath = navigation.getReturnPath()
    router.push(returnPath)
    navigation.clearReturnTo()
  }, [navigation, router])

  return (
    <AppShell>
      <div className="flex flex-col flex-1 min-h-0">
      {/* Back link */}
      <div className="shrink-0 px-6 pt-6 lg:px-8">
        <button
          type="button"
          onClick={handleBack}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-sm text-muted-foreground transition-colors hover:bg-card hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          {navigation.getReturnLabel()}
        </button>
      </div>

      {/* Main content: Source detail + Chat */}
      <div
        className={cn(
          'grid min-h-0 flex-1 gap-6 px-6 pb-6 pt-4 lg:px-8',
          chatOpen ? 'lg:grid-cols-[2fr_1fr]' : 'lg:grid-cols-[1fr_auto]'
        )}
      >
        {/* Left column - Source detail */}
        <div className="min-h-0 overflow-y-auto pr-1">
          <SourceDetailContent
            sourceId={sourceId}
            showChatButton={false}
            onClose={handleBack}
          />
        </div>

        {/* Right column - Chat with source (collapses to a rail) */}
        <div className="min-h-0">
          {chatOpen ? (
            <ChatPanel
              messages={chat.messages}
              isStreaming={chat.isStreaming}
              contextIndicators={chat.contextIndicators}
              onSendMessage={(message, model) => chat.sendMessage(message, model)}
              modelOverride={chat.currentSession?.model_override}
              onModelChange={(model) => {
                if (chat.currentSessionId) {
                  chat.updateSession(chat.currentSessionId, { model_override: model })
                }
              }}
              sessions={chat.sessions}
              currentSessionId={chat.currentSessionId}
              onCreateSession={(title) => chat.createSession({ title })}
              onSelectSession={chat.switchSession}
              onUpdateSession={(sessionId, title) => chat.updateSession(sessionId, { title })}
              onDeleteSession={chat.deleteSession}
              loadingSessions={chat.loadingSessions}
              title={chatLabel}
              onCollapse={() => setChatOpen(false)}
            />
          ) : (
            <CollapsibleColumn
              isCollapsed
              onToggle={() => setChatOpen(true)}
              collapsedColor="bg-teal"
              collapsedLabel={chatLabel}
            >
              {null}
            </CollapsibleColumn>
          )}
        </div>
      </div>
      </div>
    </AppShell>
  )
}

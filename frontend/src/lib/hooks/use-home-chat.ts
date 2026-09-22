'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { useTranslation } from '@/lib/hooks/use-translation'
import { homeChatApi } from '@/lib/api/home-chat'
import {
  HomeChatSession,
  SourceChatMessage,
  HomeChatTurn,
  HomeChatStreamEvent,
  CreateHomeChatSessionRequest,
  UpdateHomeChatSessionRequest
} from '@/lib/types/api'

export interface SendHomeMessageOptions {
  notebookIds?: string[]
  models?: {
    strategy: string
    answer: string
    finalAnswer: string
  }
}

/** Transport-level failures (browser/dev-proxy stream aborts) vs typed
 * errors (HTTP status, in-band error events) — only the former get a
 * transparent retry. Safari surfaces aborts as TypeError; Chrome as
 * TypeError "Failed to fetch". */
function isTransportError(error: unknown): boolean {
  if (error instanceof TypeError) return true
  const message = error instanceof Error ? error.message : String(error)
  return /input stream|failed to fetch|network error|load failed/i.test(message)
}

export function useHomeChat() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SourceChatMessage[]>([])
  // Aligned with AI messages in order (suggestions per turn).
  const [turns, setTurns] = useState<HomeChatTurn[]>([])
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const abortControllerRef = useRef<AbortController | null>(null)

  const { data: sessions = [], isLoading: loadingSessions, refetch: refetchSessions } = useQuery<HomeChatSession[]>({
    queryKey: ['homeChatSessions'],
    queryFn: () => homeChatApi.listSessions()
  })

  const { data: currentSession, refetch: refetchCurrentSession } = useQuery({
    queryKey: ['homeChatSession', currentSessionId],
    queryFn: () => homeChatApi.getSession(currentSessionId!),
    enabled: !!currentSessionId
  })

  // Sync persisted state when the session loads (switching sessions, reload
  // after a completed turn). Sync setState here is deliberate: the fetched
  // session IS the source of truth for the local message list.
  //
  // Never sync while streaming: for a first message the session is created
  // mid-send, its query resolves immediately with EMPTY messages, and that
  // snapshot would wipe the optimistic question until the post-turn refetch
  // brings it back together with the answer.
  useEffect(() => {
    if (isStreaming) return
    if (currentSession?.messages) {
      setMessages(currentSession.messages)
      const aiCount = currentSession.messages.filter((m) => m.type === 'ai').length
      const aligned: HomeChatTurn[] = []
      for (let i = 0; i < aiCount; i++) {
        aligned.push(currentSession.turns?.[i] ?? {})
      }
      setTurns(aligned)
      setSuggestions(aligned.length > 0 ? (aligned[aligned.length - 1].suggestions ?? []) : [])
    }
  }, [currentSession, isStreaming])

  // Intentionally no auto-select of the most recent session: Home opens
  // fresh (welcome + starter suggestions); past conversations are picked
  // from the sessions dialog.

  const createSessionMutation = useMutation({
    mutationFn: (data: CreateHomeChatSessionRequest) => homeChatApi.createSession(data),
    onSuccess: (newSession) => {
      queryClient.invalidateQueries({ queryKey: ['homeChatSessions'] })
      setCurrentSessionId(newSession.id)
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
    }
  })

  const updateSessionMutation = useMutation({
    mutationFn: ({ sessionId, data }: { sessionId: string, data: UpdateHomeChatSessionRequest }) =>
      homeChatApi.updateSession(sessionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['homeChatSessions'] })
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToUpdateSession'))
    }
  })

  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) => homeChatApi.deleteSession(sessionId),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['homeChatSessions'] })
      if (currentSessionId === deletedId) {
        setCurrentSessionId(null)
        setMessages([])
        setTurns([])
        setSuggestions([])
      }
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToDeleteSession'))
    }
  })

  const sendMessage = useCallback(async (message: string, options: SendHomeMessageOptions = {}) => {
    let sessionId = currentSessionId

    if (!sessionId) {
      try {
        const defaultTitle = message.length > 30 ? `${message.substring(0, 30)}...` : message
        const newSession = await homeChatApi.createSession({ title: defaultTitle })
        sessionId = newSession.id
        setCurrentSessionId(sessionId)
        queryClient.invalidateQueries({ queryKey: ['homeChatSessions'] })
      } catch (err: unknown) {
        const error = err as { response?: { data?: { detail?: string } }, message?: string };
        toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
        return
      }
    }

    const userMessage: SourceChatMessage = {
      id: `temp-${Date.now()}`,
      type: 'human',
      content: message,
      timestamp: new Date().toISOString()
    }
    setMessages((prev) => [...prev, userMessage])
    setIsStreaming(true)
    setSuggestions([])

    // Reserve the turn slot for the AI answer we're about to stream.
    setTurns((prev) => [...prev, {}])

    // Tracked outside the try so the error path can roll the whole
    // unfinished turn back (question + partial answer + reserved slot).
    let streamAiMessageId: string | null = null

    const consumeStream = async (body: ReadableStream<Uint8Array>) => {
      const reader = body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let sawComplete = false

      const ensureAiMessage = (initialContent: string): void => {
        if (!streamAiMessageId) {
          streamAiMessageId = `ai-${Date.now()}`
          setMessages((prev) => [
            ...prev,
            { id: streamAiMessageId!, type: 'ai', content: initialContent, timestamp: new Date().toISOString() }
          ])
        }
      }

      for (;;) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        // Keep the last incomplete line in buffer (SSE lines arrive split).
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const jsonStr = line.slice(6).trim()
          if (!jsonStr) continue

          let event: HomeChatStreamEvent
          try {
            event = JSON.parse(jsonStr)
          } catch {
            // Incomplete JSON stays skipped — don't kill the stream.
            continue
          }

          switch (event.type) {
            case 'answer_delta': {
              // Progressive answer text — append to the in-progress AI message.
              const delta = event.content ?? ''
              if (!delta) break
              ensureAiMessage(delta)
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === streamAiMessageId ? { ...msg, content: msg.content + delta } : msg
                )
              )
              break
            }
            case 'final_answer': {
              const content = event.content ?? ''
              ensureAiMessage(content)
              setMessages((prev) =>
                prev.map((msg) => (msg.id === streamAiMessageId ? { ...msg, content } : msg))
              )
              break
            }
            case 'suggestions':
              setSuggestions(event.suggestions ?? [])
              setTurns((prev) => {
                const next = [...prev]
                next[next.length - 1] = { ...next[next.length - 1], suggestions: event.suggestions ?? [] }
                return next
              })
              break
            case 'complete':
              sawComplete = true
              break
            case 'error':
              throw new Error(event.message || 'Stream error')
            default:
              // user_message (already optimistic), strategy/answer stages,
              // and any legacy/unknown event types from an older backend
              // (e.g. analytics_answer) — ignored gracefully.
              break
          }
        }
      }

      // A stream that ends without `complete` is an error, not a success —
      // never leave the user staring at a question with no reply (#57).
      if (!sawComplete) {
        throw new Error(t('apiErrors.streamIncomplete'))
      }
    }

    try {
      const sendRequest = () =>
        homeChatApi.sendMessage(sessionId, {
          message,
          ...(options.notebookIds && options.notebookIds.length > 0
            ? { notebook_ids: options.notebookIds }
            : {}),
          strategy_model: options.models?.strategy,
          answer_model: options.models?.answer,
          final_answer_model: options.models?.finalAnswer
        })

      const response = await sendRequest()
      if (!response) {
        throw new Error('No response body')
      }
      try {
        await consumeStream(response)
      } catch (streamError) {
        if (!isTransportError(streamError)) throw streamError
        // One transparent retry: the transport (browser ↔ dev proxy) aborted
        // and the server turn dies with the connection. Re-ask; the server
        // dedupes the repeated question in the checkpoint.
        const retryResponse = await sendRequest()
        if (!retryResponse) {
          throw new Error('No response body')
        }
        await consumeStream(retryResponse)
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Error sending home chat message:', error)
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToSendMessage'))
      // Roll back the whole unfinished turn: optimistic question, any
      // partially streamed answer, and the reserved turn slot.
      setMessages((prev) =>
        prev.filter((msg) => !msg.id.startsWith('temp-') && msg.id !== streamAiMessageId)
      )
      setTurns((prev) => prev.slice(0, -1))
    } finally {
      setIsStreaming(false)
      // The checkpoint is authoritative — refetch to get persisted messages.
      refetchCurrentSession()
    }
  }, [currentSessionId, refetchCurrentSession, queryClient, t])

  const cancelStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsStreaming(false)
    }
  }, [])

  const switchSession = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId)
    setSuggestions([])
  }, [])

  const newSession = useCallback(() => {
    setCurrentSessionId(null)
    setMessages([])
    setTurns([])
    setSuggestions([])
  }, [])

  return {
    sessions,
    currentSession: sessions.find((s) => s.id === currentSessionId),
    currentSessionId,
    messages,
    turns,
    suggestions,
    isStreaming,
    loadingSessions,
    createSession: createSessionMutation.mutate,
    updateSession: (sessionId: string, data: UpdateHomeChatSessionRequest) =>
      updateSessionMutation.mutate({ sessionId, data }),
    deleteSession: deleteSessionMutation.mutate,
    switchSession,
    newSession,
    sendMessage,
    cancelStreaming,
    refetchSessions
  }
}

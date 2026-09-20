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

    try {
      const response = await homeChatApi.sendMessage(sessionId, {
        message,
        ...(options.notebookIds && options.notebookIds.length > 0
          ? { notebook_ids: options.notebookIds }
          : {}),
        strategy_model: options.models?.strategy,
        answer_model: options.models?.answer,
        final_answer_model: options.models?.finalAnswer
      })

      if (!response) {
        throw new Error('No response body')
      }

      const reader = response.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let aiMessageId: string | null = null

      while (true) {
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
            case 'final_answer': {
              const content = event.content ?? ''
              if (!aiMessageId) {
                aiMessageId = `ai-${Date.now()}`
                setMessages((prev) => [
                  ...prev,
                  { id: aiMessageId!, type: 'ai', content, timestamp: new Date().toISOString() }
                ])
              } else {
                setMessages((prev) =>
                  prev.map((msg) => (msg.id === aiMessageId ? { ...msg, content } : msg))
                )
              }
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
            case 'error':
              throw new Error(event.message || 'Stream error')
            default:
              // user_message (already optimistic), strategy/answer stages,
              // complete and any legacy/unknown event types from an older
              // backend (e.g. analytics_answer) — the final answer / session
              // refetch cover these; unknown types are ignored gracefully.
              break
          }
        }
      }
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Error sending home chat message:', error)
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToSendMessage'))
      setMessages((prev) => prev.filter((msg) => !msg.id.startsWith('temp-')))
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

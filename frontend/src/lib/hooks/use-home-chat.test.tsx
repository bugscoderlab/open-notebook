/* eslint-disable @typescript-eslint/no-explicit-any */
import { ReactNode } from 'react'
import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useHomeChat } from './use-home-chat'
import { homeChatApi } from '@/lib/api/home-chat'

vi.mock('@/lib/api/home-chat', () => ({
  homeChatApi: {
    createSession: vi.fn(),
    listSessions: vi.fn(),
    getSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    sendMessage: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

const MODELS = { strategy: 'm:1', answer: 'm:1', finalAnswer: 'm:1' }

function sseStream(events: Array<Record<string, unknown>>) {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      }
      controller.close()
    },
  })
}

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}

describe('useHomeChat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(homeChatApi.listSessions).mockResolvedValue([])
    // Never resolves: simulates a server that persists exactly what was
    // streamed, so the post-turn refetch doesn't clobber streamed state.
    vi.mocked(homeChatApi.getSession).mockReturnValue(new Promise(() => {}) as any)
  })

  it('auto-creates a session on the first message and completes a knowledge turn', async () => {
    vi.mocked(homeChatApi.createSession).mockResolvedValue({
      id: 'home_chat_session:1',
      title: 'why?',
      created: '',
      updated: '',
    } as any)
    vi.mocked(homeChatApi.sendMessage).mockResolvedValue(
      sseStream([
        { type: 'user_message', content: 'why?' },
        { type: 'route', route: 'knowledge' },
        { type: 'strategy', reasoning: 'r', searches: [] },
        { type: 'answer', content: 'partial' },
        { type: 'final_answer', content: 'the final answer' },
        { type: 'suggestions', suggestions: ['Tell me more?'] },
        { type: 'complete', final_answer: 'the final answer' },
      ]) as any
    )

    const { result } = renderHook(() => useHomeChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('why?', { models: MODELS })
    })

    expect(vi.mocked(homeChatApi.createSession)).toHaveBeenCalledWith({ title: 'why?' })
    expect(vi.mocked(homeChatApi.sendMessage).mock.calls[0][0]).toBe('home_chat_session:1')
    expect(vi.mocked(homeChatApi.sendMessage).mock.calls[0][1]).toMatchObject({
      message: 'why?',
      strategy_model: 'm:1',
    })

    expect(result.current.isStreaming).toBe(false)
    const contents = result.current.messages.map((m) => m.content)
    expect(contents).toEqual(['why?', 'the final answer'])
    expect(result.current.suggestions).toEqual(['Tell me more?'])
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.turns[0].suggestions).toEqual(['Tell me more?'])
    expect(result.current.turns[0].analytics_answer).toBeUndefined()
  })

  it('attaches the analytics payload on an analytics turn', async () => {
    vi.mocked(homeChatApi.createSession).mockResolvedValue({
      id: 'home_chat_session:1',
      title: 'top spender?',
      created: '',
      updated: '',
    } as any)
    const analyticsAnswer = {
      query_id: 'q:1',
      status: 'ok',
      answer_text: 'Sarah spent MYR 100.',
      kpis: [],
      table: null,
      chart: null,
      scope: null,
      freshness_at: null,
      query_template: null,
    }
    vi.mocked(homeChatApi.sendMessage).mockResolvedValue(
      sseStream([
        { type: 'route', route: 'analytics' },
        { type: 'analytics_answer', data: analyticsAnswer },
        { type: 'suggestions', suggestions: [] },
        { type: 'complete' },
      ]) as any
    )

    const { result } = renderHook(() => useHomeChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('top spender?', { models: MODELS })
    })

    const ai = result.current.messages.find((m) => m.type === 'ai')
    expect(ai?.content).toBe('Sarah spent MYR 100.')
    expect(result.current.turns[0].analytics_answer).toMatchObject({ status: 'ok' })
  })

  it('sends notebook scope only when non-empty', async () => {
    vi.mocked(homeChatApi.createSession).mockResolvedValue({
      id: 'home_chat_session:1',
      title: 'q',
      created: '',
      updated: '',
    } as any)
    vi.mocked(homeChatApi.sendMessage).mockResolvedValue(
      sseStream([{ type: 'complete' }]) as any
    )

    const { result } = renderHook(() => useHomeChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('q', { models: MODELS, notebookIds: ['notebook:a'] })
    })
    expect(vi.mocked(homeChatApi.sendMessage).mock.calls[0][1]).toMatchObject({
      notebook_ids: ['notebook:a'],
    })

    await act(async () => {
      await result.current.sendMessage('q again', { models: MODELS, notebookIds: [] })
    })
    expect(vi.mocked(homeChatApi.sendMessage).mock.calls[1][1]).not.toHaveProperty(
      'notebook_ids'
    )
  })

  it('keeps the optimistic question while the fresh session query resolves mid-stream', async () => {
    // Regression: the first message auto-creates the session, its query
    // resolves immediately with EMPTY messages, and the sync effect used to
    // wipe the optimistic question mid-stream — it only reappeared with the
    // answer after the post-turn refetch.
    vi.mocked(homeChatApi.createSession).mockResolvedValue({
      id: 'home_chat_session:1',
      title: 'q',
      created: '',
      updated: '',
    } as any)
    let getSessionCalls = 0
    vi.mocked(homeChatApi.getSession).mockImplementation((() => {
      getSessionCalls += 1
      // First snapshot (query fired when the session was created): empty.
      // Later snapshots (post-turn refetch): persisted question + answer.
      if (getSessionCalls === 1) {
        return Promise.resolve({ messages: [], turns: [] } as any)
      }
      return Promise.resolve({
        id: 'home_chat_session:1',
        title: 'q',
        created: '',
        updated: '',
        messages: [
          { id: 'm1', type: 'human', content: 'q' },
          { id: 'm2', type: 'ai', content: 'the answer' },
        ],
        turns: [{ suggestions: ['Next?'] }],
      } as any)
    }) as any)

    // Slow stream: gives the empty session snapshot time to land mid-flight.
    const encoder = new TextEncoder()
    vi.mocked(homeChatApi.sendMessage).mockResolvedValue(
      new ReadableStream<Uint8Array>({
        async start(controller) {
          await new Promise((r) => setTimeout(r, 40))
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify({ type: 'final_answer', content: 'the answer' })}\n\n`)
          )
          await new Promise((r) => setTimeout(r, 10))
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify({ type: 'suggestions', suggestions: ['Next?'] })}\n\n`)
          )
          controller.enqueue(encoder.encode(`data: ${JSON.stringify({ type: 'complete' })}\n\n`))
          controller.close()
        },
      }) as any
    )

    const { result } = renderHook(() => useHomeChat(), { wrapper })

    let send!: Promise<void>
    await act(async () => {
      send = result.current.sendMessage('q', { models: MODELS })
      // Let the session query resolve and its sync effect attempt mid-stream.
      await new Promise((r) => setTimeout(r, 15))
    })

    // The question must still be on screen while the answer streams in.
    expect(result.current.messages.map((m) => m.content)).toEqual(['q'])

    await act(async () => {
      await send
    })

    expect(result.current.messages.map((m) => m.content)).toEqual(['q', 'the answer'])
    expect(result.current.turns).toHaveLength(1)
    expect(result.current.suggestions).toEqual(['Next?'])
  })

  it('drops the optimistic message on an in-band error event', async () => {
    vi.mocked(homeChatApi.createSession).mockResolvedValue({
      id: 'home_chat_session:1',
      title: 'q',
      created: '',
      updated: '',
    } as any)
    vi.mocked(homeChatApi.sendMessage).mockResolvedValue(
      sseStream([{ type: 'error', message: 'provider down' }]) as any
    )

    const { result } = renderHook(() => useHomeChat(), { wrapper })

    await act(async () => {
      await result.current.sendMessage('q', { models: MODELS })
    })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(result.current.turns).toEqual([])
  })
})

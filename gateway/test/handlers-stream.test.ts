import { describe, expect, it, vi } from 'vitest'

import type { ApiClient, StreamTurnEvent } from '../src/api-client.js'
import { createChatHandlers, type ChatContext } from '../src/handlers.js'

const TIMING = {
  typingIntervalMs: 60_000,
  interimAfterMs: 60_000,
  chunkPacingMs: 0,
  streamMinChars: 10,
  streamMaxChars: 50,
  sleepFn: () => Promise.resolve(),
}

/** Fake ChatContext capturing replies and quoting. */
function makeCtx() {
  const replies: Array<{ text: string; quote?: boolean }> = []
  const ctx: ChatContext = {
    chatId: 'chat:1',
    messageId: 'm1',
    text: 'question?',
    reply: async (text, extra) => {
      replies.push({ text, quote: extra?.quote })
    },
    sendTyping: async () => {},
  }
  return { ctx, replies }
}

function clientWith(stream?: AsyncGenerator<StreamTurnEvent>) {
  return {
    message: vi.fn().mockResolvedValue({
      reply: 'fallback answer',
      suggestions: [],
      conversation_reset: false,
    }),
    claim: vi.fn().mockResolvedValue({ email: 'a@b.c' }),
    messageStream:
      stream === undefined
        ? undefined
        : vi.fn().mockReturnValue(stream),
  } as unknown as Pick<ApiClient, 'message' | 'claim'> &
    Partial<Pick<ApiClient, 'messageStream'>>
}

async function* scripted(...events: StreamTurnEvent[]): AsyncGenerator<StreamTurnEvent> {
  for (const event of events) yield event
}

describe('streaming replies (shared by WhatsApp and Telegram)', () => {
  it('buffers deltas into paced segments; first segment quotes', async () => {
    const { ctx, replies } = makeCtx()
    const stream = scripted(
      { type: 'answer_delta', content: 'Hello there. ' },
      { type: 'answer_delta', content: 'General Kenobi. ' },
      { type: 'final_answer', content: 'Hello there. General Kenobi.' },
      { type: 'suggestions', suggestions: ['Again?'] },
      { type: 'complete', final_answer: 'Hello there. General Kenobi.', suggestions: ['Again?'] },
    )
    const handlers = createChatHandlers(clientWith(stream), 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    expect(replies.map((r) => r.text)).toEqual([
      'Hello there. ',
      'General Kenobi. ',
      'You could also ask:\n· Again?',
    ])
    expect(replies[0].quote).toBe(true)
    expect(replies[1].quote).toBeUndefined()
  })

  it('never sends interim text — typing only', async () => {
    const { ctx, replies } = makeCtx()
    const stream = scripted(
      { type: 'answer_delta', content: 'Answer.' },
      { type: 'complete', final_answer: 'Answer.', suggestions: [] },
    )
    const handlers = createChatHandlers(clientWith(stream), 'telegram', TIMING)

    await handlers.handleText(ctx)

    expect(replies.every((r) => !r.text.includes('Still working'))).toBe(true)
  })

  it('falls back to the non-streaming path when nothing was delivered', async () => {
    const { ctx, replies } = makeCtx()
    const client = clientWith(
      scripted({ type: 'error', message: 'provider exploded' }),
    )
    const handlers = createChatHandlers(client, 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    // The one-shot path answered instead of the broken stream.
    expect(client.message).toHaveBeenCalledWith('whatsapp', 'chat:1', 'question?')
    expect(replies[0].text).toBe('fallback answer')
  })

  it('falls back when the stream transport fails before the first byte', async () => {
    const { ctx, replies } = makeCtx()
    async function* broken(): AsyncGenerator<StreamTurnEvent> {
      throw new Error('socket reset')
      yield { type: 'complete' } // pragma: no cover
    }
    const client = clientWith(broken())
    const handlers = createChatHandlers(client, 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    expect(client.message).toHaveBeenCalled()
    expect(replies[0].text).toBe('fallback answer')
  })

  it('apologizes instead of duplicating after partial content', async () => {
    const { ctx, replies } = makeCtx()
    const stream = scripted(
      { type: 'answer_delta', content: 'Partial answer so far. ' },
      { type: 'error', message: 'provider exploded' },
    )
    const client = clientWith(stream)
    const handlers = createChatHandlers(client, 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    // No fallback call (would duplicate), an apology tail instead.
    expect(client.message).not.toHaveBeenCalled()
    const last = replies[replies.length - 1]
    expect(last.text).toContain('lost the connection')
  })

  it('uses the non-streaming path when messageStream is absent (old API)', async () => {
    const { ctx, replies } = makeCtx()
    const client = clientWith(undefined)
    const handlers = createChatHandlers(client, 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    expect(client.message).toHaveBeenCalled()
    expect(replies[0].text).toBe('fallback answer')
  })

  it('hard-flushes oversized segments at the buffer cap', async () => {
    const { ctx, replies } = makeCtx()
    const big = 'y'.repeat(120) // > streamMaxChars 50, no boundaries
    const stream = scripted(
      { type: 'answer_delta', content: big },
      { type: 'complete', final_answer: big, suggestions: [] },
    )
    const handlers = createChatHandlers(clientWith(stream), 'whatsapp', TIMING)

    await handlers.handleText(ctx)

    expect(replies.map((r) => r.text)).toEqual(['y'.repeat(50), 'y'.repeat(50), 'y'.repeat(20)])
  })

  it('calls messageStream with its this-binding intact (class client)', async () => {
    // Regression: the handler once detached the method into a bare
    // reference, so `this.call(...)` threw pre-request and every reply
    // silently fell back to the one-shot path. A class-based client
    // exercises the real binding.
    const { ctx, replies } = makeCtx()

    class ClassClient {
      message = vi.fn().mockResolvedValue({
        reply: 'fallback answer',
        suggestions: [],
        conversation_reset: false,
      })
      claim = vi.fn().mockResolvedValue({ email: 'a@b.c' })

      async *messageStream(
        _platform: string,
        _externalId: string,
        _text: string,
      ): AsyncGenerator<StreamTurnEvent> {
        yield { type: 'answer_delta', content: 'Streamed answer. ' }
        yield { type: 'suggestions', suggestions: ['More?'] }
        yield { type: 'complete', final_answer: 'Streamed answer.', suggestions: ['More?'] }
      }
    }

    const client = new ClassClient()
    const handlers = createChatHandlers(
      client as unknown as Pick<ApiClient, 'message' | 'claim'> &
        Partial<Pick<ApiClient, 'messageStream'>>,
      'whatsapp',
      TIMING,
    )

    await handlers.handleText(ctx)

    expect(client.message).not.toHaveBeenCalled()
    expect(replies.map((r) => r.text)).toEqual(['Streamed answer. ', 'You could also ask:\n· More?'])
  })
})

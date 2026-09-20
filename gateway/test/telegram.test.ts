import { describe, expect, it, vi } from 'vitest'

import type { ApiClient } from '../src/api-client.js'
import { ApiClientError } from '../src/api-client.js'
import {
  buildErrorReply,
  buildSuggestionsText,
  createTelegramHandlers,
  LINK_INSTRUCTIONS,
  START_COMMAND_RE,
  type ChatContext,
} from '../src/telegram.js'

const TIMING = { typingIntervalMs: 10_000, interimAfterMs: 10_000, chunkPacingMs: 0, sleepFn: () => Promise.resolve() }

function makeCtx(text?: string) {
  const replies: Array<{ text: string; quoted?: number }> = []
  const typings: number[] = []
  const ctx: ChatContext = {
    chatId: '4242',
    messageId: 7,
    text,
    reply: async (t, extra) => {
      // Mirror what the grammY wiring does with the neutral quote flag.
      replies.push({ text: t, quoted: extra?.quote ? 7 : undefined })
    },
    sendTyping: async () => {
      typings.push(typings.length)
    },
  }
  return { ctx, replies, typings }
}

function mockClient(overrides: Partial<Record<keyof Pick<ApiClient, 'message' | 'claim'>, unknown>> = {}) {
  return {
    message: overrides.message ?? vi.fn().mockResolvedValue({ reply: 'ok', suggestions: [], conversation_reset: false }),
    claim: overrides.claim ?? vi.fn().mockResolvedValue({ email: 'a@b.c' }),
  } as unknown as Pick<ApiClient, 'message' | 'claim'>
}

describe('start command regex', () => {
  it('matches /start CODE and /start@bot CODE', () => {
    expect(START_COMMAND_RE.exec('/start 123456')?.[1]).toBe('123456')
    expect(START_COMMAND_RE.exec('/start@open_notebook_bot 654321')?.[1]).toBe('654321')
    expect(START_COMMAND_RE.exec('/start')).toBeNull()
    expect(START_COMMAND_RE.exec('hello')).toBeNull()
  })
})

describe('handleStart (claim flow)', () => {
  it('claims the code and confirms with the linked email', async () => {
    const client = mockClient()
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies } = makeCtx('/start 123456')

    await handlers.handleStart(ctx, '123456')

    expect(client.claim).toHaveBeenCalledWith('telegram', '4242', '123456')
    expect(replies[0].text).toContain('a@b.c')
  })

  it('maps a failed claim to a chat-friendly reply', async () => {
    const client = mockClient({
      claim: vi.fn().mockRejectedValue(new ApiClientError(400, 'Invalid or expired linking code')),
    })
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies } = makeCtx()

    await handlers.handleStart(ctx, '000000')

    expect(replies[0].text).toContain('expired')
  })
})

describe('handleText (conversational ask)', () => {
  it('sends the message to the API and quotes the question in the first chunk', async () => {
    const client = mockClient({
      message: vi.fn().mockResolvedValue({ reply: 'the answer', suggestions: [], conversation_reset: false }),
    })
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies, typings } = makeCtx('what is x?')

    await handlers.handleText(ctx)

    expect(client.message).toHaveBeenCalledWith('telegram', '4242', 'what is x?')
    expect(replies[0]).toEqual({ text: 'the answer', quoted: 7 })
    expect(typings.length).toBeGreaterThan(0) // typing indicator fired
  })

  it('chunks long replies and appends suggestions as plain text', async () => {
    const longReply = `${'a'.repeat(3900)}\n\n${'b'.repeat(3900)}`
    const client = mockClient({
      message: vi.fn().mockResolvedValue({
        reply: longReply,
        suggestions: ['next one?', 'next two?'],
        conversation_reset: false,
      }),
    })
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies } = makeCtx('long')

    await handlers.handleText(ctx)

    expect(replies.length).toBe(3) // 2 chunks + suggestions
    expect(replies[0].text.length).toBeLessThanOrEqual(4000)
    expect(replies[1].text.length).toBeLessThanOrEqual(4000)
    expect(replies[2].text).toBe(buildSuggestionsText(['next one?', 'next two?']))
  })

  it('404 from the API renders the how-to-link instructions', async () => {
    const client = mockClient({
      message: vi.fn().mockRejectedValue(new ApiClientError(404, 'No integration link found')),
    })
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies } = makeCtx('hi')

    await handlers.handleText(ctx)

    expect(replies[0].text).toBe(LINK_INSTRUCTIONS)
  })

  it('429 renders the rate-limit message from the API', async () => {
    const client = mockClient({
      message: vi.fn().mockRejectedValue(new ApiClientError(429, "You're sending messages too quickly")),
    })
    const handlers = createTelegramHandlers(client, TIMING)
    const { ctx, replies } = makeCtx('hi')

    await handlers.handleText(ctx)

    expect(replies[0].text).toContain('too quickly')
  })
})

describe('handleNonText', () => {
  it('replies with the text-only note', async () => {
    const handlers = createTelegramHandlers(mockClient(), TIMING)
    const { ctx, replies } = makeCtx()

    await handlers.handleNonText(ctx)

    expect(replies[0].text).toContain('text')
  })
})

describe('buildErrorReply', () => {
  it('maps statuses to friendly text', () => {
    expect(buildErrorReply(new ApiClientError(404, 'x'))).toBe(LINK_INSTRUCTIONS)
    expect(buildErrorReply(new ApiClientError(403, 'nope'))).toBe('nope')
    expect(buildErrorReply(new ApiClientError(500, ''))).toMatch(/try again/i)
  })
})

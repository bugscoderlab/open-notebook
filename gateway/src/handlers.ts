import { ApiClientError, type ApiClient, type StreamTurnEvent } from './api-client.js'
import { chunkMessage } from './chunk.js'
import { StreamBuffer } from './stream-buffer.js'
import { sleep, startTypingLoop } from './typing.js'

/**
 * `/start 123456` — the linking-code command, identical on every platform
 * (bot-name suffix tolerated: /start@bot 123456).
 */
export const START_COMMAND_RE = /^\/start(?:@\w+)?\s+(\d{6})$/

const TYPING_INTERVAL_MS = 4_000 // Telegram clears typing after ~5s
const INTERIM_AFTER_MS = 45_000 // one "still working" message past this
const CHUNK_PACING_MS = 1_100 // stay under ~1 msg/sec per-chat guidance
const STREAM_MIN_CHARS = 400 // a streamed chunk holds at least this…
const STREAM_MAX_CHARS = 1_200 // …and hard-flushes at this, boundary or not

/** Sent when a stream dies after partial content reached the user. */
const STREAM_APOLOGY =
  'Sorry — I lost the connection finishing that answer. Please ask again.'

export const LINK_INSTRUCTIONS =
  "You're not linked to an Open Notebook account yet.\n\n" +
  'To connect: open Open Notebook → Settings → Chat integrations → ' +
  'Connect, then send me the code as "/start <code>" here.'

export function buildSuggestionsText(suggestions: string[]): string {
  return (
    'You could also ask:\n' + suggestions.map((suggestion) => `· ${suggestion}`).join('\n')
  )
}

/** Map an API error to chat-friendly text (the API's detail strings are
 * already user-facing; we only special-case the how-to-link surface). */
export function buildErrorReply(error: ApiClientError): string {
  if (error.status === 404) return LINK_INSTRUCTIONS
  if (error.status === 401 || error.status === 403) {
    return (
      error.message ||
      'This account is no longer authorized. Ask your admin to check your access.'
    )
  }
  return error.message || 'Something went wrong — please try again in a moment.'
}

/** Minimal context surface the handlers need — keeps the logic unit-testable
 * without a platform context. `extra.quote` marks the reply that should
 * quote the user's message (platform adapters map it to their native
 * quoting: Telegram reply_parameters, WhatsApp quoted). */
export interface ChatContext {
  chatId: string
  messageId: number | string
  text?: string
  reply(text: string, extra?: { quote?: boolean }): Promise<unknown>
  sendTyping(): Promise<unknown>
}

export interface HandlerTiming {
  typingIntervalMs?: number
  interimAfterMs?: number
  chunkPacingMs?: number
  streamMinChars?: number
  streamMaxChars?: number
  sleepFn?: (ms: number) => Promise<void>
}

/** Handler deps: the non-streaming surface is required; streaming is
 * optional — an older API (or a test double without it) transparently
 * falls back to the one-shot message() path. */
export type ChatHandlerClient = Pick<ApiClient, 'message' | 'claim'> &
  Partial<Pick<ApiClient, 'messageStream'>>

/**
 * Platform-agnostic chat handlers (decisions #41): typing loop for the whole
 * wait, one interim message past 45s, paragraph-chunked replies quoted
 * against the question, suggestions appended as plain text, text-only reply
 * to non-text input. `/start <code>` is intercepted for the claim flow.
 */
export function createChatHandlers(
  client: ChatHandlerClient,
  platform: string,
  timing: HandlerTiming = {},
) {
  const typingInterval = timing.typingIntervalMs ?? TYPING_INTERVAL_MS
  const interimAfter = timing.interimAfterMs ?? INTERIM_AFTER_MS
  const chunkPacing = timing.chunkPacingMs ?? CHUNK_PACING_MS
  const streamMinChars = timing.streamMinChars ?? STREAM_MIN_CHARS
  const streamMaxChars = timing.streamMaxChars ?? STREAM_MAX_CHARS
  const pause = timing.sleepFn ?? sleep

  async function handleStart(ctx: ChatContext, code: string): Promise<void> {
    try {
      const { email } = await client.claim(platform, ctx.chatId, code)
      await ctx.reply(
        `Linked as ${email}. Ask me anything about your notebooks — /help lists the special commands.`,
      )
    } catch (error) {
      await ctx.reply(buildErrorReply(error as ApiClientError))
    }
  }

  /**
   * Streaming reply: buffer token deltas into paced segments (first one
   * quotes the question). Returns the turn's suggestions.
   *
   * Failure semantics (locked in map #56): anything that goes wrong BEFORE
   * the first content reaches the user throws — the caller falls back to
   * the non-streaming path. A failure AFTER partial content was delivered
   * sends an honest apology tail instead (a fallback would duplicate).
   */
  async function streamReply(
    ctx: ChatContext,
    text: string,
  ): Promise<{ suggestions: string[] }> {
    const messageStream = client.messageStream
    if (!messageStream) {
      throw new ApiClientError(0, 'Streaming not supported by this API')
    }
    const buffer = new StreamBuffer({
      minChars: streamMinChars,
      maxChars: streamMaxChars,
    })
    let first = true
    let queuedAny = false
    let suggestions: string[] = []
    // Sends run on a chained promise so pacing holds even when deltas
    // arrive faster than the per-chat message rate allows.
    let chain: Promise<unknown> = Promise.resolve()
    const queue = (segment: string) => {
      queuedAny = true
      const quote = first
      first = false
      chain = chain.then(async () => {
        await ctx.reply(segment, quote ? { quote: true } : undefined)
        await pause(chunkPacing)
      })
    }

    try {
      for await (const event of messageStream(platform, ctx.chatId, text)) {
        if (event.type === 'answer_delta') {
          for (const segment of buffer.push(event.content ?? '')) queue(segment)
        } else if (event.type === 'suggestions') {
          suggestions = event.suggestions ?? []
        } else if (event.type === 'complete') {
          for (const segment of buffer.finish()) queue(segment)
          await chain
          return { suggestions }
        } else if (event.type === 'error') {
          for (const segment of buffer.finish()) queue(segment)
          await chain
          if (!queuedAny) {
            throw new ApiClientError(0, event.message || 'Stream error')
          }
          await ctx.reply(STREAM_APOLOGY)
          return { suggestions }
        }
      }
      // EOF without complete/error (defensive — messageStream throws first).
      throw new ApiClientError(0, 'Stream ended before completion')
    } catch (error) {
      if (!queuedAny) throw error // nothing delivered — caller falls back
      for (const segment of buffer.finish()) queue(segment)
      await chain
      await ctx.reply(STREAM_APOLOGY)
      return { suggestions }
    }
  }

  async function sendChunked(ctx: ChatContext, reply: string): Promise<void> {
    const chunks = chunkMessage(reply)
    for (let index = 0; index < chunks.length; index += 1) {
      await ctx.reply(chunks[index], index === 0 ? { quote: true } : undefined)
      if (index < chunks.length - 1) await pause(chunkPacing)
    }
  }

  async function handleText(ctx: ChatContext): Promise<void> {
    const text = ctx.text ?? ''
    const start = START_COMMAND_RE.exec(text)
    if (start) {
      await handleStart(ctx, start[1])
      return
    }

    const typing = startTypingLoop(() => ctx.sendTyping(), typingInterval)
    const interimTimer = setTimeout(() => {
      void ctx.reply('Still working — checking your notebooks…').catch(() => {})
    }, interimAfter)
    interimTimer.unref?.()

    try {
      let suggestions: string[] = []
      if (client.messageStream) {
        try {
          // Streaming path (preferred): progressive chunks, silent.
          suggestions = (await streamReply(ctx, text)).suggestions
        } catch {
          // The stream failed before anything reached the user — fall back
          // to the one-shot path rather than erroring out.
          const result = await client.message(platform, ctx.chatId, text)
          await sendChunked(ctx, result.reply)
          suggestions = result.suggestions
        }
      } else {
        // Old API without the streaming endpoint.
        const result = await client.message(platform, ctx.chatId, text)
        await sendChunked(ctx, result.reply)
        suggestions = result.suggestions
      }
      typing.stop()
      clearTimeout(interimTimer)

      if (suggestions.length > 0) {
        await pause(chunkPacing)
        await ctx.reply(buildSuggestionsText(suggestions))
      }
    } catch (error) {
      typing.stop()
      clearTimeout(interimTimer)
      await ctx.reply(buildErrorReply(error as ApiClientError))
    }
  }

  async function handleNonText(ctx: ChatContext): Promise<void> {
    await ctx.reply('I can only read text for now — send me a question as a message.')
  }

  return { handleStart, handleText, handleNonText }
}

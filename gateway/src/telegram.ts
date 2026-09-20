import { autoRetry } from '@grammyjs/auto-retry'
import { run, sequentialize, type RunnerHandle } from '@grammyjs/runner'
import { Bot } from 'grammy'

import type { ApiClient, ApiClientError } from './api-client.js'
import { chunkMessage } from './chunk.js'
import { sleep, startTypingLoop } from './typing.js'

/** Platform identifier the API expects. */
export const PLATFORM = 'telegram'

/** `/start 123456` (bot-name suffix tolerated: /start@bot 123456). */
export const START_COMMAND_RE = /^\/start(?:@\w+)?\s+(\d{6})$/

const TYPING_INTERVAL_MS = 4_000 // Telegram clears typing after ~5s
const INTERIM_AFTER_MS = 45_000 // one "still working" message past this
const CHUNK_PACING_MS = 1_100 // stay under the ~1 msg/sec per-chat guidance

export const LINK_INSTRUCTIONS =
  "You're not linked to an Open Notebook account yet.\n\n" +
  'To connect: open Open Notebook → Settings → Chat integrations → ' +
  'Connect Telegram, then send me the code as "/start <code>" here.'

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
 * without a grammY context. */
export interface ChatContext {
  chatId: string
  messageId: number
  text?: string
  reply(text: string, extra?: { reply_parameters?: { message_id: number } }): Promise<unknown>
  sendTyping(): Promise<unknown>
}

export interface HandlerTiming {
  typingIntervalMs?: number
  interimAfterMs?: number
  chunkPacingMs?: number
  sleepFn?: (ms: number) => Promise<void>
}

export function createTelegramHandlers(
  client: Pick<ApiClient, 'message' | 'claim'>,
  timing: HandlerTiming = {},
) {
  const typingInterval = timing.typingIntervalMs ?? TYPING_INTERVAL_MS
  const interimAfter = timing.interimAfterMs ?? INTERIM_AFTER_MS
  const chunkPacing = timing.chunkPacingMs ?? CHUNK_PACING_MS
  const pause = timing.sleepFn ?? sleep

  async function handleStart(ctx: ChatContext, code: string): Promise<void> {
    try {
      const { email } = await client.claim(PLATFORM, ctx.chatId, code)
      await ctx.reply(
        `Linked as ${email}. Ask me anything about your notebooks — /help lists the special commands.`,
      )
    } catch (error) {
      await ctx.reply(buildErrorReply(error as ApiClientError))
    }
  }

  async function handleText(ctx: ChatContext): Promise<void> {
    const text = ctx.text ?? ''
    const typing = startTypingLoop(() => ctx.sendTyping(), typingInterval)
    const interimTimer = setTimeout(() => {
      void ctx.reply('Still working — checking your notebooks…').catch(() => {})
    }, interimAfter)
    interimTimer.unref?.()

    try {
      const result = await client.message(PLATFORM, ctx.chatId, text)
      typing.stop()
      clearTimeout(interimTimer)

      const chunks = chunkMessage(result.reply)
      for (let index = 0; index < chunks.length; index += 1) {
        await ctx.reply(
          chunks[index],
          index === 0 ? { reply_parameters: { message_id: ctx.messageId } } : undefined,
        )
        if (index < chunks.length - 1) await pause(chunkPacing)
      }
      if (result.suggestions.length > 0) {
        await pause(chunkPacing)
        await ctx.reply(buildSuggestionsText(result.suggestions))
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

export interface TelegramAdapter {
  stop: () => Promise<void>
}

/**
 * Wire the handlers into a grammY bot: auto-retry on transient API errors,
 * per-chat sequentialize (one user's 90s ask never blocks another user),
 * long polling via the runner (no inbound ports), graceful SIGTERM stop.
 */
export function startTelegramAdapter(
  token: string,
  client: ApiClient,
  log: (msg: string) => void,
): TelegramAdapter {
  const bot = new Bot(token)
  bot.api.config.use(autoRetry())
  bot.use(sequentialize((ctx) => [String(ctx.chat?.id ?? ctx.from?.id ?? 'unknown')]))

  const handlers = createTelegramHandlers(client)

  bot.on('message:text', (ctx) => {
    const text = ctx.msg.text
    const start = START_COMMAND_RE.exec(text)
    const chatCtx: ChatContext = {
      chatId: String(ctx.chat.id),
      messageId: ctx.msg.message_id,
      text,
      reply: (t, extra) => ctx.reply(t, extra),
      sendTyping: () => ctx.replyWithChatAction('typing'),
    }
    if (start) return handlers.handleStart(chatCtx, start[1])
    return handlers.handleText(chatCtx)
  })

  bot.on('message', (ctx) => {
    const chatCtx: ChatContext = {
      chatId: String(ctx.chat.id),
      messageId: ctx.msg.message_id,
      reply: (t) => ctx.reply(t),
      sendTyping: () => ctx.replyWithChatAction('typing'),
    }
    return handlers.handleNonText(chatCtx)
  })

  bot.catch((error) => log(`telegram adapter error: ${String(error)}`))

  const handle: RunnerHandle = run(bot)
  log('telegram adapter connected (long polling)')
  return {
    stop: async () => {
      await handle.stop()
    },
  }
}

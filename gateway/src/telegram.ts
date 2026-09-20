import { autoRetry } from '@grammyjs/auto-retry'
import { run, sequentialize, type RunnerHandle } from '@grammyjs/runner'
import { Bot } from 'grammy'

import type { ApiClient } from './api-client.js'
import {
  createChatHandlers,
  type ChatContext,
  type HandlerTiming,
} from './handlers.js'

export {
  buildErrorReply,
  buildSuggestionsText,
  LINK_INSTRUCTIONS,
  START_COMMAND_RE,
} from './handlers.js'
export type { ChatContext, HandlerTiming } from './handlers.js'

/** Platform identifier the API expects. */
export const PLATFORM = 'telegram'

export function createTelegramHandlers(
  client: Pick<ApiClient, 'message' | 'claim'>,
  timing: HandlerTiming = {},
) {
  return createChatHandlers(client, PLATFORM, timing)
}

export interface TelegramAdapter {
  stop: () => Promise<void>
}

/**
 * Wire the shared handlers into a grammY bot: auto-retry on transient API
 * errors, per-chat sequentialize (one user's 90s ask never blocks another
 * user), long polling via the runner (no inbound ports), graceful SIGTERM
 * stop.
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
    const chatCtx: ChatContext = {
      chatId: String(ctx.chat.id),
      messageId: ctx.msg.message_id,
      text: ctx.msg.text,
      reply: (text, extra) =>
        ctx.reply(
          text,
          extra?.quote ? { reply_parameters: { message_id: ctx.msg.message_id } } : undefined,
        ),
      sendTyping: () => ctx.replyWithChatAction('typing'),
    }
    return handlers.handleText(chatCtx)
  })

  bot.on('message', (ctx) => {
    const chatCtx: ChatContext = {
      chatId: String(ctx.chat.id),
      messageId: ctx.msg.message_id,
      reply: (text) => ctx.reply(text),
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

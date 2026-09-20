import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  type WASocket,
} from '@whiskeysockets/baileys'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { ApiClient } from './api-client.js'
import {
  createChatHandlers,
  type ChatContext,
  type HandlerTiming,
} from './handlers.js'

export const PLATFORM = 'whatsapp'

/** Presence expires after ~10s — re-send a little sooner. */
const WHATSAPP_TYPING_INTERVAL_MS = 8_000
const RECONNECT_DELAY_MS = 3_000

/** Repo-root data folder, from either src/ (tsx dev) or dist/ (node). */
export const DEFAULT_CREDS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'data',
  'whatsapp-creds',
)

export interface WhatsAppAdapter {
  stop: () => Promise<void>
}

/** Injectable socket factory — the unit tests substitute a scripted fake. */
export type SocketFactory = (
  credsDir: string,
  log: (msg: string) => void,
) => Promise<WASocket>

const defaultSocketFactory: SocketFactory = async (credsDir, log) => {
  const { state, saveCreds } = await useMultiFileAuthState(credsDir)
  const socket = makeWASocket({
    auth: state,
    printQRInTerminal: true,
    markOnlineOnConnect: false,
  })
  socket.ev.on('creds.update', saveCreds)
  log('whatsapp: if pairing for the first time, scan the QR printed above')
  return socket
}

function disconnectStatusCode(
  socket: WASocket | null,
  lastDisconnect: unknown,
): number | undefined {
  const error = (lastDisconnect as { error?: { output?: { statusCode?: number } } } | null)
    ?.error
  return error?.output?.statusCode
}

/**
 * WhatsApp adapter on Baileys (pinned 6.7.x). ToS-gray on purpose: replies
 * only, never a verified business account, low volume (see the user guide's
 * risk warning). Session state persists under the data folder; any close
 * except a real logout reconnects; a logout logs a loud re-pair alert and the
 * next socket prints a fresh QR.
 */
export function createWhatsAppAdapter(deps: {
  client: Pick<ApiClient, 'message' | 'claim'>
  credsDir?: string
  log: (msg: string) => void
  createSocket?: SocketFactory
  timing?: HandlerTiming
  reconnectDelayMs?: number
}): WhatsAppAdapter {
  const credsDir = deps.credsDir ?? DEFAULT_CREDS_DIR
  const factory = deps.createSocket ?? defaultSocketFactory
  const reconnectDelayMs = deps.reconnectDelayMs ?? RECONNECT_DELAY_MS
  const handlers = createChatHandlers(deps.client, PLATFORM, {
    typingIntervalMs: WHATSAPP_TYPING_INTERVAL_MS,
    ...deps.timing,
  })

  let stopped = false
  let socket: WASocket | null = null
  let reconnectTimer: NodeJS.Timeout | null = null

  async function connect(): Promise<void> {
    if (stopped) return
    socket = await factory(credsDir, deps.log)

    socket.ev.on('connection.update', (update) => {
      if (update.connection === 'open') {
        deps.log('whatsapp adapter connected')
        return
      }
      if (update.connection !== 'close') return

      const statusCode = disconnectStatusCode(socket, update.lastDisconnect)
      if (statusCode === DisconnectReason.loggedOut) {
        deps.log(
          'whatsapp: RE-PAIR NEEDED — the session was logged out (phone removed the ' +
            'linked device, app reinstalled, or number re-registered). Scan the fresh QR ' +
            '(WhatsApp → Settings → Linked devices → Link a device).',
        )
        return // socket is dead; the operator re-pairs via the next start's QR
      }
      deps.log(
        `whatsapp: connection closed (status ${statusCode ?? 'unknown'}), reconnecting in ${reconnectDelayMs / 1000}s`,
      )
      reconnectTimer = setTimeout(() => {
        void connect().catch((error) =>
          deps.log(`whatsapp: reconnect failed: ${String(error)}`),
        )
      }, reconnectDelayMs)
      reconnectTimer.unref?.()
    })

    socket.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return
      for (const message of messages) {
        const jid = message.key.remoteJid ?? ''
        if (message.key.fromMe || !jid.endsWith('@s.whatsapp.net')) continue
        const text =
          message.message?.conversation ??
          message.message?.extendedTextMessage?.text ??
          undefined

        const chatCtx: ChatContext = {
          chatId: jid,
          messageId: message.key.id ?? '',
          text,
          reply: (replyText) => socket!.sendMessage(jid, { text: replyText }),
          sendTyping: async () => {
            await socket!.sendPresenceUpdate('composing', jid)
          },
        }
        if (text !== undefined) {
          void handlers.handleText(chatCtx).catch((error) =>
            deps.log(`whatsapp: handler error: ${String(error)}`),
          )
        } else {
          void handlers.handleNonText(chatCtx).catch((error) =>
            deps.log(`whatsapp: handler error: ${String(error)}`),
          )
        }
      }
    })
  }

  void connect().catch((error) => deps.log(`whatsapp: connect failed: ${String(error)}`))

  return {
    stop: async () => {
      stopped = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.end(undefined)
    },
  }
}

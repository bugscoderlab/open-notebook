import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  type WASocket,
} from '@whiskeysockets/baileys'
import { readFile, rm, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import qrcode from 'qrcode-terminal'

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
/** Logged-out sessions need a fresh QR; re-pair attempts get a slower cadence. */
const LOGOUT_RECONNECT_DELAY_MS = 30_000
/** How often the gateway polls the API for a one-click re-pair request. */
const RESET_POLL_INTERVAL_MS = 5_000

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
    markOnlineOnConnect: false,
  })
  socket.ev.on('creds.update', saveCreds)
  log('whatsapp: if pairing for the first time, scan the QR printed below')
  return socket
}

function disconnectStatusCode(lastDisconnect: unknown): number | undefined {
  const error = (lastDisconnect as { error?: { output?: { statusCode?: number } } } | null)
    ?.error
  return error?.output?.statusCode
}

/**
 * WhatsApp adapter on Baileys (pinned 6.7.x). ToS-gray on purpose: replies
 * only, never a verified business account, low volume (see the user guide's
 * risk warning). Session state persists under the data folder; any close
 * except a real logout reconnects; a logout logs a loud re-pair alert, wipes
 * the dead session, and the next socket prints a fresh QR (rendered in the
 * terminal as a dev fallback and pushed to the API for the web UI).
 */
export function createWhatsAppAdapter(deps: {
  client: Pick<
    ApiClient,
    'message' | 'claim' | 'pushWhatsappPairing' | 'getWhatsappResetNonce'
  >
  credsDir?: string
  log: (msg: string) => void
  createSocket?: SocketFactory
  timing?: HandlerTiming
  reconnectDelayMs?: number
  /** Separate (slower) cadence for logged-out re-pair attempts. */
  logoutReconnectDelayMs?: number
  /** Wipe the dead session before a re-pair socket is created. */
  clearCreds?: () => Promise<void>
  renderQr?: (qr: string) => void
  /** One-click re-pair polling cadence (web UI → fresh QR). */
  resetPollIntervalMs?: number
  /** Where the handled reset nonce is remembered (defaults to a file). */
  resetNonceStore?: {
    read: () => Promise<string | null>
    write: (nonce: string) => Promise<void>
  }
}): WhatsAppAdapter {
  const credsDir = deps.credsDir ?? DEFAULT_CREDS_DIR
  const factory = deps.createSocket ?? defaultSocketFactory
  const reconnectDelayMs = deps.reconnectDelayMs ?? RECONNECT_DELAY_MS
  const logoutReconnectDelayMs = deps.logoutReconnectDelayMs ?? LOGOUT_RECONNECT_DELAY_MS
  const clearCreds =
    deps.clearCreds ?? (async () => rm(credsDir, { recursive: true, force: true }))
  const renderQr = deps.renderQr ?? ((qr: string) => qrcode.generate(qr, { small: true }))

  /** Report pairing state to the API (for the web UI); never throws. */
  const pushPairing = (
    status: 'pairing' | 'connected' | 'disconnected' | 'logged_out',
    qr?: string,
    identity?: string,
  ) => {
    const push =
      identity !== undefined
        ? deps.client.pushWhatsappPairing(status, qr, identity)
        : qr !== undefined
          ? deps.client.pushWhatsappPairing(status, qr)
          : deps.client.pushWhatsappPairing(status)
    void push.catch((error) =>
      deps.log(`whatsapp: pairing status push failed: ${String(error)}`),
    )
  }
  const handlers = createChatHandlers(deps.client, PLATFORM, {
    typingIntervalMs: WHATSAPP_TYPING_INTERVAL_MS,
    ...deps.timing,
  })

  let stopped = false
  let socket: WASocket | null = null
  let reconnectTimer: NodeJS.Timeout | null = null
  /** Set on logout: the dead session must be wiped before the next socket. */
  let credsPoisoned = false

  // One-click re-pair: the gateway polls the API for the admin's reset nonce
  // and, on change, wipes the session so the next connect mints a fresh QR.
  // The handled nonce is remembered (file by default) so gateway restarts
  // don't re-trigger an old request — but a reset issued while the gateway
  // was down is still honored, because the API holds the nonce until bumped.
  const resetNonceFile = path.resolve(`${credsDir}-reset-nonce`)
  const resetNonceStore = deps.resetNonceStore ?? {
    read: async () => {
      try {
        return (await readFile(resetNonceFile, 'utf8')).trim() || null
      } catch {
        return null
      }
    },
    write: async (nonce: string) => writeFile(resetNonceFile, nonce),
  }
  let lastResetNonce: string | null = null

  /** Tear the session down so the next connect starts clean and mints a QR. */
  async function wipeSessionForRePair(reason: string): Promise<void> {
    deps.log(reason)
    // Detach first: the dying socket's late creds.update writes would
    // otherwise resurrect creds.json right after we delete it.
    socket?.ev.removeAllListeners('creds.update')
    await clearCreds().catch((error) =>
      deps.log(`whatsapp: wiping session failed: ${String(error)}`),
    )
    socket?.end(undefined)
  }

  async function checkResetNonce(): Promise<void> {
    if (stopped) return
    try {
      const nonce = await deps.client.getWhatsappResetNonce()
      if (!nonce || nonce === lastResetNonce) return
      lastResetNonce = nonce
      await resetNonceStore.write(nonce).catch(() => {})
      await wipeSessionForRePair(
        'whatsapp: re-pair requested from the web UI — wiping the session for a fresh QR',
      )
    } catch (error) {
      deps.log(`whatsapp: re-pair poll failed: ${String(error)}`)
    }
  }

  function scheduleReconnect(delayMs: number): void {
    // Ref'd on purpose: between the old socket dying and the reconnect firing
    // this timer may be the only event-loop handle — unref'd, the gateway
    // process would exit before the fresh QR socket is created.
    reconnectTimer = setTimeout(() => {
      void connect().catch((error) =>
        deps.log(`whatsapp: reconnect failed: ${String(error)}`),
      )
    }, delayMs)
  }

  async function connect(): Promise<void> {
    if (stopped) return
    if (credsPoisoned) {
      credsPoisoned = false
      // Wipe the logged-out session immediately before the re-pair socket
      // reads it. Baileys only emits a pairing QR for a creds store with no
      // usable session; deleting right after the logout event would race the
      // dying socket's late creds.update writes, which resurrect creds.json.
      await clearCreds().catch((error) =>
        deps.log(`whatsapp: clearing logged-out session failed: ${String(error)}`),
      )
    }
    socket = await factory(credsDir, deps.log)

    socket.ev.on('connection.update', (update) => {
      // Baileys dropped printQRInTerminal — render the QR ourselves (terminal
      // is the dev fallback) and push it to the API for the web UI.
      if (update.qr) {
        deps.log(
          'whatsapp: scan this QR with WhatsApp → Settings → Linked devices → Link a device',
        )
        renderQr(update.qr)
        pushPairing('pairing', update.qr)
        return
      }
      if (update.connection === 'open') {
        deps.log('whatsapp adapter connected')
        // Our own JID lets the web UI offer one-click self-linking (claim-self).
        // Strip the device suffix ('…:2@…' → '…@…'): a re-pair creates a new
        // device id, and the link must survive that — /start-bound links use
        // device-less sender jids, so this keeps both flows consistent.
        const ownId = socket!.user?.id
        pushPairing('connected', undefined, ownId?.replace(/:\d+(?=@)/, ''))
        return
      }
      if (update.connection !== 'close') return

      const statusCode = disconnectStatusCode(update.lastDisconnect)
      if (statusCode === DisconnectReason.loggedOut) {
        deps.log(
          'whatsapp: RE-PAIR NEEDED — the session was logged out (phone removed the ' +
            'linked device, app reinstalled, or number re-registered). A fresh QR ' +
            'follows — scan it with WhatsApp → Settings → Linked devices → Link a device.',
        )
        pushPairing('logged_out')
        // The socket is dead, but its teardown can still emit late
        // creds.update events whose saveCreds writes would resurrect the
        // logged-out creds.json right after we wipe it — so detach the
        // listener first. A new socket is what generates the re-pair QR, and
        // without the timer the event loop empties and the gateway exits
        // (local runs have no supervisord).
        socket!.ev.removeAllListeners('creds.update')
        credsPoisoned = true
        scheduleReconnect(logoutReconnectDelayMs)
        return
      }
      deps.log(
        `whatsapp: connection closed (status ${statusCode ?? 'unknown'}), reconnecting in ${reconnectDelayMs / 1000}s`,
      )
      pushPairing('disconnected')
      scheduleReconnect(reconnectDelayMs)
    })

    socket.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return
      for (const message of messages) {
        const jid = message.key.remoteJid ?? ''
        const text =
          message.message?.conversation ??
          message.message?.extendedTextMessage?.text ??
          undefined
        // Direct-jid senders may arrive as @s.whatsapp.net or (sender-privacy)
        // as @lid — both are real user chats; groups/broadcasts stay excluded.
        if (!jid.endsWith('@s.whatsapp.net') && !jid.endsWith('@lid')) continue
        // Own messages are echoes of the bot's replies — ignore them, except
        // a self-addressed /start (WhatsApp's "Message yourself" chat): when
        // the bot runs on the user's own number, that chat is the only way
        // to link the account.
        if (message.key.fromMe && !(text ?? '').startsWith('/start')) continue

        const chatCtx: ChatContext = {
          chatId: jid,
          messageId: message.key.id ?? '',
          text,
          reply: (replyText, extra) =>
            socket!.sendMessage(
              jid,
              { text: replyText },
              // Baileys quotes with the full source message, not an id.
              extra?.quote ? { quoted: message } : undefined,
            ),
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

  void resetNonceStore
    .read()
    .then((nonce) => {
      lastResetNonce = nonce
    })
    .catch(() => {})
  const resetPollTimer = setInterval(
    () => void checkResetNonce(),
    deps.resetPollIntervalMs ?? RESET_POLL_INTERVAL_MS,
  )
  resetPollTimer.unref?.()

  void connect().catch((error) => deps.log(`whatsapp: connect failed: ${String(error)}`))

  return {
    stop: async () => {
      stopped = true
      clearInterval(resetPollTimer)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socket?.end(undefined)
    },
  }
}

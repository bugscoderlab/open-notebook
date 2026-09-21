import { describe, expect, it, vi } from 'vitest'
import { DisconnectReason } from '@whiskeysockets/baileys'

import type { ApiClient } from '../src/api-client.js'
import { createWhatsAppAdapter, type SocketFactory } from '../src/whatsapp.js'

const TIMING = { typingIntervalMs: 60_000, interimAfterMs: 60_000, chunkPacingMs: 0, sleepFn: () => Promise.resolve() }

/** Scriptable fake of the Baileys socket surface the adapter uses. */
function makeFakeSocket() {
  const listeners: Record<string, (data: never) => void> = {}
  const socket = {
    ev: {
      on: (event: string, fn: (data: never) => void) => {
        listeners[event] = fn
      },
      removeAllListeners: (event: string) => {
        delete listeners[event]
      },
    },
    user: { id: '6012:7@s.whatsapp.net' },
    sendMessage: vi.fn().mockResolvedValue({}),
    sendPresenceUpdate: vi.fn().mockResolvedValue(undefined),
    // Mirror Baileys: ending the socket closes the connection (transient).
    end: vi.fn(() => {
      listeners['connection.update']?.({ connection: 'close' } as never)
    }),
  }
  return {
    socket: socket as never,
    emit: (event: string, data: unknown) => listeners[event]?.(data as never),
    sendMessage: socket.sendMessage,
  }
}

function textMessage(jid: string, text: string, fromMe = false) {
  return {
    messages: [
      {
        key: { remoteJid: jid, id: 'msg-1', fromMe },
        message: { conversation: text },
      },
    ],
    type: 'notify',
  }
}

function mockClient() {
  return {
    message: vi.fn().mockResolvedValue({ reply: 'wa answer', suggestions: [], conversation_reset: false }),
    claim: vi.fn().mockResolvedValue({ email: 'a@b.c' }),
    pushWhatsappPairing: vi.fn().mockResolvedValue(undefined),
    getWhatsappResetNonce: vi.fn().mockResolvedValue(null),
  } as unknown as Pick<
    ApiClient,
    'message' | 'claim' | 'pushWhatsappPairing' | 'getWhatsappResetNonce'
  >
}

function flush(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve))
}

describe('whatsapp adapter', () => {
  it('replies to an inbound direct text message via the API', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const logs: string[] = []
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: (m) => logs.push(m), createSocket: factory, timing: TIMING })
    await flush()

    fake.emit('messages.upsert', textMessage('15551234567@s.whatsapp.net', 'hello bot'))
    await flush()

    expect(client.message).toHaveBeenCalledWith('whatsapp', '15551234567@s.whatsapp.net', 'hello bot')
    await vi.waitFor(() => expect(fake.sendMessage).toHaveBeenCalled())
    const [jid, payload, options] = fake.sendMessage.mock.calls[0] as unknown as [
      string,
      { text: string },
      { quoted: unknown } | undefined,
    ]
    expect(jid).toBe('15551234567@s.whatsapp.net')
    expect(payload.text).toBe('wa answer')
    // First chunk quotes the user's message (Baileys needs the full message).
    expect(options?.quoted).toBeTruthy()
  })

  it('intercepts /start CODE as the claim flow', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: () => {}, createSocket: factory, timing: TIMING })
    await flush()

    fake.emit('messages.upsert', textMessage('15551234567@s.whatsapp.net', '/start 483920'))
    await flush()

    expect(client.claim).toHaveBeenCalledWith('whatsapp', '15551234567@s.whatsapp.net', '483920')
    await vi.waitFor(() => expect(fake.sendMessage).toHaveBeenCalled())
    expect((fake.sendMessage.mock.calls[0][1] as { text: string }).text).toContain('a@b.c')
  })

  it('wipes the session when the web UI requests a re-pair', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const sockets: unknown[] = []
    let cleared = 0
    const store: { value: string | null } = { value: null }
    const factory: SocketFactory = async () => {
      sockets.push(fake.socket)
      return fake.socket
    }
    client.getWhatsappResetNonce.mockResolvedValue('nonce-1')
    const adapter = createWhatsAppAdapter({
      client,
      log: () => {},
      createSocket: factory,
      timing: TIMING,
      reconnectDelayMs: 10,
      resetPollIntervalMs: 20,
      resetNonceStore: {
        read: async () => store.value,
        write: async (nonce: string) => {
          store.value = nonce
        },
      },
      clearCreds: async () => {
        cleared += 1
      },
    })
    await flush()

    await vi.waitFor(() => expect(cleared).toBe(1))
    expect(store.value).toBe('nonce-1')
    // The torn-down socket ends → transient close → a fresh socket follows.
    await vi.waitFor(() => expect(sockets.length).toBe(2))

    await adapter.stop()
  })

  it('ignores an already-handled re-pair nonce', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    let cleared = 0
    const store: { value: string | null } = { value: 'nonce-old' }
    const factory: SocketFactory = async () => fake.socket
    client.getWhatsappResetNonce.mockResolvedValue('nonce-old')
    const adapter = createWhatsAppAdapter({
      client,
      log: () => {},
      createSocket: factory,
      timing: TIMING,
      resetPollIntervalMs: 20,
      resetNonceStore: {
        read: async () => store.value,
        write: async (nonce: string) => {
          store.value = nonce
        },
      },
      clearCreds: async () => {
        cleared += 1
      },
    })
    await flush()
    await new Promise((resolve) => setTimeout(resolve, 80))
    expect(cleared).toBe(0)

    await adapter.stop()
  })

  it('accepts a self-addressed /start (Message yourself chat, sole-number setup)', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: () => {}, createSocket: factory, timing: TIMING })
    await flush()

    // fromMe=true — WhatsApp echoes own messages on linked devices; only
    // /start is honored so the bot never answers its own replies.
    fake.emit('messages.upsert', textMessage('15551234567@s.whatsapp.net', '/start 483920', true))
    await flush()

    expect(client.claim).toHaveBeenCalledWith('whatsapp', '15551234567@s.whatsapp.net', '483920')
  })

  it('ignores its own messages, groups and broadcasts', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: () => {}, createSocket: factory, timing: TIMING })
    await flush()

    fake.emit('messages.upsert', textMessage('15551234567@s.whatsapp.net', 'mine', true))
    fake.emit('messages.upsert', textMessage('12345@g.us', 'group chat'))
    fake.emit('messages.upsert', { ...textMessage('15551234567@s.whatsapp.net', 'hist'), type: 'append' })
    await flush()

    expect(client.message).not.toHaveBeenCalled()
    expect(fake.sendMessage).not.toHaveBeenCalled()
  })

  it('accepts senders addressed by @lid (sender-privacy addressing)', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: () => {}, createSocket: factory, timing: TIMING })
    await flush()

    fake.emit('messages.upsert', textMessage('78177682587656@lid', 'hello bot'))
    await flush()

    expect(client.message).toHaveBeenCalledWith('whatsapp', '78177682587656@lid', 'hello bot')
  })

  it('non-text messages get the text-only note', async () => {    const fake = makeFakeSocket()
    const client = mockClient()
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({ client, log: () => {}, createSocket: factory, timing: TIMING })
    await flush()

    fake.emit('messages.upsert', {
      messages: [{ key: { remoteJid: '15551234567@s.whatsapp.net', id: 'm', fromMe: false }, message: { imageMessage: {} } }],
      type: 'notify',
    })
    await flush()

    await vi.waitFor(() => expect(fake.sendMessage).toHaveBeenCalled())
    expect((fake.sendMessage.mock.calls[0][1] as { text: string }).text).toContain('text')
    expect(client.message).not.toHaveBeenCalled()
  })

  it('renders a QR code when pairing is required', async () => {
    const fake = makeFakeSocket()
    const client = mockClient()
    const logs: string[] = []
    const rendered: string[] = []
    const factory: SocketFactory = async () => fake.socket
    createWhatsAppAdapter({
      client,
      log: (m) => logs.push(m),
      createSocket: factory,
      timing: TIMING,
      renderQr: (qr) => rendered.push(qr),
    })
    await flush()

    fake.emit('connection.update', { qr: 'qr-payload' })
    await flush()

    expect(rendered).toEqual(['qr-payload'])
    expect(logs.some((line) => line.includes('scan this QR'))).toBe(true)
    expect(client.pushWhatsappPairing).toHaveBeenCalledWith('pairing', 'qr-payload')
  })

  it('reconnects on transient closes and re-pairs on logout', async () => {
    const sockets: ReturnType<typeof makeFakeSocket>[] = []
    const logs: string[] = []
    const client = mockClient()
    let cleared = 0
    const factory: SocketFactory = async () => {
      const fake = makeFakeSocket()
      sockets.push(fake)
      return fake.socket
    }
    const adapter = createWhatsAppAdapter({ client, log: (m) => logs.push(m), createSocket: factory, timing: TIMING, reconnectDelayMs: 10, logoutReconnectDelayMs: 10, clearCreds: async () => { cleared += 1 } })
    await flush()
    expect(sockets.length).toBe(1)

    // Open → connected push to the API (web UI state), including our own
    // JID (device suffix stripped) so the UI can offer one-click self-linking.
    sockets[0].emit('connection.update', { connection: 'open' })
    await flush()
    expect(client.pushWhatsappPairing).toHaveBeenCalledWith('connected', undefined, '6012@s.whatsapp.net')

    // Transient close → 'disconnected' push + schedules a reconnect.
    sockets[0].emit('connection.update', {
      connection: 'close',
      lastDisconnect: { error: { output: { statusCode: 428 } } },
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(sockets.length).toBe(2)
    expect(client.pushWhatsappPairing).toHaveBeenCalledWith('disconnected')

    // Logout (401) → loud re-pair alert + 'logged_out' push, the dead creds
    // are wiped, and a re-pair socket follows — the fresh QR comes from the
    // new socket; without it the event loop empties and the process exits.
    sockets[1].emit('connection.update', {
      connection: 'close',
      lastDisconnect: { error: { output: { statusCode: DisconnectReason.loggedOut } } },
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(cleared).toBe(1)
    expect(sockets.length).toBe(3)
    expect(logs.some((line) => line.includes('RE-PAIR NEEDED'))).toBe(true)
    expect(client.pushWhatsappPairing).toHaveBeenCalledWith('logged_out')

    await adapter.stop()
  })
})

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
    },
    sendMessage: vi.fn().mockResolvedValue({}),
    sendPresenceUpdate: vi.fn().mockResolvedValue(undefined),
    end: vi.fn(),
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
  } as unknown as Pick<ApiClient, 'message' | 'claim'>
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
    const [jid, payload] = fake.sendMessage.mock.calls[0] as unknown as [string, { text: string }]
    expect(jid).toBe('15551234567@s.whatsapp.net')
    expect(payload.text).toBe('wa answer')
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

  it('non-text messages get the text-only note', async () => {
    const fake = makeFakeSocket()
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

  it('reconnects on transient closes but not on logout (408)', async () => {
    const sockets: ReturnType<typeof makeFakeSocket>[] = []
    const logs: string[] = []
    const factory: SocketFactory = async () => {
      const fake = makeFakeSocket()
      sockets.push(fake)
      return fake.socket
    }
    const adapter = createWhatsAppAdapter({ client: mockClient(), log: (m) => logs.push(m), createSocket: factory, timing: TIMING, reconnectDelayMs: 10 })
    await flush()
    expect(sockets.length).toBe(1)

    // Transient close → schedules a reconnect with a short delay.
    sockets[0].emit('connection.update', {
      connection: 'close',
      lastDisconnect: { error: { output: { statusCode: 428 } } },
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(sockets.length).toBe(2)

    // Logout (401) → loud re-pair alert, no reconnect socket. Note 408 is
    // connectionLost, which MUST reconnect — only 401 means re-pair.
    const before = sockets.length
    sockets[1].emit('connection.update', {
      connection: 'close',
      lastDisconnect: { error: { output: { statusCode: DisconnectReason.loggedOut } } },
    })
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(sockets.length).toBe(before)
    expect(logs.some((line) => line.includes('RE-PAIR NEEDED'))).toBe(true)

    await adapter.stop()
  })
})

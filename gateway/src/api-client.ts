export interface MessageResult {
  reply: string
  suggestions: string[]
  conversation_reset: boolean
}

export type WhatsappPairingStatus =
  | 'pairing'
  | 'connected'
  | 'disconnected'
  | 'logged_out'

/** One typed event from the streaming message endpoint. */
export interface StreamTurnEvent {
  type: 'answer_delta' | 'final_answer' | 'suggestions' | 'complete' | 'error'
  content?: string
  suggestions?: string[]
  message?: string
}

export class ApiClientError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

interface ErrorBody {
  detail?: string
}

/**
 * Minimal client for the API's internal-token surface (ADR-018). The gateway
 * is a dumb pipe: this client carries `Authorization: Internal <token>` and
 * surfaces the API's chat-friendly `detail` strings for rendering in chat.
 */
export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly token: string,
    private readonly fetchImpl: typeof fetch = fetch,
    private readonly timeoutMs = 600_000,
  ) {}

  private async call(path: string, init: RequestInit = {}): Promise<Response> {
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.timeoutMs)
    try {
      return await this.fetchImpl(`${this.baseUrl}/api${path}`, {
        ...init,
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Internal ${this.token}`,
          ...(init.headers || {}),
        },
        signal: controller.signal,
      })
    } finally {
      clearTimeout(timer)
    }
  }

  private static async detailOf(response: Response): Promise<string> {
    try {
      const body = (await response.json()) as ErrorBody
      if (typeof body.detail === 'string' && body.detail) return body.detail
    } catch {
      // non-JSON error body
    }
    return `Request failed with status ${response.status}`
  }

  /** Verify the internal token against the integrations status endpoint. */
  async checkStatus(): Promise<void> {
    const response = await this.call('/integrations/status', { method: 'GET' })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
  }

  /** Claim a linking code (Telegram /start <code> flow). */
  async claim(
    platform: string,
    externalId: string,
    code: string,
  ): Promise<{ email: string }> {
    const response = await this.call('/integrations/claim', {
      method: 'POST',
      body: JSON.stringify({ platform, external_id: externalId, code }),
    })
    const body = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        typeof (body as ErrorBody).detail === 'string'
          ? ((body as ErrorBody).detail as string)
          : `Claim failed with status ${response.status}`,
      )
    }
    return body as { email: string }
  }

  /** Report WhatsApp pairing state (QR rotation, connect, disconnect). */
  async pushWhatsappPairing(
    status: WhatsappPairingStatus,
    qr?: string,
    identity?: string,
  ): Promise<void> {
    const body: Record<string, string> = { status }
    if (qr !== undefined) body.qr = qr
    if (identity !== undefined) body.identity = identity
    const response = await this.call('/integrations/whatsapp/pairing', {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
  }

  /** Poll for the admin's one-click re-pair request (null = none). */
  async getWhatsappResetNonce(): Promise<string | null> {
    const response = await this.call('/integrations/whatsapp/reset', {
      method: 'GET',
    })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
    const body = (await response.json().catch(() => ({}))) as {
      nonce?: string | null
    }
    return body.nonce ?? null
  }

  /**
   * Stream one conversational ask over SSE, yielding typed turn events
   * (answer_delta → final_answer → suggestions → complete, or error).
   * Throws ApiClientError on non-OK responses — callers fall back to the
   * non-streaming message() path.
   */
  async *messageStream(
    platform: string,
    externalId: string,
    text: string,
  ): AsyncGenerator<StreamTurnEvent> {
    const response = await this.call('/integrations/message/stream', {
      method: 'POST',
      body: JSON.stringify({ platform, external_id: externalId, text }),
    })
    if (!response.ok || !response.body) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (!payload) continue
          let event: StreamTurnEvent
          try {
            event = JSON.parse(payload) as StreamTurnEvent
          } catch {
            // Incomplete JSON stays skipped — don't kill the stream.
            continue
          }
          yield event
          if (event.type === 'complete' || event.type === 'error') {
            return
          }
        }
      }
      // EOF before complete/error: surface as a thrown error so the caller
      // can fall back (or apologize) — never silently act successful (#57).
      throw new ApiClientError(0, 'Stream ended before completion')
    } finally {
      reader.releaseLock()
    }
  }

  /** Route one inbound message; returns the reply to send back. */
  async message(
    platform: string,
    externalId: string,
    text: string,
  ): Promise<MessageResult> {
    const response = await this.call('/integrations/message', {
      method: 'POST',
      body: JSON.stringify({ platform, external_id: externalId, text }),
    })
    if (!response.ok) {
      throw new ApiClientError(
        response.status,
        await ApiClient.detailOf(response),
      )
    }
    return (await response.json()) as MessageResult
  }
}

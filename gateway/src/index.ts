import { ApiClient, ApiClientError } from './api-client.js'
import { loadConfig } from './config.js'
import { startTelegramAdapter } from './telegram.js'

const log = (msg: string): void => console.log(`[gateway] ${msg}`)
const fail = (msg: string): never => {
  console.error(`[gateway] ERROR: ${msg}`)
  process.exit(1)
}

async function main(): Promise<void> {
  const config = loadConfig()

  const client = new ApiClient(config.apiUrl, config.internalToken)
  try {
    await client.checkStatus()
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      fail(
        `internal token rejected by the API at ${config.apiUrl} — check ` +
          'OPEN_NOTEBOOK_INTERNAL_TOKEN / data/internal-token (rotation: delete ' +
          'the file or set a new env value, restart both services).',
      )
    }
    fail(
      `API at ${config.apiUrl} is unreachable or unhealthy: ${(error as Error).message}. ` +
        'The API must be running before the gateway starts.',
    )
  }
  log(`internal token accepted by ${config.apiUrl}`)

  const adapters: Array<{ stop: () => Promise<void> }> = []

  if (config.telegramToken) {
    adapters.push(startTelegramAdapter(config.telegramToken, client, log))
  } else {
    log('telegram adapter not configured (set OPEN_NOTEBOOK_TELEGRAM_BOT_TOKEN to enable)')
  }

  if (config.whatsappEnabled) {
    const { createWhatsAppAdapter } = await import('./whatsapp.js')
    adapters.push(createWhatsAppAdapter({ client, log }))
    log('whatsapp adapter starting (Baileys — replies only, see the user guide risk warning)')
  } else {
    log('whatsapp adapter not configured (set OPEN_NOTEBOOK_WHATSAPP_ENABLED=true to enable)')
  }

  if (adapters.length === 0) {
    log('idling — no adapters configured; the API is unaffected by this process')
  }

  const shutdown = async (signal: string): Promise<void> => {
    log(`received ${signal}, stopping adapters`)
    await Promise.allSettled(adapters.map((adapter) => adapter.stop()))
    process.exit(0)
  }
  process.on('SIGTERM', () => void shutdown('SIGTERM'))
  process.on('SIGINT', () => void shutdown('SIGINT'))
}

main().catch((error) => fail((error as Error).message))

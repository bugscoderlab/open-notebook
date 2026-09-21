import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export class ConfigError extends Error {}

export interface GatewayConfig {
  /** Internal token (ADR-018): env, else read from the data folder. */
  internalToken: string
  /** Base URL of the FastAPI backend (no trailing slash). */
  apiUrl: string
  /** Telegram bot token; null = adapter inactive. */
  telegramToken: string | null
  /** WhatsApp adapter toggle; on by default — set OPEN_NOTEBOOK_WHATSAPP_ENABLED=false to disable. */
  whatsappEnabled: boolean
}

/** Repo-root data folder, from either src/ (tsx dev) or dist/ (node). */
const DEFAULT_TOKEN_FILE = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
  'data',
  'internal-token',
)

function resolveInternalToken(env: NodeJS.ProcessEnv): string {
  const fromEnv = env.OPEN_NOTEBOOK_INTERNAL_TOKEN?.trim()
  if (fromEnv) return fromEnv

  const tokenFile = env.OPEN_NOTEBOOK_TOKEN_FILE?.trim() || DEFAULT_TOKEN_FILE
  try {
    const fromFile = readFileSync(tokenFile, 'utf8').trim()
    if (fromFile) return fromFile
  } catch {
    // fall through to the actionable error below
  }
  throw new ConfigError(
    `No internal token found. Set OPEN_NOTEBOOK_INTERNAL_TOKEN, or ensure the ` +
      `API has generated one at ${tokenFile} (it does this on first ` +
      `internal-auth call when the env var is unset).`,
  )
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): GatewayConfig {
  return {
    internalToken: resolveInternalToken(env),
    apiUrl: (env.INTERNAL_API_URL || 'http://127.0.0.1:5055').replace(/\/$/, ''),
    telegramToken: env.OPEN_NOTEBOOK_TELEGRAM_BOT_TOKEN?.trim() || null,
    whatsappEnabled: env.OPEN_NOTEBOOK_WHATSAPP_ENABLED !== 'false',
  }
}

// Numeric-only public contract. No transcript, prompts, paths, or credentials.
export const SCHEMA_VERSION = 1
export const CACHE_TTL_MS = 3_600_000
export const POLL_MS = 15_000
export const SESSION_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/

export function validSessionId(value) {
  return typeof value === 'string' && SESSION_ID.test(value)
}

function number(value, max = Number.MAX_SAFE_INTEGER) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max ? value : null
}

export function restoredRequest(saved, sessionId, now) {
  if (saved?.schemaVersion !== SCHEMA_VERSION || saved.sessionId !== sessionId) return null
  const value = number(saved.cache?.lastRequestAt)
  return value !== null && value <= now ? value : null
}

export function cacheEstimate(lastRequestAt, now) {
  const stamp = number(lastRequestAt)
  const last = stamp !== null && stamp <= now ? stamp : null
  const expiresAt = last === null ? null : last + CACHE_TTL_MS
  const remainingMs = expiresAt === null ? null : Math.max(0, expiresAt - now)
  return {
    estimated: true,
    basis: 'successful-main-turn-step-start',
    ttlMs: CACHE_TTL_MS,
    lastRequestAt: last,
    expiresAt,
    remainingMs,
    state: last === null ? 'unknown' : remainingMs > 0 ? 'warm' : 'cold',
  }
}

export function snapshot(sessionId, now, usage, lastRequestAt, active = true) {
  if (!validSessionId(sessionId)) throw new Error('Invalid Claude session identity')
  return {
    schemaVersion: SCHEMA_VERSION,
    source: 'claude-code-mod',
    sessionId,
    sampledAt: now,
    usageObservedAt: usage ? now : null,
    usageAvailable: Boolean(usage),
    sessionStartedAt: number(usage?.startedAt),
    active,
    context: {
      tokens: number(usage?.context?.tokens),
      window: number(usage?.context?.window),
      percent: number(usage?.context?.percent, 100),
    },
    cache: cacheEstimate(lastRequestAt, now),
  }
}

export function bandText(record, now) {
  const percent = record?.context?.percent
  const context = percent === null || percent === undefined ? 'Context unknown' : `Context ${percent}%`
  const cache = cacheEstimate(record?.cache?.lastRequestAt, now)
  if (cache.state === 'unknown') return `${context} · Cache estimate unknown (1h TTL)`
  if (cache.state === 'cold') return `${context} · Cache estimate cold (1h TTL)`
  const seconds = Math.ceil(cache.remainingMs / 1000)
  const minutes = Math.floor(seconds / 60)
  return `${context} · Cache estimate ${minutes}:${String(seconds % 60).padStart(2, '0')} (1h TTL)`
}

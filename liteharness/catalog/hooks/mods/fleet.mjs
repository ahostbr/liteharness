// Pure data/brief rules only. All Mods API calls belong in register.mjs.
export const FLEET_PANE = 'liteharness-fleet'
export const FLEET_LIMIT = 100
const TIERS = new Set(['orchestrator', 'leader', 'worker', 'thinker', 'reviewer'])
const clean = value => String(value ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').slice(0, 512)

export function fleetSnapshot(text) {
  const row = JSON.parse(text)
  if (row?.schemaVersion !== 1 || !Number.isSafeInteger(row.sampledAt) || row.sampledAt < 0 || row.sampledAt > 8_640_000_000_000_000 ||
      !Array.isArray(row.agents) || !Array.isArray(row.tasks) || !Array.isArray(row.warnings) ||
      !Number.isSafeInteger(row.agentCount) || row.agentCount < row.agents.length ||
      !Number.isSafeInteger(row.taskCount) || row.taskCount < row.tasks.length) {
    throw new Error('Invalid fleet snapshot')
  }
  return { sampledAt: row.sampledAt, agentCount: row.agentCount, taskCount: row.taskCount,
    agents: row.agents.slice(0, FLEET_LIMIT).map(a => ({
      agent_id: clean(a.agent_id), name: clean(a.name), tier: clean(a.tier), model: clean(a.model), cwd: clean(a.cwd),
    })),
    tasks: row.tasks.slice(0, FLEET_LIMIT).map(t => ({
      id: clean(t.id), title: clean(t.title), status: clean(t.status), assignee: clean(t.assignee), tier: clean(t.tier),
    })), warnings: row.warnings.slice(0, 10).map(clean) }
}

export const SPAWN_MODELS = new Set(['claude-opus-5-5[1m]', 'claude-opus-5[1m]', 'claude-opus-4-8[1m]', 'claude-opus-4-6[1m]'])

export function normalizedCwd(value) {
  if (typeof value !== 'string' || !value || /[\x00-\x1f]/.test(value)) return null
  const windows = /^[A-Za-z]:[\\/]/.test(value) || /^\\\\[^\\]+\\[^\\]+/.test(value)
  if (!windows && !/^\/(?!\/)/.test(value)) return null
  const path = windows ? value.replace(/\\/g, '/').toLowerCase() : value
  if (path.split('/').some(part => part === '.' || part === '..')) return null
  return path.replace(/\/+$/, '') || '/'
}

export function spawnDenial(e, ownedCwd) {
  // The host has no tier property. An explicit canonical brief line is required.
  const tiers = typeof e.prompt === 'string' ? [...e.prompt.matchAll(/^Agent-Tier: ([a-z]+)\r?$/gm)] : []
  if (tiers.length !== 1 || !TIERS.has(tiers[0][1]) || (e.prompt.match(/Agent-Tier:/g) || []).length !== 1) return 'Spawn requires exactly one Agent-Tier: orchestrator|leader|worker|thinker|reviewer brief line.'
  if (!SPAWN_MODELS.has(e.model)) {
    return 'Spawn requires an explicit full Claude model id ending in [1m]; aliases and inherit are refused.'
  }
  if (e.fork && e.parentModel !== e.model) return 'Fork ignores the requested model; its parent must match the explicit [1m] model id.'
  const cwd = normalizedCwd(e.cwd)
  if (!cwd) return 'Spawn requires an explicit absolute cwd without dot segments.'
  if (!normalizedCwd(ownedCwd) || cwd !== normalizedCwd(ownedCwd)) return 'Spawn cwd must match the current owned session scope.'
  return null
}

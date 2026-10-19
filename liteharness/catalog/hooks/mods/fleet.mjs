// Pure data/brief rules only. All Mods API calls belong in register.mjs.
export const FLEET_PANE = 'liteharness-fleet'
export const FLEET_LIMIT = 100
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

export function normalizedCwd(value) {
  if (typeof value !== 'string' || !value || /[\x00-\x1f]/.test(value)) return null
  const windows = /^[A-Za-z]:[\\/]/.test(value) || /^\\\\[^\\]+\\[^\\]+/.test(value)
  if (!windows && !/^\/(?!\/)/.test(value)) return null
  const path = windows ? value.replace(/\\/g, '/').toLowerCase() : value
  if (path.split('/').some(part => part === '.' || part === '..')) return null
  return path.replace(/\/+$/, '') || '/'
}

// The host's Agent tool passes a short alias or a full id, with or without [1m], or no model at all
// (CLAUDE_CODE_SUBAGENT_MODEL then picks Sonnet). Anything else is not a Claude model and is refused.
const SPAWN_FAMILIES = ['sonnet', 'haiku', 'opus']
export const SPAWN_MODELS = new Set(SPAWN_FAMILIES.flatMap(family => [family, 'claude-' + family + '-5-5', 'claude-' + family + '-5-5[1m]']))

// Returns the refusal text, or null when the Agent() call may run.
export function spawnDenial(e, ownedCwd) {
  // A fork inherits its parent's model, so the parent's model is checked too.
  for (const model of [e?.model, e?.fork ? e.parentModel : undefined]) {
    if (model !== undefined && model !== null && !SPAWN_MODELS.has(model)) {
      return 'Agent() model must be sonnet, haiku or opus (short name or full id, with or without [1m]) or omitted; refused ' +
        JSON.stringify(clean(model)) + '.'
    }
  }
  // The host's Agent tool has no cwd input: a call without one runs in the session's own directory,
  // which is the only directory this check allows. Never echo the requested cwd.
  const owned = normalizedCwd(ownedCwd)
  const cwd = normalizedCwd(e?.cwd || ownedCwd)
  if (!owned || !cwd || cwd !== owned) return "Agent() cwd must be the session's own directory; a foreign or unreadable cwd is refused."
  return null
}

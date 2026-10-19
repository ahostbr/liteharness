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

// Returns the refusal text, or null when the Agent() call may run. Any model runs: the host's Agent tool
// passes only its own enum (or nothing), and which model a subagent uses is never this guard's call.
export function spawnDenial(e, ownedCwd) {
  // The host's Agent tool sets no cwd for an ordinary call, which then runs in the session's own directory.
  // isolation:"worktree" runs under the session's repo, so a cwd equal to the session's directory or inside it
  // passes; a sibling that merely shares the prefix (C:/repo-evil beside C:/repo) does not. Never echo the requested cwd.
  const owned = normalizedCwd(ownedCwd)
  const cwd = normalizedCwd(e?.cwd || ownedCwd)
  const prefix = owned && (owned.endsWith('/') ? owned : owned + '/')
  if (!owned || !cwd || (cwd !== owned && !cwd.startsWith(prefix))) {
    return "Agent() cwd must be the session's own directory or inside it; a foreign or unreadable cwd is refused."
  }
  return null
}

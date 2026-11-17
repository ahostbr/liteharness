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

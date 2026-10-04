// Pure policy protocol. API-bearing code stays in hooks/register.mjs.
export const GUARD_PROTOCOL = 1
export const FILE_WRITES = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit'])
const RESERVED = new Set(['tool', 'tool_use_id', 'agentId', 'consent'])

export function guardPayload(event, cwd, sessionId, kind = 'call') {
  if (!event || typeof event !== 'object' || Array.isArray(event) ||
      typeof event.tool !== 'string' || !event.tool ||
      typeof cwd !== 'string' || !cwd || typeof sessionId !== 'string' || !sessionId) {
    throw new Error('Unsupported guard event or session context')
  }
  const input = kind === 'check' ? event.input : Object.fromEntries(
    Object.entries(event).filter(([key]) => !RESERVED.has(key)))
  if (!input || typeof input !== 'object' || Array.isArray(input)) {
    throw new Error('Unsupported tool input')
  }
  return { protocol: GUARD_PROTOCOL, kind, session_id: sessionId,
    tool_name: event.tool, tool_input: input, cwd }
}

export function guardReply(processResult) {
  if (!processResult || processResult.exitCode !== 0 || typeof processResult.stdout !== 'string') {
    throw new Error('Guard backend did not complete successfully')
  }
  const reply = JSON.parse(processResult.stdout)
  if (reply?.protocol !== GUARD_PROTOCOL || typeof reply.allowOwnWrite !== 'boolean' ||
      !(reply.deny === null || typeof reply.deny === 'string' && reply.deny.length > 0) ||
      !(reply.command === null || typeof reply.command === 'string')) {
    throw new Error('Invalid or incomplete guard backend response')
  }
  if (reply.deny && (reply.allowOwnWrite || reply.command !== null)) {
    throw new Error('Contradictory guard backend response')
  }
  return reply
}

export function continuedEvent(event, reply) {
  if (reply.command === null) return event
  if (typeof event.command !== 'string') throw new Error('Cannot rewrite a non-command tool')
  return { ...event, command: reply.command }
}

export function permissionResult(decided, reply) {
  // Rule 5a never overrides a deny, a rule-backed ask, or an unknown verdict.
  if (reply.deny) return { decision: 'deny', reason: reply.deny }
  if (reply.allowOwnWrite && decided?.decision === 'ask' && !decided.rule) {
    return { decision: 'allow', reason: 'Rule 5a: verified own-worktree file write' }
  }
  return decided
}

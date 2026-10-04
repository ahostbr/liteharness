// Pure consumer of the Python extractor's .history.json; no $/IO/transcript access.
export const PASTE_PANE = 'liteharness-pastes'
export const PASTE_PAGE_SIZE = 8
export const MANIFEST_BYTES = 256 * 1024
export const IMAGE_BYTES = 5 * 1024 * 1024

export function absolutePath(value) {
  if (typeof value !== 'string' || value.length > 2500 || /[\x00-\x1f]/.test(value)) return null
  const path = value.replace(/\\/g, '/').replace(/\/$/, '')
  if (!path.startsWith('/') && !/^[A-Za-z]:\//.test(path)) return null
  if (path.split('/').some(part => part === '.' || part === '..')) return null
  return path || '/'
}

export function within(root, value) {
  const base = absolutePath(root)
  const path = absolutePath(value)
  return base !== null && path !== null && (path === base || path.startsWith(base.replace(/\/$/, '') + '/'))
}

export function historyEntries(text) {
  if (typeof text !== 'string' || text.length > MANIFEST_BYTES) throw new Error('Invalid paste manifest')
  const value = JSON.parse(text)
  if (!Array.isArray(value?.entries) || value.entries.length > 256) throw new Error('Invalid paste manifest')
  let total = 0
  const seen = new Set()
  return value.entries.map(entry => {
    if (!entry || typeof entry.file !== 'string' || !/^[a-f0-9]{64}\.(png|jpg|gif|webp)$/.test(entry.file) ||
        !Number.isSafeInteger(entry.bytes) || entry.bytes <= 0 || entry.bytes > IMAGE_BYTES || seen.has(entry.file)) {
      throw new Error('Invalid paste entry')
    }
    total += entry.bytes
    if (total > 256 * 1024 * 1024) throw new Error('Paste history exceeds storage limit')
    seen.add(entry.file)
    return { file: entry.file, bytes: entry.bytes, preview: entry.file.endsWith('.png') }
  })
}

export function imageGeometry(columns) {
  return { columns: Math.max(1, Math.min(40, Number.isFinite(columns) ? Math.floor(columns) : 24)), rows: 8 }
}

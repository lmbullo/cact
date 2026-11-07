export const legacyName = 'old path'

// deprecated: the old path is kept for one more release
export function findLegacy() {
  return legacyName
}

export function currentName() {
  return 'new path'
}

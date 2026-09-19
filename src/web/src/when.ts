// How a moment is written, in one place.
//
// Epoch seconds from the server, the browser's own locale and zone for display.
// A run started three minutes ago says so; one from last week gets a date, since
// "6 days ago" is harder to act on than the day it happened.

export function clockOf(epochSeconds: number): string {
  if (!epochSeconds) return ''
  return new Date(epochSeconds * 1000).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit',
  })
}

export function dayOf(epochSeconds: number): string {
  if (!epochSeconds) return ''
  const d = new Date(epochSeconds * 1000)
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  const yesterday = new Date(today.getTime() - 86400000).toDateString() === d.toDateString()
  if (sameDay) return 'Today'
  if (yesterday) return 'Yesterday'
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

export function whenOf(epochSeconds: number): string {
  if (!epochSeconds) return '—'
  return `${dayOf(epochSeconds)} ${clockOf(epochSeconds)}`
}

/** "4m ago", "2h ago". Relative is the right unit for something recent. */
export function agoOf(epochSeconds: number): string {
  if (!epochSeconds) return ''
  const secs = Math.max(0, Date.now() / 1000 - epochSeconds)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

/** How long it took, from a duration in seconds. */
export function tookOf(seconds: number): string {
  if (!seconds && seconds !== 0) return ''
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${String(s).padStart(2, '0')}s`
}

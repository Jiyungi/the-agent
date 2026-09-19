// Sign in with GitHub, through Supabase, with no SDK.
//
// Supabase's OAuth endpoints are plain HTTP redirects, so the whole flow is a
// link out and a hash fragment on the way back. Adding @supabase/supabase-js
// for this would pull a dependency to do string parsing.
//
// The hash on return looks like:
//
//   #access_token=ey...&provider_token=gho_...&expires_in=3600&...
//
// `provider_token` is the GitHub token and this is the only moment it exists.
// Supabase does not store it and there is no endpoint that will hand it over
// later, so it goes straight to our server before anything else happens. Miss
// it and you get a signed-in user whose first clone 404s.
//
// The hash is cleared immediately afterwards: a URL carrying a live GitHub
// token should not survive into history, a bookmark, or a screenshot taken
// during a demo.

export interface Me {
  signed_in: boolean
  login?: string
  avatar_url?: string
  can_touch_repos?: boolean
}

const KEY = 'accel.session'

interface Stored { access_token: string }

function save(s: Stored) {
  try { localStorage.setItem(KEY, JSON.stringify(s)) } catch { /* private mode */ }
}

export function token(): string {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Stored).access_token || '' : ''
  } catch { return '' }
}

export function signOut() {
  try { localStorage.removeItem(KEY) } catch { /* nothing to do */ }
  location.reload()
}

/** Send the browser to GitHub, asking for the `repo` scope on the way. */
export async function signIn() {
  const cfg = await fetch('/api/config').then((r) => r.json())
  if (!cfg.configured) {
    alert('Sign-in is not configured on this server.')
    return
  }
  const back = encodeURIComponent(location.origin + location.pathname)
  // `repo` is what lets Accel clone a private repository and open the pull
  // request. Without it sign-in succeeds and every run fails at the clone.
  location.href =
    `${cfg.supabase_url}/auth/v1/authorize?provider=github` +
    `&scopes=${encodeURIComponent('repo')}&redirect_to=${back}`
}

/**
 * Handle a return from GitHub, if this load is one. Resolves to the signed-in
 * user, the previously stored session, or a signed-out marker.
 */
export async function resume(): Promise<Me> {
  const hash = new URLSearchParams(location.hash.replace(/^#/, ''))
  const access = hash.get('access_token') || ''
  const provider = hash.get('provider_token') || ''

  if (access) {
    // Clear the hash before the network call: if the POST is slow, the token
    // should not be sitting in the address bar meanwhile.
    history.replaceState(null, '', location.pathname + location.search)
    save({ access_token: access })
    try {
      const r = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: access, provider_token: provider }),
      })
      if (r.ok) {
        const u = await r.json()
        return { signed_in: true, ...u }
      }
    } catch { /* fall through to /api/me */ }
  }

  const t = token()
  if (!t) return { signed_in: false }
  try {
    return await fetch('/api/me', {
      headers: { Authorization: `Bearer ${t}` },
    }).then((r) => r.json())
  } catch {
    return { signed_in: false }
  }
}

/** Authorization header for API calls, or nothing when signed out. */
export function authHeaders(): Record<string, string> {
  const t = token()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

import { useCallback, useEffect, useState } from 'react'
import { api, type Job } from './api'
import { resume, signOut, type Me } from './auth'
import { SignIn } from './components/SignIn'
import { Sidebar, type Tab } from './components/Sidebar'
import { Home } from './components/Home'
import { Runs } from './components/Runs'
import { FindingsTab } from './components/FindingsTab'

type Row = {
  id: string; url: string; repo: string; status: string
  phase: string; findings: number; pr_url: string
  pages: number; watch_url: string; started: number; finished: number
}

export default function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [job, setJob] = useState<Job | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  // `null` is "still asking", which is different from "signed out". Rendering
  // the sign-in screen while the answer is in flight makes every reload flash
  // the gate at someone who is already signed in.
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => { resume().then(setMe).catch(() => setMe({ signed_in: false })) }, [])

  const refresh = useCallback(() => {
    api.jobs().then((d) => setRows(d.jobs)).catch(() => { /* keeps what it has */ })
  }, [])

  useEffect(() => { refresh() }, [refresh, tab])

  const open = useCallback(async (id: string) => {
    try {
      setJob(await api.job(id))
      setTab('runs')
    } catch { /* the list stays put */ }
  }, [])

  function started(j: Job) {
    setJob(j)
    setTab('runs')
    refresh()
  }

  const findingCount = rows.reduce((n, r) => n + r.findings, 0)
  const prCount = rows.filter((r) => r.pr_url).length

  if (me === null) return <div className="app-booting" role="status">Loading ACCEL…</div>
  if (!me.signed_in) return <SignIn />

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">Skip to content</a>

      <Sidebar
        tab={tab}
        setTab={(t) => { if (t !== 'runs') setJob(null); setTab(t) }}
        target={job ? job.repo.replace(/^https?:\/\/(www\.)?github\.com\//, '') : ''}
        runCount={rows.length}
        findingCount={findingCount}
        login={me.login ?? ''}
        avatarUrl={me.avatar_url ?? ''}
        onSignOut={signOut}
      />

      <div className="app-main" id="main">
        {tab === 'home' && (
          <Home onStarted={started} runs={rows.length}
                findings={findingCount} prs={prCount}
                lastRun={rows.length ? Math.max(...rows.map((r) => r.started)) : 0} />
        )}
        {tab === 'runs' && (
          <Runs job={job} rows={rows} onOpen={open} onUpdate={setJob}
                onClose={() => setJob(null)} />
        )}
        {tab === 'findings' && <FindingsTab onOpen={open} />}
      </div>
    </div>
  )
}

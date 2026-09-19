import { useCallback, useEffect, useState } from 'react'
import { api, type Job } from './api'
import { Sidebar, type Tab } from './components/Sidebar'
import { Home } from './components/Home'
import { Runs } from './components/Runs'
import { FindingsTab } from './components/FindingsTab'
import { LoopTab } from './components/LoopTab'

type Row = {
  id: string; url: string; repo: string; status: string
  phase: string; findings: number; pr_url: string
  pages: number; watch_url: string; started: number; finished: number
}

export default function App() {
  const [tab, setTab] = useState<Tab>('home')
  const [job, setJob] = useState<Job | null>(null)
  const [rows, setRows] = useState<Row[]>([])

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

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">Skip to content</a>

      <Sidebar
        tab={tab}
        setTab={(t) => { if (t !== 'runs') setJob(null); setTab(t) }}
        target={job ? job.repo.replace(/^https?:\/\/(www\.)?github\.com\//, '') : ''}
        runCount={rows.length}
        findingCount={findingCount}
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
        {tab === 'loop' && <LoopTab onOpen={open} />}
      </div>
    </div>
  )
}

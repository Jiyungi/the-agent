// The Runs tab: the ledger, and one run opened in full.
//
// A run opened here is one complete pass -- drive the page, patch the source,
// re-audit, open the PR -- and the panels below follow that order.

import { useEffect, useRef } from 'react'
import { api, type Job } from '../api'
import { Icon } from '../Icon'
import { LiveView } from './LiveView'
import { Findings } from './Findings'
import { whenOf, agoOf, tookOf } from '../when'
import { WatchPanel } from './WatchPanel'

const STEPS = [
  { id: 'clone', label: 'Read the code' },
  { id: 'audit', label: 'Drive the page' },
  { id: 'fix', label: 'Write the fix' },
  { id: 'reaudit', label: 'Confirm it held' },
  { id: 'pr', label: 'Open the PR' },
]

function stepClass(job: Job, id: string): string {
  const order = STEPS.map((s) => s.id)
  const at = order.indexOf(job.phase)
  const me = order.indexOf(id)
  if (job.status === 'failed' && me === at) return 'failed'
  if (me < at) return 'done'
  if (me === at) return job.status === 'done' ? 'done' : 'active'
  return ''
}

function mmss(at: number, from: number): string {
  const s = Math.max(0, at - from)
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

export function statusClass(status: string): string {
  return status === 'done' ? 'status-done'
    : status === 'failed' ? 'status-blocked'
    : status === 'queued' ? 'status-queued'
    : 'status-live'
}

interface Props {
  job: Job | null
  rows: { id: string; url: string; repo: string; status: string
          phase: string; findings: number; pr_url: string
          pages: number; watch_url: string; started: number; finished: number }[]
  onOpen: (id: string) => void
  onUpdate: (job: Job) => void
  onClose: () => void
}

export function Runs({ job, rows, onOpen, onUpdate, onClose }: Props) {
  const logRef = useRef<HTMLDivElement>(null)
  const live = !!job && (job.status === 'queued' || job.status === 'running')

  useEffect(() => {
    if (!job || !live) return
    let stop = false
    const id = window.setInterval(async () => {
      try {
        const next = await api.job(job.id)
        if (!stop) onUpdate(next)
      } catch { /* the panel keeps what it has */ }
    }, 1500)
    return () => { stop = true; window.clearInterval(id) }
  }, [job, live, onUpdate])

  useEffect(() => {
    const el = logRef.current
    if (!el || !live) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 60) el.scrollTop = el.scrollHeight
  }, [job?.events.length, live])

  if (!job) {
    return (
      <div className="dashboard-page">
        <header className="page-header">
          <div>
            <span className="eyebrow">Ledger</span>
            <h1>Runs</h1>
            <p>One run is one complete pass: drive the page, patch the source,
               re-audit, open the pull request.</p>
          </div>
        </header>

        {rows.length === 0 ? (
          <div className="quiet-panel">
            <strong>No runs yet</strong>
            Start one from Home and it appears here while it works.
          </div>
        ) : (
          <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
            <table className="grid">
              <thead>
                <tr>
                  <th>Page</th><th>Repository</th><th>Started</th><th>State</th>
                  <th className="num">Findings</th><th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td className="mono" style={{ maxWidth: 300, overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.url}</td>
                    <td className="mono">
                      {r.repo.replace(/^https?:\/\/(www\.)?github\.com\//, '')}
                    </td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      {whenOf(r.started)}
                      <span className="muted" style={{ display: 'block',
                            fontSize: 'var(--text-caption)' }}>{agoOf(r.started)}</span>
                    </td>
                    <td>
                      <span className={`status-label ${statusClass(r.status)}`}>
                        <i aria-hidden="true" />
                        {r.status === 'running' ? r.phase : r.status}
                      </span>
                    </td>
                    <td className="num">{r.findings}</td>
                    <td className="num">
                      <button className="button secondary" onClick={() => onOpen(r.id)}>
                        Open <Icon name="chevron-right" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    )
  }

  const failed = job.findings.filter((f) => f.status === 'failed')
  const undecided = job.findings.filter((f) => f.status === 'not_evaluated')
  const closed = job.patches.reduce((n, p) => n + p.closed, 0)
  const created = job.patches.reduce((n, p) => n + p.created, 0)

  return (
    <div className="dashboard-page">
      <header className="page-header">
        <div>
          <button className="button secondary" onClick={onClose} style={{ marginBottom: 14 }}>
            <Icon name="back" /> All runs
          </button>
          <span className="eyebrow">
            {job.repo.replace(/^https?:\/\/(www\.)?github\.com\//, '')}
            {job.source ? ` · ${job.source}` : ''}
          </span>
          <h1 style={{ fontSize: 28, overflowWrap: 'anywhere' }}>{job.url}</h1>
        </div>
        <div className="page-action-row">
          <span className={`status-label ${statusClass(job.status)}`}>
            <i aria-hidden="true" />
            {job.status === 'running' ? job.phase : job.status}
          </span>
          <span className="status-label status-neutral">
            <i aria-hidden="true" />{whenOf(job.started)}
          </span>
          <span className="status-label status-queued">
            <i aria-hidden="true" />{tookOf(job.elapsed)}
          </span>
        </div>
      </header>

      <dl className="run-summary-bar">
        <div><dt>Findings</dt><dd>{failed.length}</dd></div>
        <div><dt>Unresolved</dt><dd>{undecided.length}</dd></div>
        <div><dt>Closed</dt><dd>{closed}</dd></div>
        <div><dt>New issues</dt><dd>{created}</dd></div>
        <div><dt>Pages</dt><dd>{job.pages.length || 1}</dd></div>
        <div><dt>States</dt><dd>{Object.keys(job.recordings).length}</dd></div>
        <div><dt>Checks</dt><dd>{job.findings.length}</dd></div>
        <div><dt>Started</dt><dd>{whenOf(job.started)}</dd></div>
        <div><dt>Took</dt><dd>{tookOf(job.elapsed)}</dd></div>
      </dl>

      {job.pages.length > 1 && (
        <section className="section">
          <div className="section-heading">
            <div><span className="eyebrow">Coverage</span><h2>Pages audited</h2></div>
            <span className="section-count">{job.pages.length}</span>
          </div>
          <table className="grid">
            <thead><tr><th>Page</th><th>Source</th><th className="num">Findings</th></tr></thead>
            <tbody>
              {job.pages.map((pg, i) => (
                <tr key={i}>
                  <td className="mono" style={{ maxWidth: 380, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pg.url}</td>
                  <td className="mono">{pg.source || '—'}</td>
                  <td className="num">{pg.findings}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <div className="rail" style={{ marginTop: 20 }}>
        {STEPS.map((s, i) => (
          <div key={s.id} className={`rail-step ${stepClass(job, s.id)}`}>
            <span className="n">{String(i + 1).padStart(2, '0')}</span>
            <span className="t">{s.label}</span>
          </div>
        ))}
      </div>

      {job.status === 'failed' && (
        <p className="status-label status-blocked" role="alert"><i aria-hidden="true" />{job.error}</p>
      )}
      {job.pr_url && (
        <p className="status-label status-done" style={{ marginTop: 8 }}>
          <i aria-hidden="true" />
          <a href={job.pr_url} target="_blank" rel="noreferrer">
            {job.pr_url.replace(/^https?:\/\/(www\.)?github\.com\//, '')}
          </a>
        </p>
      )}
      {job.pr_blocked && !job.pr_url && (
        <p className="status-label status-attention" style={{ marginTop: 8 }}>
          <i aria-hidden="true" />{job.pr_blocked}
        </p>
      )}

      <WatchPanel job={job} live={live} />

      <section className="section">
        <div className="section-heading">
          <div><span className="eyebrow">Progress</span><h2>Log</h2></div>
          {job.trace_url && (
            <a href={job.trace_url} target="_blank" rel="noreferrer">
              Trace <Icon name="external" />
            </a>
          )}
        </div>
        <div className="log" ref={logRef} aria-live="polite">
          {job.events.length === 0 && <div className="log-row"><span className="msg">Starting</span></div>}
          {job.events.map((e, i) => (
            <div className={`log-row ${e.level}`} key={i}>
              <span className="at">{mmss(e.at, job.started)}</span>
              <span className="ph">{e.phase}</span>
              <span className="msg">{e.message}</span>
            </div>
          ))}
        </div>
      </section>

      {Object.keys(job.recordings).length > 0 && (
        <section className="section">
          <div className="section-heading">
            <div><span className="eyebrow">Evidence</span><h2>The page as ACCEL drove it</h2></div>
          </div>
          <LiveView recordings={job.recordings} />
        </section>
      )}

      {job.findings.length > 0 && (
        <section className="section">
          <div className="section-heading">
            <div><span className="eyebrow">Verdicts</span><h2>Findings</h2></div>
            <span className="section-count">{failed.length}</span>
          </div>
          <Findings findings={job.findings} axe={job.axe} />
        </section>
      )}

      {(job.patches.length > 0 || job.diff) && (
        <section className="section">
          <div className="section-heading">
            <div><span className="eyebrow">Applied all or nothing</span><h2>Changes</h2></div>
          </div>
          {job.patches.length > 0 && (
            <table className="grid" style={{ marginBottom: 20 }}>
              <thead>
                <tr>
                  <th>Criterion</th><th>Component</th><th>Result</th>
                  <th className="num">Tries</th><th className="num">Closed</th>
                </tr>
              </thead>
              <tbody>
                {job.patches.map((p, i) => (
                  <tr key={i} className={p.status === 'closed' ? '' : 'dim'}>
                    <td className="mono">{p.criterion}</td>
                    <td className="mono">{p.component}</td>
                    <td>
                      <span className={`status-label ${p.status === 'closed'
                        ? 'status-done' : 'status-attention'}`}>
                        <i aria-hidden="true" />{p.status.replace(/_/g, ' ')}
                      </span>
                    </td>
                    <td className="num">{p.patch_attempts}</td>
                    <td className="num">{p.closed}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {job.diff && (
            <div className="diff">
              {job.diff.split('\n').map((line, i) => {
                const cls = line.startsWith('+') ? 'add'
                  : line.startsWith('-') ? 'del'
                  : line.startsWith('@@') ? 'meta'
                  : line.startsWith('diff ') || line.startsWith('index ') ? 'hdr' : ''
                return <div className={cls} key={i}>{line || ' '}</div>
              })}
            </div>
          )}
        </section>
      )}
    </div>
  )
}

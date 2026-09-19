// The home page: where a run starts. Two fields, nothing else required.

import { useState } from 'react'
import { api, type Job } from '../api'
import { Icon } from '../Icon'
import { whenOf } from '../when'

interface Props {
  onStarted: (job: Job) => void
  runs: number
  findings: number
  prs: number
  lastRun: number
}

export function Home({ onStarted, runs, findings, prs, lastRun }: Props) {
  const [url, setUrl] = useState('')
  const [repo, setRepo] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const repoLooksWrong = repo.trim() !== '' && !/github\.com\/[^/]+\/[^/]+/.test(repo)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      onStarted(await api.startAudit(url.trim(), repo.trim(), [], true, true))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The run could not be started.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="dashboard-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Home</span>
          <h1>Audit a page, then fix it</h1>
        </div>
      </header>

      <form className="card" onSubmit={submit}>
        <div className="audit-form">
          <div className="field">
            <label htmlFor="url">Live page</label>
            <input
              id="url" className="input mono" type="url" required
              autoComplete="url" spellCheck={false}
              placeholder="https://yoursite.com/checkout"
              value={url} onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="repo">Repository</label>
            <input
              id="repo" className="input mono" type="text" required spellCheck={false}
              placeholder="https://github.com/owner/repo"
              value={repo} onChange={(e) => setRepo(e.target.value)}
              aria-invalid={repoLooksWrong}
              aria-describedby={repoLooksWrong ? 'repo-err' : undefined}
            />
            {repoLooksWrong && (
              <span className="muted" id="repo-err" style={{ fontSize: 'var(--text-caption)' }}>
                Expected github.com/owner/repo
              </span>
            )}
          </div>
        </div>

        <div className="field-row">
          <button className="button primary large" type="submit" disabled={busy || repoLooksWrong}>
            <Icon name="play" />
            {busy ? 'Starting' : 'Start a run'}
          </button>
        </div>

        {error && (
          <p className="status-label status-blocked" style={{ marginTop: 16 }} role="alert">
            <i aria-hidden="true" />{error}
          </p>
        )}
      </form>

      <section className="section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Ledger</span>
            <h2>What this machine has done</h2>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: 12 }}>
          <div className="cell"><small>Runs</small><strong className="mono">{runs}</strong></div>
          <div className="cell"><small>Findings</small><strong className="mono">{findings}</strong></div>
          <div className="cell"><small>Pull requests</small><strong className="mono">{prs}</strong></div>
          <div className="cell">
            <small>Last run</small>
            <strong className="mono">{lastRun ? whenOf(lastRun) : '—'}</strong>
          </div>
        </div>
      </section>
    </div>
  )
}

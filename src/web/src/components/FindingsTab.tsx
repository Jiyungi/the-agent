// Findings across every run started here, grouped by criterion.
//
// Nothing on this page is compiled in. It loads each run the server reports and
// counts what those runs actually found, so it fills as you use the product and
// is empty before you do.

import { useEffect, useState } from 'react'
import { api, type Finding, type Job } from '../api'
import { Icon } from '../Icon'

const NAMES: Record<string, string> = {
  '2.1.1': 'Keyboard',
  '2.1.2': 'No Keyboard Trap',
  '2.4.3': 'Focus Order',
  '2.4.7': 'Focus Visible',
  '2.4.11': 'Focus Not Obscured',
}

interface Row {
  criterion: string
  failed: number
  passed: number
  unresolved: number
  examined: number
  targets: { url: string; target: string; summary: string }[]
}

export function FindingsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let stop = false
    api.jobs()
      .then(async (d) => {
        const full = await Promise.all(
          d.jobs.map((j) => api.job(j.id).catch(() => null)))
        if (!stop) setJobs(full.filter(Boolean) as Job[])
      })
      .catch(() => { /* stays empty */ })
      .finally(() => { if (!stop) setLoading(false) })
    return () => { stop = true }
  }, [])

  if (loading) return <div className="dashboard-page"><p className="muted">Reading runs…</p></div>

  const byCriterion = new Map<string, Row>()
  let axeRan = 0
  let axeWcag = 0

  for (const job of jobs) {
    if (job.axe?.ran) {
      axeRan += 1
      axeWcag += (job.axe.violations ?? [])
        .filter((v) => (v.tags ?? []).some((t) => t.startsWith('wcag'))).length
    }
    for (const f of job.findings as Finding[]) {
      const row = byCriterion.get(f.criterion) ?? {
        criterion: f.criterion, failed: 0, passed: 0, unresolved: 0,
        examined: 0, targets: [],
      }
      row.examined += f.census?.examined ?? 0
      if (f.status === 'failed') {
        row.failed += 1
        for (const t of f.targets ?? []) {
          row.targets.push({ url: job.url, target: t, summary: f.summary })
        }
      } else if (f.status === 'not_evaluated') row.unresolved += 1
      else row.passed += 1
      byCriterion.set(f.criterion, row)
    }
  }

  const rows = [...byCriterion.values()].sort((a, b) => a.criterion.localeCompare(b.criterion))
  const totalFailed = rows.reduce((n, r) => n + r.failed, 0)
  const totalExamined = rows.reduce((n, r) => n + r.examined, 0)

  return (
    <div className="dashboard-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Across every run</span>
          <h1>Findings</h1>
        </div>
        <div className="page-action-row">
          <span className="status-label status-neutral"><i aria-hidden="true" />
            {jobs.length} run{jobs.length === 1 ? '' : 's'}</span>
        </div>
      </header>

      {rows.length === 0 ? (
        <div className="quiet-panel">
          <strong>Nothing found yet</strong>
          Findings appear here once a run has driven a page.
        </div>
      ) : (
        <>
          <dl className="run-summary-bar">
            <div><dt>Failed</dt><dd>{totalFailed}</dd></div>
            <div><dt>Criteria seen</dt><dd>{rows.length}</dd></div>
            <div><dt>Elements examined</dt><dd>{totalExamined}</dd></div>
            <div><dt>axe ran on</dt><dd>{axeRan} page{axeRan === 1 ? '' : 's'}</dd></div>
            <div><dt>axe WCAG hits</dt><dd>{axeWcag}</dd></div>
          </dl>

          <section className="section">
            <div className="section-heading">
              <div><span className="eyebrow">By criterion</span><h2>What failed, and where</h2></div>
            </div>
            <table className="grid">
              <thead>
                <tr>
                  <th>Criterion</th><th className="num">Failed</th>
                  <th className="num">Passed</th><th className="num">Unresolved</th>
                  <th className="num">Examined</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.criterion}>
                    <td>
                      <span className="mono" style={{ fontWeight: 700 }}>{r.criterion}</span>
                      <span className="muted" style={{ display: 'block', fontSize: 'var(--text-caption)' }}>
                        {NAMES[r.criterion] ?? ''}
                      </span>
                    </td>
                    <td className="num">
                      {r.failed > 0
                        ? <span className="status-label status-blocked"><i aria-hidden="true" />{r.failed}</span>
                        : '—'}
                    </td>
                    <td className="num">{r.passed || '—'}</td>
                    <td className="num">{r.unresolved || '—'}</td>
                    <td className="num">{r.examined || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {rows.some((r) => r.targets.length) && (
            <section className="section">
              <div className="section-heading">
                <div><span className="eyebrow">Elements</span><h2>Every element named</h2></div>
              </div>
              <div className="card" style={{ display: 'grid', gap: 14 }}>
                {rows.filter((r) => r.targets.length).map((r) => (
                  <div key={r.criterion}>
                    <strong className="mono">{r.criterion}</strong>
                    <ul className="tag-list" style={{ marginTop: 6 }}>
                      {r.targets.slice(0, 12).map((t, i) => (
                        <li key={i} title={`${t.summary} — ${t.url}`}>{t.target}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="section">
            <div className="section-heading">
              <div><span className="eyebrow">Runs</span><h2>Where these came from</h2></div>
            </div>
            <table className="grid">
              <thead><tr><th>Page</th><th className="num">Findings</th><th /></tr></thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="mono" style={{ maxWidth: 420, overflow: 'hidden',
                          textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{j.url}</td>
                    <td className="num">{j.findings.filter((f) => f.status === 'failed').length}</td>
                    <td className="num">
                      <button className="button secondary" onClick={() => onOpen(j.id)}>
                        Open <Icon name="chevron-right" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </div>
  )
}
